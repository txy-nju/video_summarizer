
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量
# 这会查找与此文件同级的 .env 文件，或者向上查找
# 我们将 .env 文件放在项目根目录
env_path = Path(__file__).resolve().parent.parent / '.env'
load_dotenv(dotenv_path=env_path)  # 从 .env 文件中加载环境变量（如 API 密钥）

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent  # 获取项目根目录的绝对路径

# 临时文件目录
TEMP_DIR = BASE_DIR / "temp"
TEMP_VIDEO_DIR = TEMP_DIR / "videos"
TEMP_AUDIO_DIR = TEMP_DIR / "audios"
TEMP_FRAMES_DIR = TEMP_DIR / "frames"

# 确保目录存在
for dir_path in [TEMP_VIDEO_DIR, TEMP_AUDIO_DIR, TEMP_FRAMES_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

# 默认配置
DEFAULT_FRAME_INTERVAL = 2  # 默认每2秒抽一帧
MAX_IMAGE_SIZE = 768        # 图片长边限制
