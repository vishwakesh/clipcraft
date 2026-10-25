from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services.captions import create_ass_file

logger = logging.getLogger(__name__)
_SILENCE_START = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([0-9.]+)")


def _escape_filter_path(path: Path) -> str:
    value = path.resolve().as_posix()
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")


def _detect_keep_segments(video_path: Path, start: float, duration: float) -> list[tuple[float, float]]:
    """Return local-time segments outside long silences; fail closed by keeping the full clip."""
    command = [
        "ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info", "-ss", f"{start:.3f}",
        "-t", f"{duration:.3f}", "-i", str(video_path), "-vn", "-af",
        "silencedetect=noise=-45dB:d=0.55", "-f", "null", "-",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
    logs = result.stderr or ""
    starts = [float(v) for v in _SILENCE_START.findall(logs)]
    ends = [float(v) for v in _SILENCE_END.findall(logs)]
    if result.returncode != 0 and not (starts or ends):
        logger.warning("Silence detection failed; keeping full clip. %s", logs[-500:])
        return [(0.0, duration)]
    silences: list[tuple[float, float]] = []
    pending_start: float | None = None
    for i, silence_start in enumerate(starts):
        pending_start = silence_start
        # FFmpeg normally emits a paired end. If it doesn't, the silence runs to clip end.
        matching_end = next((end for end in ends if end >= silence_start), None)
        if matching_end is not None:
            silences.append((max(0.0, silence_start), min(duration, matching_end)))
            pending_start = None
    if pending_start is not None:
        silences.append((max(0.0, pending_start), duration))
    if not silences:
        return [(0.0, duration)]

    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for silence_start, silence_end in sorted(silences):
        silence_start = max(cursor, min(duration, silence_start))
        silence_end = max(silence_start, min(duration, silence_end))
        if silence_start - cursor >= 0.20:
            keep.append((cursor, silence_start))
        cursor = max(cursor, silence_end)
    if duration - cursor >= 0.20:
        keep.append((cursor, duration))
    total_kept = sum(end - start for start, end in keep)
    # Do not make a clip choppy or remove substantial ambience by mistake.
    if len(keep) <= 1 or total_kept < max(18.0, duration * 0.55):
        return [(0.0, duration)]
    return keep


def _make_trimmed_video(
    video_path: Path,
    temporary_path: Path,
    clip_start: float,
    clip_end: float,
    keep: list[tuple[float, float]],
) -> None:
    if len(keep) == 1 and keep[0][0] <= 0 and keep[0][1] >= clip_end - clip_start - 0.01:
        raise ValueError("No silence segments to concatenate")
    number = len(keep)
    graph_parts = [f"[0:v]split={number}" + "".join(f"[v{i}]" for i in range(number))]
    graph_parts.append(f"[0:a]asplit={number}" + "".join(f"[a{i}]" for i in range(number)))
    concat_inputs: list[str] = []
    for index, (segment_start, segment_end) in enumerate(keep):
        graph_parts.append(f"[v{index}]trim=start={segment_start:.3f}:end={segment_end:.3f},setpts=PTS-STARTPTS[vout{index}]")
        graph_parts.append(f"[a{index}]atrim=start={segment_start:.3f}:end={segment_end:.3f},asetpts=PTS-STARTPTS[aout{index}]")
        concat_inputs.extend([f"[vout{index}]", f"[aout{index}]"])
    graph_parts.append("".join(concat_inputs) + f"concat=n={number}:v=1:a=1[v][a]")
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{clip_start:.3f}",
        "-i", str(video_path), "-t", f"{clip_end - clip_start:.3f}", "-filter_complex", ";".join(graph_parts),
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", get_settings().ffmpeg_preset,
        "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
        str(temporary_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=600, check=False)
    if result.returncode != 0 or not temporary_path.is_file() or temporary_path.stat().st_size == 0:
        temporary_path.unlink(missing_ok=True)
        raise RuntimeError("Could not remove silent gaps from the clip; retry with Trim silent gaps turned off.")


def _map_words_after_trim(
    words: list[dict[str, Any]], clip_start: float, keep: list[tuple[float, float]]
) -> tuple[list[dict[str, Any]], float]:
    remapped: list[dict[str, Any]] = []
    offset = 0.0
    segment_offsets: list[tuple[float, float, float]] = []
    for start, end in keep:
        segment_offsets.append((start, end, offset))
        offset += end - start
    for word in words:
        local_start = float(word["start"]) - clip_start
        local_end = float(word["end"]) - clip_start
        best: tuple[float, float, float] | None = None
        for segment_start, segment_end, output_offset in segment_offsets:
            overlap_start = max(local_start, segment_start)
            overlap_end = min(local_end, segment_end)
            if overlap_end > overlap_start:
                candidate = (overlap_start, overlap_end, output_offset)
                if best is None or (candidate[1] - candidate[0]) > (best[1] - best[0]):
                    best = candidate
        if best:
            overlap_start, overlap_end, output_offset = best
            remapped.append({
                "start": round(output_offset + overlap_start - next(s for s, e, o in segment_offsets if o == output_offset), 3),
                "end": round(output_offset + overlap_end - next(s for s, e, o in segment_offsets if o == output_offset), 3),
                "word": word["word"],
            })
    return remapped, offset


def render_clip(
    video_path: Path,
    output_path: Path,
    start: float,
    end: float,
    words: list[dict[str, Any]],
    settings: dict[str, Any] | None = None,
) -> Path:
    settings = settings or {}
    if not video_path.is_file():
        raise FileNotFoundError("The original video file is missing; it may have been cleaned up.")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("FFmpeg is not installed or not on PATH.")
    duration = end - start
    if duration < 20 or duration > 60:
        raise ValueError("Clip duration must be between 20 and 60 seconds.")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    render_input = video_path
    input_start = start
    render_duration = duration
    caption_start = start
    caption_end = end
    caption_words = words
    intermediate: Path | None = None
    try:
        if bool(settings.get("trim_silence", False)):
            keep = _detect_keep_segments(video_path, start, duration)
            if not (len(keep) == 1 and keep[0][0] <= 0 and keep[0][1] >= duration - 0.01):
                intermediate = output_path.with_name(output_path.stem + ".trimmed.mp4")
                _make_trimmed_video(video_path, intermediate, start, end, keep)
                caption_words, render_duration = _map_words_after_trim(words, start, keep)
                render_input = intermediate
                input_start = 0.0
                caption_start = 0.0
                caption_end = render_duration

        ass_path = output_path.with_suffix(".ass")
        create_ass_file(caption_words, caption_start, caption_end, ass_path, settings)
        filter_path = _escape_filter_path(ass_path)
        focus_x = max(0.0, min(1.0, float(settings.get("crop_focus_x", 0.5))))
        focus_y = max(0.0, min(1.0, float(settings.get("crop_focus_y", 0.5))))
        video_filter = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920:(iw-ow)*{focus_x:.3f}:(ih-oh)*{focus_y:.3f},"
            "setsar=1,"
            f"subtitles=filename='{filter_path}'"
        )
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        if input_start > 0:
            command.extend(["-ss", f"{input_start:.3f}"])
        command.extend([
            "-i", str(render_input), "-t", f"{render_duration:.3f}", "-vf", video_filter,
            "-c:v", "libx264", "-preset", get_settings().ffmpeg_preset, "-crf", "22",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
            "-max_muxing_queue_size", "2048", str(output_path),
        ])
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("FFmpeg timed out while rendering the clip.") from exc
        if result.returncode != 0:
            logger.error("FFmpeg failed: %s", result.stderr[-2500:])
            raise RuntimeError("FFmpeg failed to render this clip. Check that FFmpeg includes libass and libx264.")
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise RuntimeError("FFmpeg reported success but produced an empty output file.")
        logger.info("Rendered clip %s (%0.1f sec)", output_path.name, render_duration)
        return output_path
    finally:
        try:
            output_path.with_suffix(".ass").unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove temporary subtitle file for %s", output_path)
        if intermediate:
            intermediate.unlink(missing_ok=True)
