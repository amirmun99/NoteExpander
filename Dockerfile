FROM python:3.12-slim

# System deps: ffmpeg (for Whisper), git (for openai-whisper model download), curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg git curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create data and log dirs
RUN mkdir -p data/audio logs

EXPOSE 8765
ENTRYPOINT ["python", "main.py"]
