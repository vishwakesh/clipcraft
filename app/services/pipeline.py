from __future__ import annotations

import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.database import create_clips, get_clip, get_clips, get_job, update_clip, update_job
from app.services.downloader import download_video
from app.services.highlights import find_highlights
from app.services.renderer import render_clip
from app.services.transcription import transcribe_video

logger = logging.getLogger(__name__)
DEFAULT_CAPTION_SETTINGS: dict[str, Any] = {
    "font_name": "Poppins",
    "font_size": 64,
    "position": "lower",
    "outline": 4,
    "highlight_color": "yellow",
    "trim_silence": False,
    "crop_focus_x": 0.5,
    "crop_focus_y": 0.5,
}


def process_job(job_id: str) -> None:
    settings = get_settings()
    job = get_job(job_id)
    if not job:
        logger.error("Cannot process missing job %s", job_id)
        return
    job_dir = settings.jobs_dir / job_id
    work_dir = job_dir / "work"
    clips_dir = job_dir / "clips"
    work_dir.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)
    source_path: Path | None = None
    try:
        update_job(job_id, status="running", stage="downloading", progress=3, error=None)

        def on_download(percent: int) -> None:
            update_job(job_id, progress=min(22, 3 + int(percent * 0.19)))

        downloaded = download_video(
            job["source_url"], work_dir, settings.max_video_seconds, progress_callback=on_download
        )
        source_path = job_dir / f"source_video{downloaded.path.suffix.lower()}"
        shutil.move(str(downloaded.path), str(source_path))
        update_job(
            job_id,
            stage="transcribing",
            progress=24,
            video_path=str(source_path.resolve()),
            video_title=downloaded.title,
            video_duration=downloaded.duration,
        )
        logger.info("Transcribing job=%s title=%s", job_id, downloaded.title)
        words, detected_language = transcribe_video(source_path, job.get("language"))
        update_job(job_id, transcript_json=words, language=job.get("language") or detected_language, stage="analyzing", progress=43)

        highlights = find_highlights(words, downloaded.duration, int(job.get("top_n") or 5))
        clips_payload: list[dict[str, Any]] = []
        for candidate in highlights:
            clips_payload.append({
                **candidate,
                "id": uuid.uuid4().hex,
                "settings": dict(DEFAULT_CAPTION_SETTINGS),
            })
        create_clips(job_id, clips_payload)
        saved_clips = get_clips(job_id)
        update_job(job_id, stage="rendering", progress=62)
        successes = 0
        errors: list[str] = []
        total = max(1, len(saved_clips))
        for index, clip in enumerate(saved_clips, start=1):
            output = clips_dir / f"{clip['id']}.mp4"
            update_clip(clip["id"], render_status="rendering")
            try:
                render_clip(
                    source_path,
                    output,
                    float(clip["start_seconds"]),
                    float(clip["end_seconds"]),
                    words,
                    clip.get("settings") or DEFAULT_CAPTION_SETTINGS,
                )
                update_clip(clip["id"], output_path=str(output.resolve()), render_status="completed")
                successes += 1
            except Exception as exc:
                logger.exception("Failed rendering clip %s from job %s", clip["id"], job_id)
                update_clip(clip["id"], render_status="failed", output_path=None)
                output.unlink(missing_ok=True)
                errors.append(f"Clip {index}: {str(exc)[:180]}")
            update_job(job_id, progress=min(97, 62 + int(index / total * 35)))
        if successes == 0:
            raise RuntimeError("All clip renders failed. " + ("; ".join(errors[:3]) if errors else "Check FFmpeg/libass."))
        update_job(job_id, status="completed", stage="completed", progress=100, error=("Some clips failed to render: " + "; ".join(errors[:3])) if errors else None)
        logger.info("Job completed id=%s clips_rendered=%d", job_id, successes)
    except Exception as exc:
        logger.exception("Job failed id=%s", job_id)
        update_job(job_id, status="failed", stage="failed", error=str(exc)[:1000], progress=100)
    finally:
        # Keep the original source video for preview/re-render; remove downloader and intermediate temp data.
        shutil.rmtree(work_dir, ignore_errors=True)


def rerender_clip(job_id: str, clip_id: str) -> None:
    settings = get_settings()
    clip = get_clip(clip_id)
    job = get_job(job_id)
    if not clip or not job or clip["job_id"] != job_id:
        logger.warning("Cannot re-render unknown clip=%s job=%s", clip_id, job_id)
        return
    source_path = Path(str(job.get("video_path") or ""))
    if not source_path.is_file():
        update_clip(clip_id, render_status="failed")
        logger.error("Source video missing for re-render job=%s", job_id)
        return
    try:
        words = json.loads(job.get("transcript_json") or "[]")
        output_dir = settings.jobs_dir / job_id / "clips"
        output_dir.mkdir(parents=True, exist_ok=True)
        final_path = output_dir / f"{clip_id}.mp4"
        temporary_path = output_dir / f"{clip_id}.rendering.mp4"
        update_clip(clip_id, render_status="rendering")
        render_clip(
            source_path,
            temporary_path,
            float(clip["start_seconds"]),
            float(clip["end_seconds"]),
            words,
            clip.get("settings") or DEFAULT_CAPTION_SETTINGS,
        )
        temporary_path.replace(final_path)
        update_clip(clip_id, output_path=str(final_path.resolve()), render_status="completed")
    except Exception as exc:
        logger.exception("Re-render failed for clip=%s", clip_id)
        try:
            (settings.jobs_dir / job_id / "clips" / f"{clip_id}.rendering.mp4").unlink(missing_ok=True)
        except OSError:
            pass
        update_clip(clip_id, render_status="failed")
