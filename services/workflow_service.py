import os
import time
import random
from pathlib import Path
from typing import IO, Callable, Optional, Dict, Any

# 引入抽象层和具体策略
from core.extraction.base import VideoSource
from core.extraction.sources import UrlVideoSource, LocalFileVideoSource

# 【核心改造】引入新的工作流接口，不再需要旧的 analysis 和 generation
from core.workflow import (
    analyze_video,
    finalize_summary as _finalize_summary_api,
    answer_question_at_timestamp,
)
from core.workflow.session import ensure_thread_id
from utils.file_utils import clear_temp_folder
from utils.logger import setup_logger, log_metric_event
from config.settings import ENABLE_METRICS_LOGGING, METRICS_SAMPLE_RATE

class VideoSummaryService:
    def __init__(self, api_key: str, base_url: Optional[str] = None):
        self.api_key = api_key
        self.last_thread_id = ""
        # 优先使用前端传入的 base_url，如果为空则尝试读取环境变量，最后回退到官方默认地址
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        
        # 【环境变量依赖注入】
        # 将前端传入的 API Key 和 Base URL 挂载到系统环境变量中。
        # 这是为了确保完全解耦的 LangGraph 内部节点（如 text_analyzer_node、vision_analyzer_node）
        # 可以在底层安全且纯粹地调用 os.getenv() 来初始化 OpenAI() 客户端，而无需破坏状态机签名。
        if self.api_key:
            os.environ["OPENAI_API_KEY"] = self.api_key
            
        if self.base_url:
            os.environ["OPENAI_BASE_URL"] = self.base_url
            
        # 核心组件的初始化现在由 langgraph 内部管理
        self.metrics_logger = setup_logger("video_summarizer.metrics")

    def _analyze_source(
        self,
        source: VideoSource,
        user_prompt: str = "",
        status_callback: Optional[Callable[[str], None]] = None,
        thread_id: str = "",
    ) -> Dict[str, Any]:
        """
        统一的内部处理逻辑：
        1. 使用 VideoSource 获取内容 (Transcript + Frames)
        2. 调用工作流第一阶段，返回待审批包
        """
        metrics_enabled = ENABLE_METRICS_LOGGING and random.random() <= METRICS_SAMPLE_RATE
        process_started_at = time.perf_counter()
        try:
            # 1. 获取内容 (VideoSource 现在是完全独立的)
            # [前端透传] 将来自 UI 的回调函数注入到底层的抽取生命周期中
            extraction_started_at = time.perf_counter()
            transcript, frames = source.process(status_callback=status_callback)
            extraction_ms = int((time.perf_counter() - extraction_started_at) * 1000)

            if metrics_enabled:
                log_metric_event(
                    self.metrics_logger,
                    "service_extraction_finished",
                    transcript_chars=len(transcript),
                    keyframes_count=len(frames),
                    extraction_duration_ms=extraction_ms,
                )

            # 2. 调用第一阶段入口 analyze_video，执行分片分析并返回待审批包
            if status_callback:
                status_callback("🔗 音频底模与关键帧视觉流预处理成功。多模态数据已向 AI 并发流水线集结。")
                
            print("Invoking AI workflow...")
            
            # 如果用户未输入提示，则使用架构默认的综合总结提示
            if not user_prompt or not user_prompt.strip():
                user_prompt = "请结合画面与语音，给出一个全面、客观的高质量视频总结。"
                
            # [前端透传] 将 UI 回调函数挂载进 LangGraph 执行总线上
            resolved_thread_id = ensure_thread_id(thread_id)
            self.last_thread_id = resolved_thread_id

            workflow_started_at = time.perf_counter()
            review_package = analyze_video(
                transcript=transcript,
                keyframes=frames,
                user_prompt=user_prompt,
                status_callback=status_callback,
                thread_id=resolved_thread_id,
            )
            workflow_ms = int((time.perf_counter() - workflow_started_at) * 1000)

            if isinstance(review_package, dict):
                self.last_thread_id = str(review_package.get("thread_id", resolved_thread_id)) or resolved_thread_id

            if metrics_enabled:
                log_metric_event(
                    self.metrics_logger,
                    "service_workflow_finished",
                    thread_id=resolved_thread_id,
                    workflow_duration_ms=workflow_ms,
                    summary_chars=0,
                )
            
            if status_callback:
                status_callback("🎉 第一阶段完成：聚合稿已生成并进入人类审批关口。")
                
            print("Workflow complete.")
            
            return review_package
        finally:
            if metrics_enabled:
                total_ms = int((time.perf_counter() - process_started_at) * 1000)
                log_metric_event(
                    self.metrics_logger,
                    "service_process_finished",
                    thread_id=self.last_thread_id,
                    total_duration_ms=total_ms,
                )

            # 在结束后清理，确保不留垃圾文件
            clear_temp_folder()

    def analyze_url_video(
        self,
        url: str,
        user_prompt: str = "",
        status_callback: Optional[Callable[[str], None]] = None,
        thread_id: str = "",
    ) -> Dict[str, Any]:
        """
        第一阶段入口（URL 来源）：下载视频，执行分片分析，返回待审批包。
        """
        # [生命周期 Bugfix]：必须在实例化 Source (其底层处理器会在 init 时建文件夹) 之前，进行清空操作。
        # 否则 clear_temp_folder 会把刚建好的 temp/videos 删掉，导致 FileNotFoundError。
        clear_temp_folder()
        
        # 创建 Source 实例时传入必要的配置
        source = UrlVideoSource(url, api_key=self.api_key, base_url=self.base_url)
        return self._analyze_source(
            source,
            user_prompt=user_prompt,
            status_callback=status_callback,
            thread_id=thread_id,
        )

    def analyze_uploaded_video(
        self,
        uploaded_file: IO[bytes],
        original_filename: str,
        user_prompt: str = "",
        status_callback: Optional[Callable[[str], None]] = None,
        thread_id: str = "",
    ) -> Dict[str, Any]:
        """
        第一阶段入口（上传文件来源）：接收上传文件，执行分片分析，返回待审批包。
        """
        # 服务端格式校验（第一道防线，防止客户端绕过前端限制）
        from backend.schemas.video_format import ALLOWED_VIDEO_EXTENSIONS, validate_video_extension

        if not validate_video_extension(original_filename):
            allowed = ", ".join(sorted(ext.lstrip(".") for ext in ALLOWED_VIDEO_EXTENSIONS))
            raise ValueError(
                f"不支持的文件格式：{Path(original_filename).suffix or '(无扩展名)'}。"
                f"支持的格式：{allowed}"
            )

        # [生命周期 Bugfix]：必须在实例化 Source 之前进行环境清理。
        clear_temp_folder()
        
        # 创建 Source 实例时传入必要的配置
        source = LocalFileVideoSource(
            uploaded_file, 
            original_filename, 
            api_key=self.api_key, 
            base_url=self.base_url
        )
        return self._analyze_source(
            source,
            user_prompt=user_prompt,
            status_callback=status_callback,
            thread_id=thread_id,
        )

    def finalize_summary(
        self,
        thread_id: str,
        edited_aggregated_chunk_insights: str = "",
        human_guidance: str = "",
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        第二阶段：提交人类审批意见并完成最终总结。
        """
        resolved_thread_id = ensure_thread_id(thread_id or self.last_thread_id)
        self.last_thread_id = resolved_thread_id
        return _finalize_summary_api(
            thread_id=resolved_thread_id,
            edited_aggregated_chunk_insights=edited_aggregated_chunk_insights,
            human_guidance=human_guidance,
            status_callback=status_callback,
        )

    def ask_at_timestamp(
        self,
        timestamp: str,
        question: str,
        thread_id: str = "",
        window_seconds: int = 20,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        阶段2能力：基于历史 checkpoint 的时间旅行追问。
        """
        resolved_thread_id = ensure_thread_id(thread_id or self.last_thread_id)
        self.last_thread_id = resolved_thread_id

        return answer_question_at_timestamp(
            thread_id=resolved_thread_id,
            timestamp=timestamp,
            question=question,
            window_seconds=window_seconds,
            status_callback=status_callback,
        )