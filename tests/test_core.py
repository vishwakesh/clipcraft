from app.security import validate_youtube_url
from app.services.highlights import split_transcript, _dedupe


def test_normalizes_youtube_watch_url():
    assert validate_youtube_url("https://youtu.be/dQw4w9WgXcQ?t=20") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_rejects_lookalike_hosts():
    try:
        validate_youtube_url("https://youtube.com.attacker.example/watch?v=dQw4w9WgXcQ")
    except ValueError as exc:
        assert "Only YouTube" in str(exc)
    else:
        raise AssertionError("Lookalike YouTube host should be rejected")


def test_rejects_non_video_url():
    try:
        validate_youtube_url("https://www.youtube.com/playlist?list=PL1234567890")
    except ValueError:
        pass
    else:
        raise AssertionError("Playlist URLs should be rejected")


def test_transcript_chunks_overlap_and_keep_absolute_times():
    words = [
        {"start": float(t), "end": float(t + 1), "word": f"w{t}"}
        for t in range(0, 500, 5)
    ]
    chunks = split_transcript(words, duration=500, chunk_seconds=240, overlap_seconds=30)
    assert len(chunks) == 3
    assert chunks[0]["start"] == 0.0
    assert chunks[1]["start"] == 210.0
    assert "00:03:35.000" in chunks[1]["transcript"]


def test_dedupe_prefers_higher_score_for_overlapping_candidates():
    candidates = [
        {"start": 10, "end": 40, "score": 90, "title": "A"},
        {"start": 12, "end": 42, "score": 70, "title": "B"},
        {"start": 70, "end": 100, "score": 60, "title": "C"},
    ]
    assert [c["title"] for c in _dedupe(candidates)] == ["A", "C"]
