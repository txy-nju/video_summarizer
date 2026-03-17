import os
from typing import IO

# 引入抽象层和具体策略
from core.extraction.base import VideoSource
from core.extraction.sources import UrlVideoSource, LocalFileVideoSource

# 【核心改造】引入新的工作流接口，不再需要旧的 analysis 和 generation
from core.workflow import summarize_video
from utils.file_utils import clear_temp_folder

class VideoSummaryService:
    def __init__(self, api_key: str):
        # 从环境变量中获取 base_url
        self.base_url = os.getenv("OPENAI_BASE_URL")
        self.api_key = api_key
        
        # 【核心改造】不再需要初始化 analyzer 和 generator
        # 核心组件的初始化现在由 langgraph 内部管理

    def _process_source(self, source: VideoSource, user_prompt: str = "") -> str:
        """
        统一的内部处理逻辑：
        1. 使用 VideoSource 获取内容 (Transcript + Frames)
        2. 调用新的工作流进行分析和总结
        """
        # 在开始前清理临时文件夹
        clear_temp_folder()

        try:
            # 1. 获取内容 (VideoSource 现在是完全独立的)
            transcript, frames = source.process()

            # 2. 【核心改造】调用新的、符合架构的 summarize_video 函数
            print("Invoking AI workflow...")
            
            # 如果用户未输入提示，则使用架构默认的综合总结提示
            if not user_prompt or not user_prompt.strip():
                user_prompt = "请结合画面与语音，给出一个全面、客观的高质量视频总结。"
                
            summary = summarize_video(
                transcript=transcript,
                keyframes=frames,
                user_prompt=user_prompt
            )
            print("Workflow complete.")
            
            return summary
        finally:
            # 在结束后清理，确保不留垃圾文件
            clear_temp_folder()

    def process_video_from_url(self, url: str, user_prompt: str = "") -> str:
        """
        针对 URL 的完整流程。
        """
        # 创建 Source 实例时传入必要的配置
        source = UrlVideoSource(url, api_key=self.api_key, base_url=self.base_url)
        return self._process_source(source, user_prompt=user_prompt)

    def process_uploaded_video(self, uploaded_file: IO[bytes], original_filename: str, user_prompt: str = "") -> str:
        """
        针对上传文件的完整流程。
        """
        # 创建 Source 实例时传入必要的配置
        source = LocalFileVideoSource(
            uploaded_file, 
            original_filename, 
            api_key=self.api_key, 
            base_url=self.base_url
        )
        return self._process_source(source, user_prompt=user_prompt)