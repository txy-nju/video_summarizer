
import cv2
import base64
from pathlib import Path
from moviepy.video.io.VideoFileClip import VideoFileClip
from config.settings import TEMP_AUDIO_DIR, MAX_IMAGE_SIZE

class MediaExtractor:
    def __init__(self, audio_dir: Path = TEMP_AUDIO_DIR):
        self.audio_dir = audio_dir
        self.audio_dir.mkdir(parents=True, exist_ok=True)

    def extract_audio(self, video_path: Path) -> Path:
        """
        从视频中提取音频并返回音频文件路径
        """
        audio_path = self.audio_dir / f"{video_path.stem}.mp3"
        with VideoFileClip(str(video_path)) as video:
            video.audio.write_audiofile(str(audio_path), codec='mp3', logger=None)
        return audio_path

    def extract_frames(self, video_path: Path, interval: int) -> list[str]:
        """
        从视频中提取关键帧，并返回Base64编码的图片列表
        """
        vidcap = cv2.VideoCapture(str(video_path))
        if not vidcap.isOpened():
            raise IOError(f"Cannot open video file: {video_path}")
            
        fps = vidcap.get(cv2.CAP_PROP_FPS)
        if fps == 0:
            # 如果无法获取FPS，可以设置一个默认值或抛出错误
            # 这里我们选择一个通用值，并记录一个警告（如果日志系统集成）
            fps = 30 

        frames = []
        
        frame_count = 0
        success = True
        while success:
            success, image = vidcap.read()
            # 根据帧数和帧率计算是否到达抽帧时间点
            if success and frame_count % int(fps * interval) == 0:
                # 缩放图片
                h, w, _ = image.shape
                if max(h, w) > MAX_IMAGE_SIZE:
                    if h > w:
                        new_h = MAX_IMAGE_SIZE
                        new_w = int(w * (MAX_IMAGE_SIZE / h))
                    else:
                        new_w = MAX_IMAGE_SIZE
                        new_h = int(h * (MAX_IMAGE_SIZE / w))
                    image = cv2.resize(image, (new_w, new_h))

                # 编码为Base64
                _, buffer = cv2.imencode('.jpg', image)
                jpg_as_text = base64.b64encode(buffer).decode('utf-8')
                frames.append(jpg_as_text)
            frame_count += 1
        
        vidcap.release()
        return frames
