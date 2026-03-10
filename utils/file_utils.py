
import os
import shutil
from config.settings import TEMP_DIR

def clear_temp_folder():
    """
    清空临时文件夹
    """
    if TEMP_DIR.exists():
        for item in TEMP_DIR.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                os.remove(item)
    print(f"Temporary folder '{TEMP_DIR}' cleared.")
