
import os
from pathlib import Path
from typing import Tuple, IO

from core.extraction.video.downloader import VideoDownloader
from core.extraction.video.local_video_handler import LocalVideoHandler
from core.extraction.extractor import MediaExtractor
from core.extraction.transcriber import AudioTranscriber
from core.analysis.analyzer import ContentAnalyzer
from core.generation.report_generator import ReportGenerator
from config.settings import DEFAULT_FRAME_INTERVAL
from utils.file_utils import clear_temp_folder

class VideoSummaryService:
    def __init__(self, api_key: str):
        # 从环境变量中获取 base_url
        self.base_url = os.getenv("OPENAI_BASE_URL")
        
        self.downloader = VideoDownloader()
        self.local_handler = LocalVideoHandler()
        self.extractor = MediaExtractor()
        self.transcriber = AudioTranscriber(api_key, base_url=self.base_url)
        self.analyzer = ContentAnalyzer(api_key, base_url=self.base_url)
        self.generator = ReportGenerator()

    def _extract_and_transcribe(self, video_path: Path) -> Tuple[str, list[str]]:
        """
        [通用方法] 从本地视频文件中提取文本和关键帧。
        
        Args:
            video_path (Path): 本地视频文件的路径。

        Returns:
            Tuple[str, list[str]]: 一个包含转录文本和Base64关键帧列表的元组。
        """
        # 2. 提取音频和关键帧
        print(f"Processing video at {video_path}...")
        print("Extracting audio...")
        audio_path = self.extractor.extract_audio(video_path)
        print(f"Audio extracted to {audio_path}")

        print("Extracting frames...")
        frames = self.extractor.extract_frames(video_path, interval=DEFAULT_FRAME_INTERVAL)
        print(f"Extracted {len(frames)} frames.")

        # 3. 转录音频
        print("Transcribing audio...")
        transcript = self.transcriber.transcribe(audio_path)
        print(f"Transcription complete. Length: {len(transcript)}")
        
        return transcript, frames

    def extract_content_from_url(self, url: str) -> Tuple[str, list[str]]:
        """
        [仅供兼容] 从视频URL中提取文本和关键帧。
        建议使用 process_video_from_url 替代。
        """
        # 1. 下载视频
        print(f"Downloading video from {url}...")
        video_path = self.downloader.download(url)
        print(f"Video downloaded to {video_path}")

        return self._extract_and_transcribe(video_path)

    def process_video_from_url(self, url: str) -> str:
        """
        针对 URL 的完整流程：下载 -> 提取 -> 分析。
        """
        # 在开始前清理临时文件夹
        clear_temp_folder()

        try:
            # 1. 下载视频
            print(f"Downloading video from {url}...")
            video_path = self.downloader.download(url)
            print(f"Video downloaded to {video_path}")

            # 2. 通用处理 (提取 + 转录)
            transcript, frames = self._extract_and_transcribe(video_path)

            # 3. 分析
            print("Analyzing content...")
            summary = self.analyzer.analyze(transcript, frames)
            print("Analysis complete.")
            
            return summary
        finally:
            # 在结束后清理，确保不留垃圾文件
            # 使用 finally 确保即使出错也能清理
            clear_temp_folder()

    def process_uploaded_video(self, uploaded_file: IO[bytes], original_filename: str) -> str:
        """
        针对上传文件的完整流程：保存 -> 提取 -> 分析。
        """
        # 在开始前清理临时文件夹
        clear_temp_folder()

        try:
            # 1. 保存上传的文件
            print(f"Saving uploaded file {original_filename}...")
            video_path = self.local_handler.save_uploaded_file(uploaded_file, original_filename)
            print(f"File saved to {video_path}")

            # 2. 通用处理 (提取 + 转录)
            transcript, frames = self._extract_and_transcribe(video_path)

            # 3. 分析
            print("Analyzing content...")
            summary = self.analyzer.analyze(transcript, frames)
            print("Analysis complete.")
            
            return summary
        finally:
            # 在结束后清理
            clear_temp_folder()
