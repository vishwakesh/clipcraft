# Minecraft Clipper (MVP)

A self-hosted FastAPI app that analyzes a YouTube video, finds Minecraft highlight candidates with NVIDIA's OpenAI-compatible API, transcribes words with `faster-whisper`, then renders 9:16 MP4 clips with karaoke-style captions burned in through FFmpeg/ASS.

**Use this only for videos you own or are authorized to download and edit.** This is a single-instance, single-user MVP, not a fully hardened public SaaS. Configure auth and HTTPS before remote access.

## What it does

- Validates single-video YouTube URLs and rejects lookalike hosts/playlist pages.
- Downloads the best available video/audio with `yt-dlp`, merging to MP4 where supported.
- Produces word-level timestamps with `faster-whisper`.
- Splits transcript into roughly four-minute batches with 30-second overlaps, then calls the NVIDIA API using three stored prompts: highlight finder, batch merger/ranker, and title/hook writer.
- Merges duplicate overlapping candidates, ranks them, keeps the requested top N, and constrains clips to 20–60 seconds.
- Uses FFmpeg to crop to 1080×1920, burn in 3-word karaoke captions, and export MP4.
- Lets you adjust clip start/end, caption font name/size/position/outline/highlight color, crop focus, and optional silence-gap trimming. Re-renders reuse the stored transcript and do **not** call the AI stages again.
- Stores job/clip state in SQLite and provides progress through `GET /jobs/{id}`.
- Cleans intermediate download data after the job, while retaining the original video to support re-renders.

## Project structure

```text
minecraft-clipper/
├── app/
│   ├── main.py                 # FastAPI routes, status and render endpoints
│   ├── config.py               # Environment settings
│   ├── database.py             # SQLite job/clip persistence
│   ├── schemas.py              # Typed API payloads and validation
│   ├── security.py             # YouTube URL validation
│   ├── services/
│   │   ├── downloader.py       # yt-dlp download
│   │   ├── transcription.py    # faster-whisper word timestamps
│   │   ├── nvidia.py           # NVIDIA API client + retries + JSON parsing
│   │   ├── highlights.py       # transcript chunks, detection, dedupe, ranking
│   │   ├── captions.py         # ASS karaoke caption generation
│   │   ├── renderer.py         # FFmpeg crop, silence trimming, MP4 export
│   │   └── pipeline.py         # pipeline orchestration and re-render jobs
│   └── static/                 # Minimal HTML/CSS/JS dashboard
├── prompts/                    # Editable AI system prompts
├── tests/
├── requirements.txt
├── Dockerfile
└── docker-compose.yml
```

## Requirements

- Python 3.11 recommended.
- FFmpeg available on `PATH`, built with `libass` and `libx264` support.
- An NVIDIA API key and access to the configured model.
- Enough disk space for the source video, Whisper model cache, and rendered clips. CPU transcription can be slow.

### Ubuntu / Debian setup

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv ffmpeg libass9 fonts-dejavu-core fonts-liberation
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set `NVIDIA_API_KEY`. Start with `WHISPER_MODEL=base` for an easier first test; `small` generally gives a stronger transcript but uses more memory/compute. Do not commit `.env` or share your API key.

Run the server:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. API documentation is at `http://127.0.0.1:8000/docs`; health check is `GET /health`.

### Docker

```bash
cp .env.example .env
# Edit .env and configure NVIDIA_API_KEY before starting.
docker compose up --build -d
docker compose logs -f clipper
```

Compose binds port 8000 to localhost on purpose. Put the app behind a TLS reverse proxy before allowing remote access. Set `APP_USERNAME` and `APP_PASSWORD` in `.env` to enable HTTP Basic Auth. Use a strong unique password. Basic Auth must be protected by HTTPS when accessed remotely.

## API examples

Create a job (only a video URL you own or are authorized to process):

