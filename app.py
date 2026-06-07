import os
import json
import streamlit as st
from dotenv import load_dotenv
from services.workflow_service import VideoSummaryService

# 在应用启动时尝试加载项目根目录的 .env 文件
load_dotenv()


def _init_session_state() -> None:
    defaults = {
        "current_summary": "",
        "active_thread_id": "",
        "restored_thread_id": "",
        "time_travel_answer": "",
        "pending_review": {},
        "editable_aggregated_insights": "",
        "human_guidance": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

def main():
    """
    Main entry point for the Streamlit Video Summarizer application.

    Sets up the page layout, sidebar inputs for API keys and video URLs,
    and handles the orchestration of video processing and summary display.
    """
    _init_session_state()

    st.set_page_config(layout="wide")
    st.title("多模态智能视频总结 (Video Summarizer)")

    # Sidebar for inputs
    with st.sidebar:
        st.header("⚙️ Settings (配置)")
        
        # [前端 UX 优化] 自动读取环境变量作为输入框默认值，免除每次重启手填的烦恼
        default_api_key = os.getenv("OPENAI_API_KEY", "")
        default_base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        
        api_key = st.text_input("OpenAI API Key", value=default_api_key, type="password")
        base_url = st.text_input("OpenAI Base URL", value=default_base_url, help="如果您使用的是兼容 OpenAI 格式的中转 API，请在此修改地址。")
        
        # 选择视频来源
        source_type = st.radio("🎬 Video Source (视频来源)", ("YouTube URL", "Local Upload"))
        
        video_url = None
        uploaded_file = None

        if source_type == "YouTube URL":
            video_url = st.text_input("Video URL")
        else:
            from backend.schemas.video_format import ALLOWED_VIDEO_EXTENSIONS
            _allowed_types = sorted(ext.lstrip(".") for ext in ALLOWED_VIDEO_EXTENSIONS)
            uploaded_file = st.file_uploader("Upload a video", type=_allowed_types)

        st.markdown("---")
        st.header("🎯 Summary Requirements (总结偏好)"
)
        user_prompt = st.text_area(
            "您希望 AI 侧重总结什么内容？ (What would you like the AI to focus on?)", 
            placeholder="例如：请侧重于分析视频中产品演示的具体操作步骤和图表数据...",
            help="留空则会进行默认的全面综合总结。(Leave blank for a general comprehensive summary.)"
        )

        st.markdown("---")
        st.header("🧵 Session (会话)"
)
        restored_thread_id = st.text_input(
            "已有 Thread ID（可选）",
            value=st.session_state.get("restored_thread_id", st.session_state.get("active_thread_id", "")),
            help="可粘贴历史 thread_id 以继续追问；留空则在首次总结时自动生成。",
        )
        if restored_thread_id != st.session_state.get("restored_thread_id", ""):
            st.session_state["restored_thread_id"] = restored_thread_id
        if st.button("绑定为当前会话"):
            st.session_state["active_thread_id"] = restored_thread_id.strip()
            st.success("当前会话 thread_id 已更新。")

        process_button = st.button("🚀 Generate Review Draft (生成待审批稿)")

    # Main content area
    col1, col2 = st.columns(2)

    # 左侧显示视频
    with col1:
        st.header("📺 Video"
)
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

        active_thread_id = st.session_state.get("active_thread_id", "")
        if active_thread_id:
            st.caption(f"当前会话 Thread ID: {active_thread_id}")
        
        if process_button:
            if not api_key:
                st.warning("Please enter your OpenAI API Key first.")
            elif source_type == "YouTube URL" and not video_url:
                st.warning("Please enter a valid YouTube URL.")
            elif source_type == "Local Upload" and not uploaded_file:
                st.warning("Please upload a video file.")
            else:
                review_package = {}
                
                # [状态回传体验跃升] 使用 st.status 作为后台运行日志的容器
                with st.status("🔄 正在唤醒深度多模态解析引擎，请坐和放宽...", expanded=True) as status_container:
                    progress_header = st.empty()
                    audio_progress_text = st.empty()
                    audio_progress_bar = st.progress(0)
                    vision_progress_text = st.empty()
                    vision_progress_bar = st.progress(0)
                    synthesis_progress_text = st.empty()
                    synthesis_progress_bar = st.progress(0)
                    overall_progress_text = st.empty()
                    overall_progress_bar = st.progress(0)

                    progress_header.info("分片进度面板：等待任务开始...")
                    audio_progress_text.write("音频分片：0/0")
                    vision_progress_text.write("视觉分片：0/0")
                    synthesis_progress_text.write("融合分片：0/0")
                    overall_progress_text.write("总体进度：0/0 (0%)")
                    
                    # 声明一个闭包函数，它会被注入到庞大业务底座的最深处
                    def update_status_ui(msg: str):
                        if isinstance(msg, str) and msg.startswith("[[PROGRESS]]"):
                            payload_raw = msg[len("[[PROGRESS]]"):]
                            try:
                                payload = json.loads(payload_raw)
                            except Exception:
                                return

                            if not isinstance(payload, dict):
                                return

                            if str(payload.get("type", "")).strip() != "chunk_progress":
                                return

                            total_chunks = int(payload.get("total_chunks", 0) or 0)
                            audio_done = int(payload.get("audio_done", 0) or 0)
                            vision_done = int(payload.get("vision_done", 0) or 0)
                            synthesis_done = int(payload.get("synthesis_done", 0) or 0)
                            overall_done = int(payload.get("overall_done", 0) or 0)
                            overall_total = int(payload.get("overall_total", 0) or 0)
                            overall_percent = int(payload.get("overall_percent", 0) or 0)

                            audio_percent = int((audio_done / total_chunks) * 100) if total_chunks > 0 else 0
                            vision_percent = int((vision_done / total_chunks) * 100) if total_chunks > 0 else 0
                            synthesis_percent = int((synthesis_done / total_chunks) * 100) if total_chunks > 0 else 0

                            progress_header.info("分片进度面板：Send API 实时 fan-out/fan-in")
                            audio_progress_text.write(f"音频分片：{audio_done}/{total_chunks} ({audio_percent}%)")
                            audio_progress_bar.progress(max(0, min(100, audio_percent)))
                            vision_progress_text.write(f"视觉分片：{vision_done}/{total_chunks} ({vision_percent}%)")
                            vision_progress_bar.progress(max(0, min(100, vision_percent)))
                            synthesis_progress_text.write(
                                f"融合分片：{synthesis_done}/{total_chunks} ({synthesis_percent}%)"
                            )
                            synthesis_progress_bar.progress(max(0, min(100, synthesis_percent)))
                            overall_progress_text.write(
                                f"总体进度：{overall_done}/{overall_total} ({max(0, min(100, overall_percent))}%)"
                            )
                            overall_progress_bar.progress(max(0, min(100, overall_percent)))
                            return

                        status_container.update(label=msg)
                        # 在状态面板内部如极客流水线一般打出所有曾执行过的子任务日志
                        st.write(msg)

                    try:
                        service = VideoSummaryService(api_key=api_key, base_url=base_url)
                        active_thread_id = st.session_state.get("active_thread_id", "") or st.session_state.get("restored_thread_id", "")
                        
                        if source_type == "YouTube URL":
                            # 处理 URL
                            review_package = service.analyze_url_video(
                                video_url, 
                                user_prompt=user_prompt, 
                                status_callback=update_status_ui,
                                thread_id=active_thread_id,
                            )
                        else:
                            # 处理上传的文件
                            review_package = service.analyze_uploaded_video(
                                uploaded_file, 
                                uploaded_file.name, 
                                user_prompt=user_prompt, 
                                status_callback=update_status_ui,
                                thread_id=active_thread_id,
                            )

                        st.session_state["pending_review"] = review_package if isinstance(review_package, dict) else {}
                        st.session_state["editable_aggregated_insights"] = str(
                            st.session_state["pending_review"].get("editable_aggregated_chunk_insights", "")
                        )
                        st.session_state["human_guidance"] = str(
                            st.session_state["pending_review"].get("human_guidance", "")
                        )
                        st.session_state["active_thread_id"] = service.last_thread_id
                        st.session_state["restored_thread_id"] = service.last_thread_id
                        
                        status_container.update(
                            label="✅ 第一阶段完成：已生成待审批聚合稿，请在下方审批区确认后继续。", 
                            state="complete", 
                            expanded=False # 执行完毕后自动收起流水线日志，腾出屏幕空间
                        )
                        
                    except Exception as e:
                        status_container.update(label="❌ 系统异常，流水线熔断", state="error", expanded=True)
                        st.error(f"处理过程中发生严重异常: {e}")
                        
                pending = st.session_state.get("pending_review", {})
                if isinstance(pending, dict) and pending:
                    st.info("已到达人类审批步骤：你可以直接修改聚合稿，并补充额外指导，再生成最终总结。")
        else:
             cached_summary = st.session_state.get("current_summary", "")
             if cached_summary:
                 st.markdown(cached_summary)
             else:
                 st.markdown("Summary will appear here after processing...")

        st.markdown("---")
        st.header("🧑‍⚖️ Human Review (人工审批)"
)
        pending = st.session_state.get("pending_review", {})
        if isinstance(pending, dict) and pending:
            st.caption(f"审批会话 Thread ID: {pending.get('thread_id', '')}")
            raw_aggregated = str(pending.get("aggregated_chunk_insights", ""))
            if raw_aggregated:
                with st.expander("查看原始聚合稿（只读）", expanded=False):
                    st.markdown(raw_aggregated)

            edited_aggregated = st.text_area(
                "可编辑聚合稿（将作为第二阶段唯一证据输入）",
                value=st.session_state.get("editable_aggregated_insights", ""),
                height=260,
            )
            st.session_state["editable_aggregated_insights"] = edited_aggregated

            human_guidance = st.text_area(
                "额外 Human Guidance（可选）",
                value=st.session_state.get("human_guidance", ""),
                placeholder="例如：请先给执行摘要，再按时间线展开，重点写产品策略，不要写泛化结论。",
                height=120,
            )
            st.session_state["human_guidance"] = human_guidance

            finalize_button = st.button("✅ Approve And Generate Final Summary (审批并生成最终总结)")
            if finalize_button:
                thread_id_for_finalize = str(pending.get("thread_id", "")).strip() or st.session_state.get("active_thread_id", "")
                if not thread_id_for_finalize:
                    st.warning("缺少 thread_id，请先执行第一阶段。")
                else:
                    with st.status("🔄 正在执行第二阶段：人类审批后全篇总结与质量审查...", expanded=True) as finalize_status:

                        def update_finalize_status(msg: str):
                            finalize_status.update(label=msg)
                            st.write(msg)

                        try:
                            service = VideoSummaryService(api_key=api_key, base_url=base_url)
                            final_summary = service.finalize_summary(
                                thread_id=thread_id_for_finalize,
                                edited_aggregated_chunk_insights=edited_aggregated,
                                human_guidance=human_guidance,
                                status_callback=update_finalize_status,
                            )
                            st.session_state["current_summary"] = final_summary
                            st.session_state["active_thread_id"] = service.last_thread_id
                            st.session_state["restored_thread_id"] = service.last_thread_id
                            st.session_state["pending_review"] = {}

                            finalize_status.update(
                                label="✅ 第二阶段完成：最终总结已生成。",
                                state="complete",
                                expanded=False,
                            )
                            st.markdown(final_summary)
                            st.balloons()
                        except Exception as e:
                            finalize_status.update(label="❌ 第二阶段执行失败", state="error", expanded=True)
                            st.error(f"审批后生成失败: {e}")
        else:
            st.info("暂无待审批稿。请先点击 Generate Review Draft。")

        st.markdown("---")
        st.header("⏱️ Time Travel Q&A")
        active_thread_id = st.session_state.get("active_thread_id", "")
        if active_thread_id:
            st.caption(f"追问将使用当前会话 Thread ID: {active_thread_id}")
        else:
            st.info("请先完成一次视频总结，或在侧边栏绑定一个已有 thread_id。")

        time_travel_timestamp = st.text_input(
            "时间戳",
            value="00:10",
            help="支持 MM:SS 或 HH:MM:SS，例如 01:30 或 00:14:20。",
        )
        time_travel_window = st.slider(
            "证据窗口（秒）",
            min_value=5,
            max_value=60,
            value=20,
            step=5,
        )
        time_travel_question = st.text_area(
            "追问问题",
            placeholder="例如：请解释这个时间点画面中的架构图在表达什么？",
        )

        ask_button = st.button("🔎 Ask At Timestamp (时间旅行追问)"
)
        if ask_button:
            if not active_thread_id:
                st.warning("当前没有可用的 thread_id，请先生成总结或绑定历史会话。")
            elif not time_travel_question.strip():
                st.warning("请输入追问问题。")
            else:
                with st.status("🕒 正在回溯历史状态并抽取目标时间窗证据...", expanded=True) as travel_status:
                    def update_time_travel_status(msg: str):
                        travel_status.update(label=msg)
                        st.write(msg)

                    try:
                        service = VideoSummaryService(api_key=api_key, base_url=base_url)
                        answer = service.ask_at_timestamp(
                            timestamp=time_travel_timestamp,
                            question=time_travel_question,
                            thread_id=active_thread_id,
                            window_seconds=time_travel_window,
                            status_callback=update_time_travel_status,
                        )
                        st.session_state["time_travel_answer"] = answer
                        travel_status.update(
                            label="✅ 已完成时间旅行追问。",
                            state="complete",
                            expanded=False,
                        )
                    except Exception as e:
                        travel_status.update(label="❌ 时间旅行追问失败", state="error", expanded=True)
                        st.error(f"追问过程中发生异常: {e}")

        cached_answer = st.session_state.get("time_travel_answer", "")
        if cached_answer:
            st.subheader("追问结果")
            st.markdown(cached_answer)

if __name__ == "__main__":
    main()