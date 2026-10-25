from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=2)
def _get_model(model_size: str, device: str, compute_type: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("faster-whisper is not installed. Run: pip install -r requirements.txt") from exc
    logger.info("Loading faster-whisper model=%s device=%s compute_type=%s", model_size, device, compute_type)
    return WhisperModel(model_size, device=device, compute_type=compute_type)


def transcribe_video(path: Path, language: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
    settings = get_settings()
    model = _get_model(settings.whisper_model, settings.whisper_device, settings.whisper_compute_type)
    segments, metadata = model.transcribe(
        str(path),
        language=language or None,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,
    )
    words: list[dict[str, Any]] = []
    for segment in segments:
        for word in (segment.words or []):
            token = (word.word or "").strip()
            if not token:
                continue
            start = max(0.0, float(word.start))
            end = max(start + 0.02, float(word.end))
            words.append({"start": round(start, 3), "end": round(end, 3), "word": token})
    if not words:
        raise RuntimeError("Transcription returned no words. Try a clearer video or another Whisper model.")
    detected = getattr(metadata, "language", None)
    logger.info("Transcription complete: words=%d language=%s", len(words), detected)
    return words, detected
