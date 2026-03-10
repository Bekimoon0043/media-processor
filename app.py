import os
import subprocess
import tempfile
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Amharic Video Title Adder")

templates = Jinja2Templates(directory="templates")

def add_text_overlay(input_path: str, output_path: str):
    """
    Use ffmpeg drawtext filter to overlay 'Amharic Video' on the video.
    """
    # Using a basic font (FreeSans) from fonts-freefont-ttf package
    cmd = [
        "ffmpeg",
        "-i", input_path,
        "-vf", "drawtext=text='Amharic Video':fontfile=/usr/share/fonts/truetype/freefont/FreeSans.ttf:fontsize=48:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.5:boxborderw=10",
        "-codec:a", "copy",   # copy audio stream without re-encoding
        "-y", output_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"FFmpeg error: {result.stderr}")
        return output_path
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Processing timeout")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/process")
async def process_video(file: UploadFile = File(...)):
    """
    Upload a video, add 'Amharic Video' text overlay, and return the result.
    """
    # Save uploaded file temporarily
    suffix = Path(file.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_in:
        content = await file.read()
        tmp_in.write(content)
        tmp_in_path = tmp_in.name

    # Output file
    tmp_out_path = tempfile.NamedTemporaryFile(delete=False, suffix=suffix).name

    try:
        add_text_overlay(tmp_in_path, tmp_out_path)
        return FileResponse(
            tmp_out_path,
            media_type="video/mp4",
            filename=f"amharic_{file.filename}"
        )
    finally:
        # Clean up temporary files
        os.unlink(tmp_in_path)
        os.unlink(tmp_out_path)

@app.get("/health")
async def health():
    return {"status": "ok"}
