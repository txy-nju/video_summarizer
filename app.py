import streamlit as st
import tempfile
from services.workflow_service import VideoSummaryService

def main():
    """
    Main entry point for the Streamlit Video Summarizer application.

    Sets up the page layout, sidebar inputs for API keys and video URLs,
    and handles the orchestration of video processing and summary display.
    """
    st.set_page_config(layout="wide")
    st.title("多模态智能视频总结 (Video Summarizer)")

    # Sidebar for inputs
    with st.sidebar:
        st.header("⚙️ Settings (配置)")
        api_key = st.text_input("OpenAI API Key", type="password")
        
        # 选择视频来源
        source_type = st.radio("🎬 Video Source (视频来源)", ("YouTube URL", "Local Upload"))
        
        video_url = None
        uploaded_file = None

        if source_type == "YouTube URL":
            video_url = st.text_input("Video URL")
        else:
            uploaded_file = st.file_uploader("Upload a video", type=["mp4", "mov", "avi", "mkv"])

        st.markdown("---")
        st.header("🎯 Summary Requirements (总结偏好)")
        user_prompt = st.text_area(
            "您希望 AI 侧重总结什么内容？ (What would you like the AI to focus on?)", 
            placeholder="例如：请侧重于分析视频中产品演示的具体操作步骤和图表数据...",
            help="留空则会进行默认的全面综合总结。(Leave blank for a general comprehensive summary.)"
        )

        process_button = st.button("🚀 Generate Summary (开始总结)")

    # Main content area
    col1, col2 = st.columns(2)

    # 左侧显示视频
    with col1:
        st.header("📺 Video")
        if source_type == "YouTube URL" and video_url:
            st.video(video_url)
        elif source_type == "Local Upload" and uploaded_file:
            # Streamlit可以直接显示上传的文件对象
            st.video(uploaded_file)
        else:
            st.info("Please provide a video source to begin.")

    # 右侧显示摘要
    with col2:
        st.header("📝 Summary")
        
        if process_button:
            if not api_key:
                st.warning("Please enter your OpenAI API Key first.")
            elif source_type == "YouTube URL" and not video_url:
                st.warning("Please enter a valid YouTube URL.")
            elif source_type == "Local Upload" and not uploaded_file:
                st.warning("Please upload a video file.")
            else:
                # 开始处理
                with st.spinner("Processing video and invoking AI Workflow... This may take a while."):
                    try:
                        service = VideoSummaryService(api_key)
                        summary = ""
                        
                        if source_type == "YouTube URL":
                            # 处理 URL
                            summary = service.process_video_from_url(video_url, user_prompt=user_prompt)
                        else:
                            # 处理上传的文件
                            # uploaded_file 是一个 BytesIO 对象，包含 name 属性
                            summary = service.process_uploaded_video(uploaded_file, uploaded_file.name, user_prompt=user_prompt)
                        
                        st.markdown(summary)
                    except Exception as e:
                        st.error(f"An error occurred: {e}")
        else:
             st.markdown("Summary will appear here after processing...")

if __name__ == "__main__":
    main()