
import os
from pathlib import Path
from typing import Tuple

from core.extraction.downloader import VideoDownloader
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
        self.extractor = MediaExtractor()
        self.transcriber = AudioTranscriber(api_key, base_url=self.base_url)
        self.analyzer = ContentAnalyzer(api_key, base_url=self.base_url)
        self.generator = ReportGenerator()

    def extract_content_from_url(self, url: str) -> Tuple[str, list[str]]:
        """
        第一部分：从视频URL中提取文本和关键帧。
        
        Args:
            url (str): 视频的URL。

        Returns:
            Tuple[str, list[str]]: 一个包含转录文本和Base64关键帧列表的元组。
        """
        # 1. 下载视频
        print(f"Downloading video from {url}...")
        video_path = self.downloader.download(url)
        print(f"Video downloaded to {video_path}")

        # 2. 提取音频和关键帧
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

    def process_video(self, url: str) -> str:
        """
        执行完整的视频总结流程（提取 -> 分析）。
        """
        # 在开始前清理临时文件夹
        clear_temp_folder()

        # 第一部分：提取
        transcript, frames = self.extract_content_from_url(url)

        # 第二部分：分析
        print("Analyzing content...")
        summary = self.analyzer.analyze(transcript, frames)
        print("Analysis complete.")

        # 第三部分：生成报告 (当前未激活)
        # report_path = self.generator.generate_pdf(summary, frames)
        # print(f"Report generated at {report_path}")

        # 在结束后再次清理，确保不留垃圾文件
        clear_temp_folder()

        return summary
