from __future__ import annotations

import base64
import binascii
import hmac
import json
import logging
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import create_job, get_clip, get_clips, get_job, init_db, update_clip
from app.schemas import ClipRenderRequest, JobCreate
from app.security import validate_youtube_url
from app.services.pipeline import process_job, rerender_clip

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if bool(settings.app_username) != bool(settings.app_password):
        raise RuntimeError("Set both APP_USERNAME and APP_PASSWORD, or leave both empty for local development.")
    init_db()
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", description="Turn Minecraft videos you own or are authorized to use into captioned vertical clips.", lifespan=lifespan)
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def optional_basic_auth(request: Request, call_next):
    """Optional single-user HTTP Basic Auth; use HTTPS whenever remote access is allowed."""
    expected_user = settings.app_username
    expected_password = settings.app_password
    if not expected_user and not expected_password:
        return await call_next(request)
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Basic "):
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required."},
            headers={"WWW-Authenticate": 'Basic realm="Minecraft Clipper"'},
        )
    try:
        decoded = base64.b64decode(auth_header.split(" ", 1)[1], validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        decoded = ""
    username, separator, password = decoded.partition(":")
    valid_user = bool(separator) and hmac.compare_digest(username, expected_user)
    valid_password = bool(separator) and hmac.compare_digest(password, expected_password)
    if not (valid_user and valid_password):
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid credentials."},
            headers={"WWW-Authenticate": 'Basic realm="Minecraft Clipper"'},
        )
    return await call_next(request)


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "ffmpeg_available": shutil.which("ffmpeg") is not None,
        "nvidia_configured": bool(settings.nvidia_api_key),
        "whisper_model": settings.whisper_model,
    }


@app.post("/jobs", status_code=202)
def create_clipping_job(body: JobCreate, background_tasks: BackgroundTasks) -> dict[str, Any]:
    if not settings.nvidia_api_key:
        raise HTTPException(status_code=503, detail="NVIDIA_API_KEY is missing. Copy .env.example to .env, add your key, and restart.")
    if shutil.which("ffmpeg") is None:
        raise HTTPException(status_code=503, detail="FFmpeg is missing. Install FFmpeg and make sure it is on PATH.")
    try:
        normalized_url = validate_youtube_url(body.youtube_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job_id = uuid.uuid4().hex
    job_dir = settings.jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    create_job({"id": job_id, "source_url": normalized_url, "language": body.language, "top_n": body.top_n})
    background_tasks.add_task(process_job, job_id)
    return {"id": job_id, "status": "queued", "stage": "queued", "progress": 0, "poll_url": f"/jobs/{job_id}"}


def _job_payload(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    # Never expose the raw transcript or absolute filesystem paths through the status API.
    raw_transcript = job.pop("transcript_json", None)
    job.pop("video_path", None)
    try:
        parsed_transcript = json.loads(raw_transcript or "[]")
        word_count = len(parsed_transcript) if isinstance(parsed_transcript, list) else 0
    except (json.JSONDecodeError, TypeError):
        word_count = 0
    clips = get_clips(job_id)
    public_clips = []
    for clip in clips:
        absolute_path = Path(clip["output_path"]) if clip.get("output_path") else None
        clip["download_url"] = f"/media/{job_id}/{absolute_path.name}" if absolute_path and absolute_path.is_file() else None
        clip.pop("output_path", None)
        clip.pop("settings_json", None)
        public_clips.append(clip)
    job["clips"] = public_clips
    job["transcript_word_count"] = word_count
    return job



@app.get("/jobs/{job_id}")
def get_job_status(job_id: str) -> dict[str, Any]:
    return _job_payload(job_id)


@app.post("/jobs/{job_id}/clips/{clip_id}/render", status_code=202)
def render_edited_clip(job_id: str, clip_id: str, body: ClipRenderRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    job = get_job(job_id)
    clip = get_clip(clip_id)
    if not job or not clip or clip.get("job_id") != job_id:
        raise HTTPException(status_code=404, detail="Job or clip not found.")
    if job.get("status") != "completed":
        raise HTTPException(status_code=409, detail="Wait until the initial clip job has completed before re-rendering.")
    if not job.get("video_path") or not Path(job["video_path"]).is_file():
        raise HTTPException(status_code=410, detail="The original video is no longer available for re-rendering.")
    if job.get("video_duration") and body.end > float(job["video_duration"]):
        raise HTTPException(status_code=422, detail="Clip end time cannot exceed the original video duration.")
    caption_settings = body.model_dump(exclude={"start", "end"})
    update_clip(
        clip_id,
        start_seconds=body.start,
        end_seconds=body.end,
        settings_json=caption_settings,
        render_status="queued",
    )
    background_tasks.add_task(rerender_clip, job_id, clip_id)
    return {"message": "Re-render queued. AI analysis and transcription will not run again.", "job_id": job_id, "clip_id": clip_id, "render_status": "queued"}


@app.get("/media/{job_id}/{filename}", include_in_schema=False)
def serve_clip(job_id: str, filename: str) -> FileResponse:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(status_code=404, detail="Rendered clip not found.")
    if Path(filename).name != filename or not re.fullmatch(r"[a-f0-9]{32}\.mp4", filename):
        raise HTTPException(status_code=400, detail="Invalid media filename.")
    clip_dir = settings.jobs_dir / job_id / "clips"
    target = (clip_dir / filename).resolve()
    if target.parent != clip_dir.resolve() or not target.is_file():
        raise HTTPException(status_code=404, detail="Rendered clip not found.")
    # Do not force Content-Disposition: attachment; the same URL is used by the in-page video preview.
    return FileResponse(target, media_type="video/mp4")