```bash
curl -X POST http://127.0.0.1:8000/jobs \
  -H 'Content-Type: application/json' \
  -d '{"youtube_url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ","top_n":5,"language":"en"}'
```

The response includes a job `id`. Poll it for per-stage progress and clips:

```bash
curl http://127.0.0.1:8000/jobs/YOUR_JOB_ID
```

Re-render one clip after changing its timing/caption settings, without rerunning download, transcription, or AI highlight detection:

```bash
curl -X POST http://127.0.0.1:8000/jobs/YOUR_JOB_ID/clips/YOUR_CLIP_ID/render \
  -H 'Content-Type: application/json' \
  -d '{
    "start": 123.0,
    "end": 163.0,
    "font_name": "Poppins",
    "font_size": 64,
    "position": "lower",
    "outline": 4,
    "highlight_color": "yellow",
    "trim_silence": false,
    "crop_focus_x": 0.5,
    "crop_focus_y": 0.5
  }'
```

Clip downloads are exposed through the `download_url` field returned in the job status JSON. Clip duration must remain 20–60 seconds and the end time cannot exceed the source duration.

## Environment settings

| Variable | Purpose | Default |
|---|---|---|
| `NVIDIA_API_KEY` | API credential; required to submit jobs | empty |
| `NVIDIA_BASE_URL` | OpenAI-compatible NVIDIA endpoint | `https://integrate.api.nvidia.com/v1` |
| `NVIDIA_MODEL` | NVIDIA Catalog model name | `openai/gpt-oss-20b` |
| `WHISPER_MODEL` | faster-whisper model size | `small` in code; `base` in `.env.example` |
| `WHISPER_DEVICE` | `cpu` or `cuda` | `cpu` |
| `WHISPER_COMPUTE_TYPE` | compute precision | `int8` |
| `WORK_DIR` | SQLite and job storage root | `./data` |
| `MAX_VIDEO_SECONDS` | Maximum accepted source duration | `14400` |
| `APP_USERNAME` / `APP_PASSWORD` | Optional HTTP Basic Auth | empty (local development only) |
| `FFMPEG_PRESET` | Speed/quality tradeoff | `veryfast` |

Prompts are editable text files in `prompts/`. Change `NVIDIA_MODEL` if the default model is unavailable to your account.

## Fonts and visual behavior

The UI exposes Montserrat, Poppins, Bangers and Minecraft-style font names, but font files are **not bundled**. FFmpeg/libass uses fonts installed on the host/container; if a chosen family is missing, it may substitute another font. Install fonts from a source whose license permits your use, then restart/re-render. Captions use white text with a black outline/shadow and yellow karaoke highlighting by default.

The crop center can be adjusted via focus X/Y. It is not face tracking and does not understand Minecraft HUD layouts; preview each clip and adjust focus when important gameplay is off-center.

## Operational notes / limitations

- `BackgroundTasks` is intentionally used to keep this starter compact. Run a single Uvicorn worker for this MVP; concurrent multi-process deployments should replace it with Celery/RQ and a shared job queue.
- This tool does not include multi-user accounts, public SaaS billing, quotas, webhook delivery, or cloud object storage. Do not expose it as a public service without authentication, rate limits, per-user isolation, disk quotas, retention/deletion policy, and a TLS reverse proxy.
- The transcript-only model cannot reliably infer every visual event. It avoids inventing actions but will miss silent visual-only clutch plays. Consider adding a sampled-frame vision pass later.
- Silence trimming detects long low-volume gaps in audio. Minecraft ambience can be quiet; inspect the output if you enable this option. If FFmpeg cannot safely trim, retry with the option off.
- No zoom-punch effect is enabled in this MVP. Add it only after testing a robust loudness/event detector against real gameplay.
- Source videos are retained to enable re-rendering. Set an operational deletion/retention policy before storing many jobs.
- YouTube download availability, model access, and API limits can change. Respect creator rights, platform terms, and applicable law.

## Tests

```bash
pip install pytest
pytest -q
python -m compileall app
```
