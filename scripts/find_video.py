import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from backend.db.session import SessionLocal
    from backend.models.database import VideoResource
    db = SessionLocal()
    rows = db.query(VideoResource).filter(VideoResource.is_deleted == False).all()
    for r in rows:
        print(f"vid={r.video_id} owner={r.owner_id} name=[{r.file_name}] deleted={r.is_deleted}")
    db.close()
except Exception:
    traceback.print_exc()
