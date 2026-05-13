from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config import get_settings

_settings = get_settings()

engine = create_engine(_settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, class_=Session, autocommit=False, autoflush=False)


def get_db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
