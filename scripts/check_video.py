import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.db.session import SessionLocal
from backend.models.database import VideoQARecord, VideoResource, VideoSummaryTask, kb_video_relation_table

VIDEO_ID = "2bf52fe3-74b8-410b-9067-f47788008dce"
db = SessionLocal()
video = db.query(VideoResource).filter(VideoResource.video_id == VIDEO_ID).one_or_none()
print(f"Video: {video.file_name} | oss={video.oss_key} | deleted={video.is_deleted}")
tasks = db.query(VideoSummaryTask).filter(VideoSummaryTask.video_id == VIDEO_ID).all()
print(f"Tasks: {len(tasks)}")
for t in tasks:
    qa = db.query(VideoQARecord).filter(VideoQARecord.task_id == t.task_id).count()
    print(f"  {t.task_id} state={t.workflow_state.value} qa={qa}")
kb = db.execute(kb_video_relation_table.select().where(kb_video_relation_table.c.video_id == VIDEO_ID)).fetchall()
print(f"KB links: {len(kb)}")
for r in kb:
    print(f"  kbid={r.kbid}")
db.close()
