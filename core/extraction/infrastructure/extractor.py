import cv2
import base64
import math
from pathlib import Path
from typing import Optional
from moviepy.video.io.VideoFileClip import VideoFileClip
from config.settings import (
    TEMP_AUDIO_DIR,
    TEMP_FRAMES_DIR,
    MAX_IMAGE_SIZE,
    ENABLE_KEYFRAME_FILE_REFERENCE,
    KEYFRAME_REFERENCE_INCLUDE_INLINE_IMAGE,
    KEYFRAME_IMAGE_EXTENSION,
    CHUNK_SPLIT_DURATION_SHORT_SECONDS,
    CHUNK_SPLIT_DURATION_MEDIUM_SECONDS,
    SCENE_CHANGE_SEVERE_SHORT_THRESHOLD,
    SCENE_CHANGE_SEVERE_MEDIUM_THRESHOLD,
    SCENE_CHANGE_SEVERE_LONG_THRESHOLD,
)

class MediaExtractor:
    def __init__(self, audio_dir: Path = TEMP_AUDIO_DIR):
        self.audio_dir = audio_dir
        self.audio_dir.mkdir(parents=True, exist_ok=True)

    def extract_audio(self, video_path: Path) -> Optional[Path]:
        """
        从视频中提取音频并返回音频文件路径。若视频无音轨，则优雅返回 None。
        """
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")
            
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        audio_path = self.audio_dir / f"{video_path.stem}.mp3"
        
        try:
            with VideoFileClip(str(video_path)) as video:
                if video.audio is None:
                    print(f"Warning: No audio track found in {video_path}")
                    return None
                video.audio.write_audiofile(str(audio_path), codec='mp3', logger=None)
            return audio_path
        except Exception as e:
            print(f"Error extracting audio: {e}")
            raise IOError(f"Failed to extract audio from {video_path}: {e}")

    @staticmethod
    def _calc_histogram(image, compare_max_size: int = 320):
        """计算图像的归一化灰度直方图（用于场景比较，默认先降采样以提速）。"""
        h, w = image.shape[:2]
        if max(h, w) > compare_max_size:
            if h > w:
                new_h = compare_max_size
                new_w = int(w * (compare_max_size / h))
            else:
                new_w = compare_max_size
                new_h = int(h * (compare_max_size / w))
            image = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
        cv2.normalize(hist, hist, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
        return hist

    @staticmethod
    def _resolve_probe_fps(duration_seconds: float, requested_probe_fps: int) -> int:
        """
        根据视频时长动态调整探测频率：长视频降低探测频率以显著提速。
        优先使用显式传入值（>0），否则走自动策略。
        """
        if requested_probe_fps and requested_probe_fps > 0:
            return max(1, int(requested_probe_fps))

        # 自动档位：<10min 用 5fps；10~30min 用 3fps；>=30min 用 1fps
        if duration_seconds >= 1800:
            return 1
        if duration_seconds >= 600:
            return 3
        return 5

    @staticmethod
    def _clamp_scene_change_score(score: float) -> float:
        if math.isnan(score):
            return 0.0
        return max(0.0, min(1.0, float(score)))

    @staticmethod
    def _resolve_severe_threshold(duration_seconds: float) -> float:
        if duration_seconds < CHUNK_SPLIT_DURATION_SHORT_SECONDS:
            return SCENE_CHANGE_SEVERE_SHORT_THRESHOLD
        if duration_seconds < CHUNK_SPLIT_DURATION_MEDIUM_SECONDS:
            return SCENE_CHANGE_SEVERE_MEDIUM_THRESHOLD
        return SCENE_CHANGE_SEVERE_LONG_THRESHOLD

    @staticmethod
    def _classify_scene_change_level(scene_change_score: float, severe_threshold: float) -> str:
        if scene_change_score >= severe_threshold:
            return "severe"
        if scene_change_score >= severe_threshold * 0.5:
            return "moderate"
        return "none"

    def extract_frames(
        self,
        video_path: Path,
        interval: int = 2,
        max_interval: int = 60,
        threshold: float = 0.90,
        probe_fps: int = 0,
    ) -> list[dict]:
        """
        基于场景检测的关键帧提取。
        通过自适应 probe_fps、grab/retrieve 采样和最大间隔兜底，在速度与信息覆盖之间取平衡。
        
        :param video_path: 视频路径
        :param interval: 两次抽帧的最小时间间隔（秒）
        :param max_interval: 强制抽帧的最大时间间隔（秒），用于防断层兜底
        :param threshold: 直方图相关性阈值，低于该值则认为场景发生突变
        :param probe_fps: 探测频率；传入 0 时按视频时长自动选择
        :return: 关键帧字典列表，元素包含 time，且根据配置包含 image 或 frame_file
        """
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        vidcap = cv2.VideoCapture(str(video_path))
        if not vidcap.isOpened():
            raise IOError(f"Cannot open video file: {video_path}")
            
        fps = vidcap.get(cv2.CAP_PROP_FPS)
        # 兼容 fps 为 0, NaN 或 None 的异常情况
        if not fps or math.isnan(fps) or fps <= 0:
            fps = 30.0 

        frames = []
        frame_count = 0

        total_frames = vidcap.get(cv2.CAP_PROP_FRAME_COUNT)
        if not total_frames or math.isnan(total_frames) or total_frames <= 0:
            total_frames = 0.0
        duration_seconds = (total_frames / fps) if total_frames > 0 else 0.0

        # 探测步长：长视频降低 probe_fps；仅在探测帧 retrieve，非探测帧仅 grab 以降低解码开销
        safe_probe_fps = self._resolve_probe_fps(duration_seconds, probe_fps)
        probe_stride = max(1, int(fps / safe_probe_fps))
        severe_threshold = self._resolve_severe_threshold(duration_seconds)
        
        last_extracted_time = -1000.0  # 确保第0秒强制抽取
        last_hist = None
        
        while True:
            # grab: 仅推进解码器，不把图像拷贝到 Python 层，减少热路径开销
            if not vidcap.grab():
                break

            # 跳过非探测帧：只 grab 不 retrieve，进一步减少跨语言内存拷贝和解码压力
            if probe_stride > 1 and frame_count % probe_stride != 0:
                frame_count += 1
                continue

            success, image = vidcap.retrieve()
            if not success or image is None:
                # 视频流中断、损坏或自然结束，安全退出
                break
                
            current_time = frame_count / fps
            should_extract = False
            scene_change_score = 0.0
            scene_change_level = "none"
            
            # 条件 1：首帧强制抽取作为基准 Anchor
            current_hist = None

            if last_hist is None:
                should_extract = True
            else:
                time_since_last = current_time - last_extracted_time
                current_hist = self._calc_histogram(image)
                correlation = cv2.compareHist(last_hist, current_hist, cv2.HISTCMP_CORREL)
                scene_change_score = self._clamp_scene_change_score(1.0 - float(correlation))
                scene_change_level = self._classify_scene_change_level(scene_change_score, severe_threshold)
                
                # 条件 2：达到最大间隔时强制抽帧，避免长时间没有视觉证据
                if time_since_last >= max_interval:
                    should_extract = True
                # 条件 3：达到最小间隔后，仅在场景明显变化时抽帧
                elif time_since_last >= interval:
                    if correlation < threshold:
                        should_extract = True

            if should_extract:
                # 缩放图片以控制 Base64 长度
                h, w, _ = image.shape
                if max(h, w) > MAX_IMAGE_SIZE:
                    if h > w:
                        new_h = MAX_IMAGE_SIZE
                        new_w = int(w * (MAX_IMAGE_SIZE / h))
                    else:
                        new_w = MAX_IMAGE_SIZE
                        new_h = int(h * (MAX_IMAGE_SIZE / w))
                    image = cv2.resize(image, (new_w, new_h))

                # 更新基准与时间
                # 复用已计算的 current_hist，避免重复计算
                if current_hist is None:
                    current_hist = self._calc_histogram(image)
                last_hist = current_hist
                last_extracted_time = current_time

                ext = KEYFRAME_IMAGE_EXTENSION if KEYFRAME_IMAGE_EXTENSION in {"jpg", "jpeg", "png"} else "jpg"
                ok, buffer = cv2.imencode(f".{ext}", image)
                if not ok:
                    frame_count += 1
                    continue

                jpg_as_text = base64.b64encode(buffer).decode('utf-8')
                
                # 计算时间戳并格式化为 HH:MM:SS 或 MM:SS
                total_seconds = int(round(current_time))
                hours, remainder = divmod(total_seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                if hours > 0:
                    time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                else:
                    time_str = f"{minutes:02d}:{seconds:02d}"
                
                frame_payload = {"time": time_str}
                frame_payload["scene_change_score"] = round(scene_change_score, 4)
                frame_payload["scene_change_level"] = scene_change_level

                if ENABLE_KEYFRAME_FILE_REFERENCE:
                    TEMP_FRAMES_DIR.mkdir(parents=True, exist_ok=True)
                    frame_filename = f"{video_path.stem}_{total_seconds:08d}_{len(frames):04d}.{ext}"
                    frame_file_path = TEMP_FRAMES_DIR / frame_filename
                    try:
                        frame_file_path.write_bytes(buffer.tobytes())
                        frame_payload["frame_file"] = frame_filename
                    except Exception:
                        # 文件落盘失败时回退为内联图片，保证主流程不中断。
                        frame_payload["image"] = jpg_as_text

                    if KEYFRAME_REFERENCE_INCLUDE_INLINE_IMAGE:
                        frame_payload["image"] = jpg_as_text
                else:
                    frame_payload["image"] = jpg_as_text

                frames.append(frame_payload)
                
            frame_count += 1

        vidcap.release()
        return frames