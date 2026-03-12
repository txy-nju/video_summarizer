
# Video Summarizer (视频内容智能总结助手)

Video Summarizer 是一个基于 Python 的智能工具，旨在自动化从视频 URL 或本地文件中提取关键信息并生成结构化总结的过程。它结合了视频下载、多模态内容提取（音频转录 + 关键帧截取）以及大模型分析能力，为您提供高效的视频内容摘要服务。

## 🚀 核心功能

*   **视频获取**：
    *   **URL 下载**：支持从 YouTube 等主流平台下载视频（基于 `yt-dlp`），并具备绕过反爬虫机制的能力（支持 Cookies）。
    *   **本地上传**：支持用户直接上传本地视频文件（支持 mp4, mov, avi, mkv 等格式）进行处理。
*   **多模态提取**：
    *   **音频转录**：利用 OpenAI Whisper API 将视频语音精准转录为带时间戳的文本。
    *   **关键帧截取**：智能提取视频关键帧，辅助理解视觉内容。
*   **智能分析**：集成大模型（如 GPT-4o），对转录文本和关键帧进行深度分析，生成高质量的内容总结。
*   **报告生成**：(开发中) 将分析结果整理为 PDF 等格式的文档报告。
*   **可视化界面**：提供基于 Streamlit 的交互式 Web 界面，操作简单直观。

## 🛠️ 技术栈

*   **核心语言**: Python 3.8+
*   **Web 框架**: Streamlit
*   **视频处理**: `yt-dlp`, `moviepy`, `opencv-python`
*   **AI/ML**: `openai` (Whisper & GPT), `tenacity` (重试机制)
*   **工具库**: `python-dotenv` (环境配置)

## 📦 安装与配置

### 1. 克隆仓库

```bash
git clone https://github.com/txy-nju/audio_summarizer.git
cd audio_summarizer
```

### 2. 创建虚拟环境 (推荐)

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置环境变量

在项目根目录下创建一个 `.env` 文件，并填入您的 API 密钥和相关配置：

```ini
# .env 文件示例

# OpenAI API Key (必填)
OPENAI_API_KEY="sk-your_api_key_here"

# OpenAI Base URL (可选，如果您使用中转服务)
OPENAI_BASE_URL="https://your.proxy.server/v1"
```

### 5. 配置 YouTube Cookies (可选但推荐)

为了防止 YouTube 下载时的反爬虫限制（如 "Sign in to confirm you’re not a bot"），建议提供 `cookies.txt` 文件：

1.  在浏览器中安装 "Get cookies.txt LOCALLY" 插件。
2.  访问 YouTube 并登录您的账号。
3.  使用插件导出 Cookies，保存为 `cookies.txt`。
4.  将 `cookies.txt` 文件放置在项目根目录下。

## ▶️ 运行应用

启动 Streamlit 界面：

```bash
streamlit run app.py
```

访问浏览器中的 `http://localhost:8501` 即可使用。

## 📂 项目结构

```
video_summarizer/
├── app.py                  # Streamlit 应用入口
├── config/                 # 配置管理
│   └── settings.py
├── core/                   # 核心业务逻辑
│   ├── extraction/         # 提取层
│   │   ├── base.py         # 抽象基类 (VideoSource)
│   │   ├── sources/        # 策略实现 (UrlVideoSource, LocalFileVideoSource)
│   │   └── infrastructure/ # 基础设施实现 (Extractor, Transcriber, Downloader)
│   ├── analysis/           # 分析层 (大模型总结)
│   └── generation/         # 生成层 (报告生成)
├── services/               # 业务流程编排
│   └── workflow_service.py
├── utils/                  # 通用工具
├── tests/                  # 测试用例
├── .env                    # 环境变量 (需手动创建)
├── cookies.txt             # YouTube Cookies (需手动创建)
└── requirements.txt        # 项目依赖
```

## 📝 开发计划

- [x] 视频下载与反爬虫处理
- [x] 支持本地视频上传
- [x] 音频转录与关键帧提取
- [x] 大模型内容分析接口
- [ ] PDF 报告生成模块
- [ ] 更多视频源支持
- [ ] 用户账户系统 (可选)

## 🤝 贡献

欢迎提交 Issue 或 Pull Request 来改进这个项目！

## 📄 许可证

MIT License
