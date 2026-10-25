from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")

    app_name: str = "Minecraft Clipper"
    environment: str = "development"
    work_dir: Path = Path("./data")
    database_path: Path | None = None
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "openai/gpt-oss-20b"
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    max_video_seconds: int = 4 * 60 * 60
    max_clip_count: int = 15
    download_timeout_seconds: int = 900
    ffmpeg_preset: str = "veryfast"
    output_width: int = 1080
    output_height: int = 1920
    log_level: str = "INFO"
    app_username: str = ""
    app_password: str = ""

    @property
    def db_path(self) -> Path:
        return self.database_path or self.work_dir / "clipper.sqlite3"

    @property
    def jobs_dir(self) -> Path:
        return self.work_dir / "jobs"

    @property
    def prompts_dir(self) -> Path:
        # Prompts are stored alongside the project, not in user-uploaded job folders.
        return Path(__file__).resolve().parent.parent / "prompts"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    return settings
