from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse


_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_ALLOWED_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
}


def validate_youtube_url(raw_url: str) -> str:
    """Return a normalized HTTPS URL for a single YouTube video or raise ValueError."""
    value = raw_url.strip()
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"https", "http"}:
        raise ValueError("Use a valid YouTube video URL (https://...).")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in _ALLOWED_HOSTS:
        raise ValueError("Only YouTube video URLs are supported.")

    video_id: str | None = None
    path_parts = [part for part in parsed.path.split("/") if part]
    if host in {"youtu.be", "www.youtu.be"}:
        video_id = path_parts[0] if path_parts else None
    elif parsed.path.rstrip("/") == "/watch":
        video_id = parse_qs(parsed.query).get("v", [None])[0]
    elif len(path_parts) >= 2 and path_parts[0] in {"shorts", "embed", "live", "v"}:
        video_id = path_parts[1]
    if not video_id or not _VIDEO_ID.fullmatch(video_id):
        raise ValueError("That URL does not look like a single YouTube video URL.")
    return f"https://www.youtube.com/watch?v={video_id}"
