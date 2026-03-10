FROM python:3.9-slim

# Install ffmpeg and a basic font for text overlay
RUN apt-get update && apt-get install -y ffmpeg fonts-freefont-ttf && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render sets the PORT environment variable
CMD uvicorn app:app --host 0.0.0.0 --port $PORT
