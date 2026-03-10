
import streamlit as st
from services.workflow_service import VideoSummaryService

def main():
    """
    Main entry point for the Streamlit Video Summarizer application.

    Sets up the page layout, sidebar inputs for API keys and video URLs,
    and handles the orchestration of video processing and summary display.
    """
    st.set_page_config(layout="wide")
    st.title("Video Summarizer")

    # Sidebar for inputs
    with st.sidebar:
        st.header("Settings")
        api_key = st.text_input("API Key", type="password")
        video_url = st.text_input("Video URL")
        process_button = st.button("Generate Summary")

    # Main content area
    col1, col2 = st.columns(2)

    with col1:
        st.header("Video")
        if video_url:
            st.video(video_url)

    with col2:
        st.header("Summary")
        if process_button and api_key and video_url:
            with st.spinner("Processing video..."):
                try:
                    service = VideoSummaryService(api_key)
                    summary = service.process_video(video_url)
                    st.markdown(summary)
                except Exception as e:
                    st.error(f"An error occurred: {e}")
        elif process_button and not api_key:
            st.warning("Please enter your API Key.")
        else:
            st.markdown("Summary will appear here...")

if __name__ == "__main__":
    main()
