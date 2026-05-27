@echo off
echo Starting FastAPI server in NORMAL mode (no reload, highly stable)...
uvicorn backend.main:app --host 0.0.0.0 --port 8000
pause
