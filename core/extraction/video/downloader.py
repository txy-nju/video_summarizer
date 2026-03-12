
import yt_dlp
from pathlib import Path
from config.settings import TEMP_VIDEO_DIR, BASE_DIR

class VideoDownloader:
    def __init__(self, output_dir: Path = TEMP_VIDEO_DIR):
        self.output_dir = output_dir
        # 确保目录存在
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def download(self, url: str) -> Path:
        """
        下载视频并返回本地文件路径。
        使用 cookies.txt 文件并优化了格式选择以提高成功率。
        """
        output_template = str(self.output_dir / '%(id)s.%(ext)s')
        cookiefile_path = BASE_DIR / 'cookies.txt'
        
        ydl_opts = {
            # 【修改】优化格式选择，让 yt-dlp 自动合并最佳音视频
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': output_template,
            'quiet': True,
            'no_warnings': True,
        }

        if cookiefile_path.exists():
            print("Found cookies.txt, using it for authentication.")
            ydl_opts['cookiefile'] = str(cookiefile_path)
        else:
            print("Warning: cookies.txt not found in project root. Download may fail due to anti-crawling.")

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                return Path(filename)
        except yt_dlp.utils.DownloadError as e:
            if "confirm you’re not a bot" in str(e):
                raise Exception(
                    "\n[下载错误] YouTube触发了反爬虫机制，下载失败。\n"
                    "解决方案：\n"
                    "1. 请确保您已按照说明，在项目根目录放置了有效的 'cookies.txt' 文件。\n"
                    "2. 如果文件已存在但仍然失败，请重新从浏览器导出最新的 cookies.txt。\n"
                ) from e
            if "Requested format is not available" in str(e):
                 raise Exception(
                    f"\n[下载错误] 视频 {url} 没有找到请求的特定格式。\n"
                    "这通常是因为视频的编码方式特殊。代码已自动调整为更通用的格式，请重试。\n"
                ) from e
            # 如果是其他下载错误，则重新抛出
            raise e
