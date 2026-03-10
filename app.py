import os
import subprocess
import tempfile
import logging
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException, Request, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Amharic Video Title Adder")
templates = Jinja2Templates(directory="templates")

FONT_PATH = "/usr/share/fonts/truetype/freefont/FreeSans.ttf"

def check_font():
    if not os.path.exists(FONT_PATH):
        logger.error(f"Font file not found at {FONT_PATH}")
        raise RuntimeError(f"Font file missing: {FONT_PATH}")
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
    except subprocess.TimeoutExpired:
        logger.error("FFmpeg timeout")
        raise HTTPException(status_code=504, detail="Processing timeout")
    except Exception as e:
        logger.exception("Unexpected ffmpeg error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/process")
async def process_video(file: UploadFile = File(...), background_tasks: BackgroundTasks = None):
    # Validate content type
    if not file.content_type or not file.content_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="File must be a video")
    
    suffix = Path(file.filename).suffix or ".mp4"
    try:
        # Save input file
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_in:
            content = await file.read()
            tmp_in.write(content)
            tmp_in_path = tmp_in.name
            logger.info(f"Saved input to {tmp_in_path} ({len(content)} bytes)")
        
        # Output file
        tmp_out_path = tempfile.NamedTemporaryFile(delete=False, suffix=suffix).name
        
        # Process
        add_text_overlay(tmp_in_path, tmp_out_path)
        
        # Schedule deletion of temp files
        if background_tasks:
            background_tasks.add_task(os.unlink, tmp_in_path)
            background_tasks.add_task(os.unlink, tmp_out_path)
        else:
            # Fallback: delete input now, output later (not ideal)
            os.unlink(tmp_in_path)
            # We'll still return FileResponse, but output file won't be auto-deleted
            # So better to always use background_tasks.
            # Ensure BackgroundTasks is injected (it will be by FastAPI)
            pass
        
        return FileResponse(
            tmp_out_path,
            media_type="video/mp4",
            filename=f"amharic_{file.filename}"
        )
    except HTTPException:
        # Clean up input file if it exists
        if 'tmp_in_path' in locals():
            try:
                os.unlink(tmp_in_path)
            except:
                pass
        raise
    except Exception as e:
        logger.exception("Unhandled exception")
        raise HTTPException(status_code=500, detail="Internal server error")
