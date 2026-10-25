from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, model_validator


class JobCreate(BaseModel):
    youtube_url: str = Field(min_length=10, max_length=2048)
    top_n: int = Field(default=5, ge=1, le=15)
    language: str | None = Field(default=None, min_length=2, max_length=12)


class CaptionSettings(BaseModel):
    font_name: Literal["Montserrat", "Poppins", "Bangers", "Minecraft"] = "Poppins"
    font_size: int = Field(default=64, ge=28, le=120)
    position: Literal["lower", "center", "top"] = "lower"
    outline: int = Field(default=4, ge=1, le=10)
    highlight_color: Literal["yellow", "cyan", "green", "white"] = "yellow"
    trim_silence: bool = False
    crop_focus_x: float = Field(default=0.5, ge=0.0, le=1.0)
    crop_focus_y: float = Field(default=0.5, ge=0.0, le=1.0)


class ClipRenderRequest(CaptionSettings):
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_span(self) -> "ClipRenderRequest":
        duration = self.end - self.start
        if duration < 20 or duration > 60:
            raise ValueError("Clip duration must be between 20 and 60 seconds.")
        return self


class MessageResponse(BaseModel):
    message: str
