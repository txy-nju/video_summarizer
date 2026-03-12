
import unittest
import shutil
import os
import sys
from pathlib import Path
from io import BytesIO

# 将项目根目录添加到 sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..')))

from core.extraction.infrastructure.video.local_video_handler import LocalVideoHandler

class TestLocalVideoHandler(unittest.TestCase):

    def setUp(self):
        """在每个测试开始前，创建一个临时的测试目录"""
        self.test_dir = Path("./test_temp_uploads")
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.test_dir.mkdir(exist_ok=True)
        self.handler = LocalVideoHandler(output_dir=self.test_dir)

    def tearDown(self):
        """在每个测试结束后，清理临时目录"""
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_save_uploaded_file_success(self):
        """测试上传文件保存成功的情况"""
        # 模拟上传的文件内容
        file_content = b"fake video content"
        uploaded_file = BytesIO(file_content)
        original_filename = "test_video.mp4"
        
        # 执行保存
        saved_path = self.handler.save_uploaded_file(uploaded_file, original_filename)

        # 验证文件是否已创建
        self.assertTrue(saved_path.exists())
        
        # 验证文件名是否包含原始文件名（根据我们的实现逻辑）
        self.assertIn(original_filename, saved_path.name)
        
        # 验证文件内容是否一致
        with open(saved_path, "rb") as f:
            saved_content = f.read()
        self.assertEqual(saved_content, file_content)

    def test_save_uploaded_file_path_structure(self):
        """验证保存路径是否在预期的目录下"""
        file_content = b"content"
        uploaded_file = BytesIO(file_content)
        original_filename = "another_video.mov"
        
        saved_path = self.handler.save_uploaded_file(uploaded_file, original_filename)
        
        # 验证父目录是否是我们设置的 test_dir
        self.assertEqual(saved_path.parent.resolve(), self.test_dir.resolve())

if __name__ == '__main__':
    unittest.main()
