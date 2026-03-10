# FFmpeg + Amharic Transcriber on Render

Deploy a full media processing system with ffmpeg and Amharic speech-to-text on Render's free tier.

## Deploy to Render

1. Fork this repository.
2. Create a new **Web Service** on Render, connect your repo.
3. Use the following settings:
   - **Environment**: Docker
   - **Plan**: Free
4. Click **Create Web Service**.

Render will build the Docker image and start the app.

## Usage

- Web UI: `https://your-service.onrender.com`
- API docs: `https://your-service.onrender.com/docs`
- n8n integration: Use the API endpoints (e.g., `POST /api/transcribe`).

## API Endpoints

| Method | Endpoint            | Description                          |
|--------|---------------------|--------------------------------------|
| POST   | /api/convert        | Convert media format                 |
| POST   | /api/trim           | Trim media                           |
| POST   | /api/extract-audio  | Extract audio stream                 |
| POST   | /api/transcribe     | Generate SRT (Amharic default)       |
| POST   | /api/burn-subtitles | Burn SRT into video                  |
| GET    | /api/info           | Get media info via ffprobe           |
| GET    | /api/health         | Check service status                 |

## Notes

- Free tier limits: 512 MB RAM, 60s timeout, ephemeral disk.
- First request may be slow because the model loads.
- Uploaded files are deleted after processing.
- For large files, consider using cloud storage and passing URLs.
