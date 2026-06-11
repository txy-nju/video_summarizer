r"""
清理指定视频及其所有关联产物的脚本。
用法: cd video_summarizer && python scripts\cleanup_video.py
"""
import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db.session import SessionLocal
from backend.models.database import VideoQARecord, VideoResource, VideoSummaryTask, kb_video_relation_table

VIDEO_ID = "2bf52fe3-74b8-410b-9067-f47788008dce"

def main():
    db = SessionLocal()
    try:
        video = db.query(VideoResource).filter(VideoResource.video_id == VIDEO_ID).one_or_none()
        if not video:
            print(f"Video {VIDEO_ID} not found"); return

        print(f"[VIDEO] id={video.video_id} file={video.file_name} owner={video.owner_id}")
        print(f"  oss_key={video.oss_key} keyframes_prefix={video.keyframes_oss_prefix}")

        tasks = db.query(VideoSummaryTask).filter(VideoSummaryTask.video_id == VIDEO_ID).all()
        print(f"\n[TASKS] {len(tasks)} found:")
        for t in tasks:
            qa_count = db.query(VideoQARecord).filter(VideoQARecord.task_id == t.task_id).count()
            print(f"  task_id={t.task_id} state={t.workflow_state.value} qa={qa_count}")

        kb_rows = db.execute(kb_video_relation_table.select().where(kb_video_relation_table.c.video_id == VIDEO_ID)).fetchall()
        print(f"\n[KB] {len(kb_rows)} linked:")
        for r in kb_rows:
            print(f"  kbid={r.kbid}")

        print("\n" + "="*50)
        if input("Type YES to delete: ").strip() != "YES":
            print("Cancelled"); return

        for t in tasks:
            n = db.query(VideoQARecord).filter(VideoQARecord.task_id == t.task_id).delete(synchronize_session=False)
            print(f"  [DEL] QA for task {t.task_id}: {n} rows")
        for t in tasks:
            db.delete(t)
            print(f"  [DEL] Task: {t.task_id}")
        db.flush()  # 确保 Task 的 DELETE 先执行，避免 FK 约束冲突
        if kb_rows:
            db.execute(kb_video_relation_table.delete().where(kb_video_relation_table.c.video_id == VIDEO_ID))
            print(f"  [DEL] KB relations: {len(kb_rows)} rows")
        db.delete(video)
        print(f"  [DEL] Video: {VIDEO_ID}")

        db.commit()
        print("\n[DONE] DB records deleted.")
        print("\n[NOTE] Restart Celery worker. Chroma/OSS cleanup runs via recovery task.")
        if video.oss_key:
            print(f"  Manual cleanup: temp/object_storage/{video.oss_key}")
        if video.keyframes_oss_prefix:
            print(f"  Manual cleanup: temp/object_storage/{video.keyframes_oss_prefix}")
    except Exception:
        db.rollback()
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    main()
