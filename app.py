import os
import subprocess
import tempfile
import logging
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from faster_whisper import WhisperModel

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Amharic Video Processor")
templates = Jinja2Templates(directory="templates")

# Global variable for Whisper model (lazy-loaded)
_whisper_model = None

def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        logger.info("Loading Whisper model (tiny)...")
        # Use tiny model for low memory; int8 quantization reduces RAM usage
        _whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
        logger.info("Whisper model loaded.")
    return _whisper_model

# Font path for drawtext filter
FONT_PATH = "/usr/share/fonts/truetype/freefont/FreeSans.ttf"

def check_font():
    if not os.path.exists(FONT_PATH):
        logger.error(f"Font file not found at {FONT_PATH}")
        raise RuntimeError(f"Required font missing: {FONT_PATH}")
    logger.info(f"Font file found: {FONT_PATH}")

def add_text_overlay(input_path: str, output_path: str):
    check_font()
    cmd = [
        "ffmpeg",
        "-i", input_path,
        "-vf", f"drawtext=text='Amharic Video':fontfile={FONT_PATH}:fontsize=48:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.5:boxborderw=10",
        "-codec:a", "copy",
        "-y", output_path
    ]
    logger.info(f"Running ffmpeg: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            logger.error(f"FFmpeg stderr: {result.stderr}")
            raise HTTPException(status_code=500, detail=f"FFmpeg error: {result.stderr}")
        logger.info("FFmpeg completed successfully")
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise HTTPException(status_code=500, detail="Output file missing or empty")
        return output_path
    except subprocess.TimeoutExpired:
        logger.error("FFmpeg timeout")
        raise HTTPException(status_code=504, detail="Processing timeout")
    except Exception as e:
        logger.exception("Unexpected ffmpeg error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/health", response_class=JSONResponse)
async def health_check():
    return {"status": "healthy", "font_ok": os.path.exists(FONT_PATH)}

@app.post("/process")
async def process_video(file: UploadFile = File(...), background_tasks: BackgroundTasks = None):
    # (same as before – overlay endpoint)
    if not file.content_type or not file.content_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="File must be a video")
    suffix = Path(file.filename).suffix or ".mp4"
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_in:
            content = await file.read()
            tmp_in.write(content)
            tmp_in_path = tmp_in.name
            logger.info(f"Saved input to {tmp_in_path} ({len(content)} bytes)")
        tmp_out_path = tempfile.NamedTemporaryFile(delete=False, suffix=suffix).name
        add_text_overlay(tmp_in_path, tmp_out_path)
        if background_tasks:
            background_tasks.add_task(os.unlink, tmp_in_path)
            background_tasks.add_task(os.unlink, tmp_out_path)
        return FileResponse(tmp_out_path, media_type="video/mp4", filename=f"amharic_{file.filename}")
    except Exception as e:
        if 'tmp_in_path' in locals() and os.path.exists(tmp_in_path):
            os.unlink(tmp_in_path)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/transcribe")
async def transcribe_media(
    file: UploadFile = File(...),
    language: str = Form("am"),      # default Amharic
    task: str = Form("transcribe")   # or "translate"
):
    """
    Transcribe an audio/video file and return an SRT subtitle file.
    """
    # Determine file extension
    suffix = Path(file.filename).suffix
    if not suffix:
        suffix = ".mp4"  # assume video if no extension

    # Save uploaded file
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_in:
            content = await file.read()
            tmp_in.write(content)
            tmp_in_path = tmp_in.name
            logger.info(f"Saved input for transcription: {tmp_in_path} ({len(content)} bytes)")
    except Exception as e:
        logger.exception("Failed to save uploaded file")
        raise HTTPException(status_code=500, detail="Could not save file")

    # If video, extract audio first
    audio_path = tmp_in_path
    need_cleanup_audio = False
    if suffix.lower() in ['.mp4', '.mkv', '.avi', '.mov', '.webm']:
        logger.info("Input is video, extracting audio...")
        audio_path = tempfile.NamedTemporaryFile(delete=False, suffix=".wav").name
        need_cleanup_audio = True
        extract_cmd = [
            "ffmpeg", "-i", tmp_in_path,
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            "-y", audio_path
        ]
        try:
            result = subprocess.run(extract_cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                logger.error(f"Audio extraction failed: {result.stderr}")
                raise HTTPException(status_code=500, detail="Could not extract audio from video")
            logger.info(f"Audio extracted to {audio_path}")
        except subprocess.TimeoutExpired:
            logger.error("Audio extraction timeout")
            raise HTTPException(status_code=504, detail="Audio extraction timeout")
        except Exception as e:
            logger.exception("Audio extraction error")
            raise HTTPException(status_code=500, detail="Audio extraction failed")

    try:
        # Load Whisper model (lazy)
        model = get_whisper_model()
        logger.info(f"Starting transcription for language={language}, task={task}")
        segments, info = model.transcribe(audio_path, language=language, task=task, beam_size=5)

        # Build SRT content
        srt_lines = []
        for i, seg in enumerate(segments, start=1):
            start_srt = f"{int(seg.start//3600):02d}:{int((seg.start%3600)//60):02d}:{seg.start%60:06.3f}".replace('.', ',')
            end_srt = f"{int(seg.end//3600):02d}:{int((seg.end%3600)//60):02d}:{seg.end%60:06.3f}".replace('.', ',')
            srt_lines.append(f"{i}\n{start_srt} --> {end_srt}\n{seg.text.strip()}\n")

        srt_content = "\n".join(srt_lines)

        # Write SRT to a temporary file
        srt_file = tempfile.NamedTemporaryFile(delete=False, suffix=".srt", mode='w', encoding='utf-8')
        srt_file.write(srt_content)
        srt_file.close()
        logger.info(f"Transcription complete, SRT saved to {srt_file.name}")

        # Return the SRT file
        return FileResponse(
            srt_file.name,
            media_type="text/plain",
            filename=f"subtitles_{Path(file.filename).stem}.srt"
        )
    except Exception as e:
        logger.exception("Transcription error")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")
    finally:
        # Cleanup temporary files
        try:
            os.unlink(tmp_in_path)
            logger.info(f"Deleted {tmp_in_path}")
        except Exception as e:
            logger.warning(f"Could not delete {tmp_in_path}: {e}")
        if need_cleanup_audio:
            try:
                os.unlink(audio_path)
                logger.info(f"Deleted {audio_path}")
            except Exception as e:
                logger.warning(f"Could not delete {audio_path}: {e}")
        # Note: srt_file is cleaned after response? We'll let the OS handle it or use background task.
        # For simplicity, we'll not delete it immediately because FileResponse needs it.
        # Since we're on ephemeral storage, it's okay if it remains; it will be deleted when the container restarts.
        # Alternatively, we can schedule deletion with BackgroundTasks, but that complicates.
        # We'll leave as is for now.
