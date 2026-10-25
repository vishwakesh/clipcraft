from __future__ import annotations

import json
import logging
import math
from typing import Any

from app.services.nvidia import generate_json, load_prompt

logger = logging.getLogger(__name__)


def _stamp(seconds: float) -> str:
    total_ms = max(0, int(seconds * 1000))
    minutes, ms = divmod(total_ms, 60_000)
    seconds_part, millis = divmod(ms, 1000)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds_part:02d}.{millis:03d}"


def split_transcript(words: list[dict[str, Any]], duration: float, chunk_seconds: int = 240, overlap_seconds: int = 30) -> list[dict[str, Any]]:
    """Split a word-timestamp transcript into overlapping chunks with absolute video timestamps."""
    if not words:
        return []
    step = max(60, chunk_seconds - overlap_seconds)
    chunks: list[dict[str, Any]] = []
    start = 0.0
    while start < duration:
        end = min(duration, start + chunk_seconds)
        selected = [w for w in words if float(w["end"]) >= start and float(w["start"]) <= end]
        if selected:
            transcript_lines = [f"[{_stamp(float(w['start']))} - {_stamp(float(w['end']))}] {str(w['word'])}" for w in selected]
            chunks.append({"start": start, "end": end, "transcript": "\n".join(transcript_lines)})
        if end >= duration:
            break
        start += step
    return chunks


def _normalize_candidate(raw: dict[str, Any], duration: float) -> dict[str, Any] | None:
    try:
        start = float(raw.get("start", raw.get("start_seconds")))
        end = float(raw.get("end", raw.get("end_seconds")))
        score = float(raw.get("score", 50))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(start) or not math.isfinite(end) or not math.isfinite(score):
        return None
    start = max(0.0, min(start, duration))
    end = max(0.0, min(end, duration))
    if end <= start:
        return None
    span = end - start
    if span < 20:
        end = min(duration, start + 20)
        if end - start < 20:
            start = max(0.0, end - 20)
    elif span > 60:
        end = start + 60
    if end - start < 20 or end <= start:
        return None
    title = str(raw.get("title") or "Minecraft moment").strip()[:120]
    hook = str(raw.get("hook") or title).strip()[:220]
    reason = str(raw.get("reason") or "Strong moment in the transcript.").strip()[:500]
    return {"start": round(start, 3), "end": round(end, 3), "title": title, "hook": hook, "score": max(0, min(100, score)), "reason": reason}


def _overlap_ratio(a: dict[str, Any], b: dict[str, Any]) -> float:
    intersection = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    union = max(a["end"], b["end"]) - min(a["start"], b["start"])
    return intersection / union if union > 0 else 0.0


def _dedupe(candidates: list[dict[str, Any]], threshold: float = 0.45) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda c: c["score"], reverse=True):
        if any(_overlap_ratio(candidate, kept) >= threshold for kept in result):
            continue
        result.append(candidate)
    return result


def _as_array(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, dict):
        response = response.get("clips", response.get("highlights", response.get("items", [])))
    if not isinstance(response, list):
        raise ValueError("NVIDIA response must be a JSON array of clips.")
    return [item for item in response if isinstance(item, dict)]


def find_highlights(words: list[dict[str, Any]], duration: float, top_n: int) -> list[dict[str, Any]]:
    chunks = split_transcript(words, duration)
    if not chunks:
        raise RuntimeError("No transcript chunks were generated.")
    highlight_prompt = load_prompt("highlight_finder.txt")
    candidates: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        logger.info("Analyzing transcript chunk %d/%d (%s–%s sec)", index, len(chunks), int(chunk["start"]), int(chunk["end"]))
        prompt = (
            f"Video duration: {duration:.3f} seconds.\n"
            f"Chunk {index}/{len(chunks)} covers video seconds {chunk['start']:.3f} to {chunk['end']:.3f}.\n"
            "All returned start/end values MUST be absolute seconds from the beginning of the full video, not offsets within this chunk.\n"
            "Return 2-8 good candidates if the transcript supports them; return fewer rather than inventing moments.\n\n"
            f"TRANSCRIPT WITH ABSOLUTE WORD TIMESTAMPS:\n{chunk['transcript']}"
        )
        try:
            raw_items = _as_array(generate_json(highlight_prompt, prompt, max_tokens=3500))
        except Exception as exc:
            # One failed chunk should not discard highlights from all the other chunks.
            logger.exception("Highlight analysis failed for chunk %d: %s", index, exc)
            continue
        for raw in raw_items:
            normalized = _normalize_candidate(raw, duration)
            if normalized:
                # Only accept candidates plausibly within the current chunk and source duration.
                midpoint = (normalized["start"] + normalized["end"]) / 2
                if chunk["start"] - 5 <= midpoint <= chunk["end"] + 5:
                    candidates.append(normalized)
    if not candidates:
        raise RuntimeError("NVIDIA returned no valid highlight candidates. Try a different video or model.")

    pre_ranked = _dedupe(candidates)
    merger_prompt = load_prompt("batch_merger_ranker.txt")
    try:
        merged = _as_array(generate_json(merger_prompt, json.dumps({"video_duration": duration, "candidates": pre_ranked}, ensure_ascii=False), max_tokens=3500))
        normalized_merged = [candidate for raw in merged if (candidate := _normalize_candidate(raw, duration))]
        final_candidates = _dedupe(normalized_merged) if normalized_merged else pre_ranked
    except Exception:
        logger.exception("LLM batch merger failed; falling back to deterministic overlap removal")
        final_candidates = pre_ranked

    title_prompt = load_prompt("title_caption_writer.txt")
    try:
        rewrite_payload = [{"index": index, "title": c["title"], "hook": c["hook"], "reason": c["reason"]} for index, c in enumerate(final_candidates)]
        rewritten = _as_array(generate_json(title_prompt, json.dumps(rewrite_payload, ensure_ascii=False), max_tokens=2500))
        for raw in rewritten:
            try:
                idx = int(raw.get("index", -1))
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(final_candidates):
                title = str(raw.get("title") or "").strip()
                hook = str(raw.get("hook") or "").strip()
                if title:
                    final_candidates[idx]["title"] = " ".join(title.split()[:8])[:100]
                if hook:
                    final_candidates[idx]["hook"] = hook[:180]
    except Exception:
        logger.exception("Title writer failed; keeping generated titles and hooks")

    return sorted(final_candidates, key=lambda c: c["score"], reverse=True)[:top_n]
