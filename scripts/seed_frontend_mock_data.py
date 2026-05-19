from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
import sys

from sqlalchemy import delete

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.auth.utils import hash_password
from backend.db.session import SessionLocal
from backend.models.database import (
    DeviceToken,
    GlobalChatSession,
    GlobalQARecord,
    KnowledgeBase,
    User,
    VideoQARecord,
    VideoResource,
    VideoSummaryTask,
    kb_video_relation_table,
)
from backend.models.enums import FrameExtractionStatus, TranscribeStatus, WorkflowState


def _reset_tables(session) -> None:
    session.execute(delete(DeviceToken))
    session.execute(delete(GlobalQARecord))
    session.execute(delete(GlobalChatSession))
    session.execute(delete(VideoQARecord))
    session.execute(delete(VideoSummaryTask))
    session.execute(delete(kb_video_relation_table))
    session.execute(delete(KnowledgeBase))
    session.execute(delete(VideoResource))
    session.execute(delete(User))


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed mock data for frontend API simulation")
    parser.add_argument("--reset", action="store_true", help="Clear existing data before seeding")
    args = parser.parse_args()

    now = datetime.now(UTC)

    ids = {
        "user_id": "11111111-1111-1111-1111-111111111111",
        "kbid": "22222222-2222-2222-2222-222222222222",
        "video_ready_id": "33333333-3333-3333-3333-333333333333",
        "video_processing_id": "33333333-3333-3333-3333-333333333334",
        "task_waiting_id": "44444444-4444-4444-4444-444444444444",
        "task_completed_id": "44444444-4444-4444-4444-444444444445",
        "video_qa_id": "55555555-5555-5555-5555-555555555555",
        "chat_id": "66666666-6666-6666-6666-666666666666",
        "global_qa_id": "77777777-7777-7777-7777-777777777777",
        "device_token_id": "88888888-8888-8888-8888-888888888888",
    }

    with SessionLocal() as session:
        if args.reset:
            _reset_tables(session)
            print("[OK] Cleared existing data")

        user = User(
            user_id=ids["user_id"],
            username="frontend_demo",
            password=hash_password("Demo123456!"),
        )

        kb = KnowledgeBase(
            kbid=ids["kbid"],
            owner_id=ids["user_id"],
            name="前端联调知识库",
            category="demo",
            description="用于前端模拟调用测试",
            vector_collection_name="kb_frontend_demo",
            config={
                "retrieval": {"top_k": 5, "rerank": True},
                "tool_preferences": {"allow_web_search": False},
                "llm_policy": {"temperature": 0.2},
            },
        )

        video_ready = VideoResource(
            video_id=ids["video_ready_id"],
            owner_id=ids["user_id"],
            file_name="demo_ready_video.mp4",
            oss_key="videos/demo_ready_video.mp4",
            duration=1260,
            full_transcript="这是用于前端联调的完整转录文本。",
            transcribe_status=TranscribeStatus.COMPLETED,
            transcript_vector_ids=["vec_transcript_001", "vec_transcript_002"],
            keyframes=[
                {
                    "time": "00:01:20",
                    "scene_change_score": 0.81,
                    "scene_change_level": "moderate",
                    "oss_key": "frames/demo_ready/frame_0001.jpg",
                },
                {
                    "time": "00:09:45",
                    "scene_change_score": 0.93,
                    "scene_change_level": "severe",
                    "oss_key": "frames/demo_ready/frame_0002.jpg",
                },
            ],
            frame_extraction_status=FrameExtractionStatus.COMPLETED,
            keyframes_oss_prefix="frames/demo_ready/",
            extract_completed_at=now,
            deletion_status="NONE",
        )

        video_processing = VideoResource(
            video_id=ids["video_processing_id"],
            owner_id=ids["user_id"],
            file_name="demo_processing_video.mp4",
            oss_key="videos/demo_processing_video.mp4",
            duration=540,
            transcribe_status=TranscribeStatus.TRANSCRIBING,
            frame_extraction_status=FrameExtractionStatus.EXTRACTING,
            deletion_status="NONE",
        )

        task_waiting = VideoSummaryTask(
            task_id=ids["task_waiting_id"],
            kbid=ids["kbid"],
            video_id=ids["video_ready_id"],
            workflow_state=WorkflowState.WAITING_USER_APPROVAL,
            user_initial_preference="请突出业务结论",
            draft_summary="这是第一阶段草稿总结。",
            title="Demo 任务（待审批）",
            summary_vector_ids=["vec_summary_draft_001"],
        )

        task_completed = VideoSummaryTask(
            task_id=ids["task_completed_id"],
            kbid=ids["kbid"],
            video_id=ids["video_ready_id"],
            workflow_state=WorkflowState.COMPLETED,
            user_initial_preference="关注技术实现",
            draft_summary="这是第二个任务的草稿。",
            final_summary="这是第二个任务的最终总结内容。",
            user_guidance="多给关键时间点",
            title="Demo 任务（已完成）",
            summary_vector_ids=["vec_summary_final_001", "vec_summary_final_002"],
        )

        video_qa = VideoQARecord(
            qa_id=ids["video_qa_id"],
            task_id=ids["task_completed_id"],
            start_time="00:05:00",
            end_time="00:06:00",
            question_content="这一分钟主要讲了什么？",
            answer_content="这一分钟主要讲了模型评估方法和关键指标。",
            attachments=[
                {
                    "name": "slide_5.png",
                    "oss_key": "attachments/slide_5.png",
                    "mime_type": "image/png",
                    "size_bytes": 204800,
                }
            ],
        )

        chat = GlobalChatSession(
            chat_id=ids["chat_id"],
            kbid=ids["kbid"],
            chat_title="全局问答联调会话",
        )

        global_qa = GlobalQARecord(
            qa_id=ids["global_qa_id"],
            chat_id=ids["chat_id"],
            question_content="跨视频来看，核心结论是什么？",
            answer_content="核心结论是应优先优化召回质量，再做总结压缩。",
            attachments=[],
            cited_sources=[
                {
                    "video_id": ids["video_ready_id"],
                    "task_id": ids["task_completed_id"],
                    "time_range": "00:09:30-00:10:20",
                    "quote": "先提升召回，再做摘要融合",
                    "score": 0.92,
                }
            ],
        )

        device = DeviceToken(
            device_token_id=ids["device_token_id"],
            user_id=ids["user_id"],
            device_token="fcm_mock_token_frontend_demo",
            platform="android",
            app_version="1.0.0",
            device_id="android-demo-001",
        )

        # 分阶段 flush：父表先落库，保证外键依赖顺序稳定。
        session.add(user)
        session.flush()

        session.add(kb)
        session.add(video_ready)
        session.add(video_processing)
        session.flush()

        session.execute(
            kb_video_relation_table.insert().values(kbid=ids["kbid"], video_id=ids["video_ready_id"])
        )
        session.execute(
            kb_video_relation_table.insert().values(kbid=ids["kbid"], video_id=ids["video_processing_id"])
        )

        session.add(task_waiting)
        session.add(task_completed)
        session.add(chat)
        session.flush()

        session.add(video_qa)
        session.add(global_qa)
        session.add(device)

        session.commit()

    print("[OK] Seed data inserted")
    print("[INFO] Login account:")
    print("  username: frontend_demo")
    print("  password: Demo123456!")
    print("[INFO] Key IDs:")
    print(json.dumps(ids, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
