from abc import ABC, abstractmethod
from typing import Tuple, List, Dict, Callable, Optional
from pathlib import Path

from config.settings import DEFAULT_FRAME_INTERVAL
from core.extraction.infrastructure.extractor import MediaExtractor
from core.extraction.infrastructure.transcriber import AudioTranscriber

class VideoSource(ABC):
    """
    视频源抽象基类。
    封装了从视频源获取、提取、转录的完整流程。
    """
    def __init__(self, api_key: str, base_url: str = None):
        # api_key 参数保留以兼容旧调用方；AudioTranscriber 内部已统一走工厂路由。
        self.extractor = MediaExtractor()
        self.transcriber = AudioTranscriber(api_key=api_key, base_url=base_url)

    @abstractmethod
    def acquire_video(self, status_callback: Optional[Callable[[str], None]] = None) -> Path:
        """
        获取视频文件并返回其本地路径。
        具体子类需实现此方法（如从 URL 下载或保存上传文件）。
        
        :param status_callback: 用于向前端抛出进度状态的可选回调函数
        Returns:
            Path: 本地视频文件的绝对路径。
        """
        pass

    def process(self, status_callback: Optional[Callable[[str], None]] = None) -> Tuple[str, List[Dict]]:
        """
        模板方法：执行标准的视频处理流程。
        1. 获取视频 (acquire_video)
        2. 提取音频和关键帧 (self.extractor)
        3. 转录音频 (self.transcriber)
            
        :param status_callback: 用于向前端抛出进度状态的可选回调函数
        Returns:
            Tuple[str, List[Dict]]: (转录文本, 关键帧列表)
        """
        
        def _notify(msg: str):
            if status_callback:
                status_callback(msg)
            print(msg)
            
        # 1. 获取视频路径
        _notify("📥 正在获取并保存视频文件...")
        video_path = self.acquire_video(status_callback)
        _notify(f"✅ 视频已就绪: {video_path.name}")

        # 2. 提取内容
        _notify("🎵 正在从视频流中分离音频轨...")
        audio_path = self.extractor.extract_audio(video_path)
        if audio_path:
            _notify("✅ 音频提取成功。")
        else:
            _notify("⚠️ 视频无音轨，跳过音频提取。")

        _notify("🎞️ 正在提取关键帧（场景检测 + 自适应探测频率）...")
        frames = self.extractor.extract_frames(video_path, interval=DEFAULT_FRAME_INTERVAL)
        _notify(f"✅ 成功抽取 {len(frames)} 张关键帧画面。")

        # 3. 生成转录
        transcript = ""
        if audio_path:
            _notify("🎙️ 正在调用 Whisper 模型进行高精度语音转录 (这可能需要一些时间)...")
            transcript = self.transcriber.transcribe(audio_path)
            _notify(f"✅ 语音转录完成，共提取 {len(transcript)} 个字符。")
        else:
            _notify("ℹ️ 无音频，跳过语音转录。")

        return transcript, frames