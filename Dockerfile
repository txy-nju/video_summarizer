FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Install system dependencies (ffmpeg and opencv requirements)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsm6 \
    libxext6 \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY video_summarizer/requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY video_summarizer /app/

# Copy modular_rag from the private monorepo
COPY MODULAR-RAG-MCP-SERVER/src/modular_rag /app/modular_rag/

# Create necessary directories
RUN mkdir -p /app/temp/object_storage /app/test_output

# Expose ports (8000 for FastAPI, 8501 for Streamlit)
EXPOSE 8000
EXPOSE 8501
