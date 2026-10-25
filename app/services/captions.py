from __future__ import annotations

from pathlib import Path
from typing import Any

_POSITION = {"lower": (2, 250), "center": (5, 0), "top": (8, 180)}
_COLORS = {
    "yellow": "&H0000D7FF",  # ASS uses AABBGGRR.
    "cyan": "&H00FFFF00",
    "green": "&H0000FF66",
    "white": "&H00FFFFFF",
}


def _ass_time(seconds: float) -> str:
    total_cs = max(0, int(round(seconds * 100)))
    hours, rem = divmod(total_cs, 360000)
    minutes, rem = divmod(rem, 6000)
    whole_seconds, centiseconds = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"


def _safe_text(value: str) -> str:
    # Prevent transcript characters from being interpreted as ASS override tags.
    return value.replace("\\", "／").replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def create_ass_file(
    words: list[dict[str, Any]],
    clip_start: float,
    clip_end: float,
    path: Path,
    settings: dict[str, Any],
) -> Path:
    font_name = settings.get("font_name", "Poppins")
    font_size = int(settings.get("font_size", 64))
    outline = int(settings.get("outline", 4))
    position, margin_v = _POSITION.get(str(settings.get("position", "lower")), _POSITION["lower"])
    secondary = _COLORS.get(str(settings.get("highlight_color", "yellow")), _COLORS["yellow"])
    relevant = [
        {"start": max(clip_start, float(w["start"])), "end": min(clip_end, float(w["end"])), "word": _safe_text(str(w["word"]))}
        for w in words
        if float(w["end"]) > clip_start and float(w["start"]) < clip_end and str(w.get("word", "")).strip()
    ]
    relevant = [w for w in relevant if w["end"] > w["start"]]
    style = (
        f"Style: Default,{font_name},{font_size},&H00FFFFFF,{secondary},&H00000000,&H80000000,"
        f"1,0,0,0,100,100,0,0,1,{outline},2,{position},80,80,{margin_v},1"
    )
    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        style, "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    group_size = 3
    for index in range(0, len(relevant), group_size):
        group = relevant[index:index + group_size]
        local_start = max(0.0, group[0]["start"] - clip_start)
        local_end = min(clip_end - clip_start, group[-1]["end"] - clip_start)
        if local_end <= local_start:
            continue
        karaoke_parts: list[str] = []
        for word_index, word in enumerate(group):
            next_start = group[word_index + 1]["start"] if word_index + 1 < len(group) else word["end"]
            active_end = min(clip_end, max(word["start"] + 0.02, next_start))
            duration_cs = max(1, int(round((active_end - word["start"]) * 100)))
            karaoke_parts.append(f"{{\\kf{duration_cs}}}{word['word']}")
        text = " ".join(karaoke_parts)
        lines.append(f"Dialogue: 0,{_ass_time(local_start)},{_ass_time(local_end)},Default,,0,0,0,,{text}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
