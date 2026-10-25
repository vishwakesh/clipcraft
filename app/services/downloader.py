from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadedVideo:
    path: Path
    title: str
    duration: float


class _YDLLogger:
    def debug(self, message: str) -> None:
        if message.startswith("[debug]"):
            logger.debug(message)

    def warning(self, message: str) -> None:
        logger.warning("yt-dlp: %s", message)

    def error(self, message: str) -> None:
        logger.error("yt-dlp: %s", message)


def download_video(
    url: str,
    output_dir: Path,
    max_duration_seconds: int,
    progress_callback: Callable[[int], None] | None = None,
) -> DownloadedVideo:
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp is not installed. Run: pip install -r requirements.txt") from exc
    output_dir.mkdir(parents=True, exist_ok=True)

    def progress_hook(data: dict) -> None:
        if not progress_callback:
            return
        if data.get("status") == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            downloaded = data.get("downloaded_bytes", 0)
            if total:
                percent = max(0, min(100, int(downloaded * 100 / total)))
                progress_callback(percent)

    def match_filter(info: dict, *, incomplete: bool = False) -> str | None:
        duration = info.get("duration")
        if duration and duration > max_duration_seconds:
            return f"Video is {int(duration)} seconds long; limit is {max_duration_seconds} seconds."
        return None

    options = {
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": {"default": str(output_dir / "source.%(ext)s")},
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "logger": _YDLLogger(),
        "progress_hooks": [progress_hook],
        "match_filter": match_filter,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "continuedl": True,
        "restrictfilenames": True,
        "overwrites": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise RuntimeError("YouTube did not return video metadata.")
            # Avoid an output path derived from a remote title; we only use the fixed source.* template.
            candidates = [Path(str(info.get("filepath"))) if info.get("filepath") else None]
            candidates.append(Path(ydl.prepare_filename(info)))
    except Exception as exc:
        message = str(exc)
        if "Video is" in message and "limit is" in message:
            raise ValueError(message) from exc
        raise RuntimeError(f"Video download failed: {message[:400]}") from exc

    found: Path | None = None
    for candidate in candidates:
        if candidate and candidate.is_file() and candidate.suffix.lower() not in {".part", ".ytdl"}:
            found = candidate
            break
    if found is None:
        ignored = {".part", ".ytdl", ".json", ".description", ".vtt", ".srt"}
        options_found = [p for p in output_dir.glob("source.*") if p.is_file() and p.suffix.lower() not in ignored]
        if options_found:
            # Prefer mp4 where present, otherwise retain the actual extension.
            options_found.sort(key=lambda p: (p.suffix.lower() != ".mp4", p.stat().st_size == 0))
            found = options_found[0]
    if found is None or not found.is_file() or found.stat().st_size == 0:
        raise RuntimeError("Download finished, but no usable video file was found. Check FFmpeg and yt-dlp output.")

    duration = float(info.get("duration") or 0)
    if duration <= 0:
        raise RuntimeError("Could not read video duration from YouTube metadata.")
    if duration > max_duration_seconds:
        raise ValueError(f"Video is {int(duration)} seconds long; limit is {max_duration_seconds} seconds.")
    return DownloadedVideo(path=found, title=str(info.get("title") or "Untitled Minecraft video")[:300], duration=duration)
