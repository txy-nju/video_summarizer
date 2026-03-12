
from pathlib import Path
import shutil
from typing import IO
from config.settings import TEMP_VIDEO_DIR

class LocalVideoHandler:
    def __init__(self, output_dir: Path = TEMP_VIDEO_DIR):
        """
        初始化本地视频处理器。

        Args:
            output_dir (Path): 保存上传视频的目标目录。
        """
        self.output_dir = output_dir
        # 确保目录存在
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save_uploaded_file(self, uploaded_file: IO[bytes], original_filename: str) -> Path:
        """
        将上传的文件流保存到本地，并返回其路径。

        为了避免文件名冲突和安全问题，我们不直接使用原始文件名，
        而是将其作为保存路径的一部分。

        Args:
            uploaded_file (IO[bytes]): 上传的文件对象 (如 Streamlit 的 UploadedFile)。
                                      它应该是一个二进制文件流。
            original_filename (str): 原始文件名，用于提取扩展名。

        Returns:
            Path: 保存后视频文件的绝对路径。
        """
        # 创建一个安全的目标文件路径
        # 这里我们简单地加上前缀，实际项目中可能需要更复杂的命名策略（如UUID）
        destination_path = self.output_dir / f"uploaded_{original_filename}"

        try:
            # 将上传的文件内容写入目标路径
            # 注意：uploaded_file 假定是一个类文件对象（file-like object），支持 read()
            with open(destination_path, "wb") as f:
                shutil.copyfileobj(uploaded_file, f)
            
            print(f"File '{original_filename}' saved to '{destination_path}'")
            return destination_path
        except IOError as e:
            print(f"Error saving file: {e}")
            raise
