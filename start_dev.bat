@echo off
echo Starting FastAPI server in DEVELOPMENT mode (with hot-reload and temp excluded)...
uvicorn backend.main:app --reload --reload-dir backend --reload-dir core
pause
