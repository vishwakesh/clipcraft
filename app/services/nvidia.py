from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)


def load_prompt(name: str) -> str:
    path = get_settings().prompts_dir / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Required prompt file is missing: {path.name}") from exc


def _parse_json(content: str) -> Any:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if len(lines) >= 3 else lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Some models wrap a JSON array in a short explanation; recover only the bracketed payload.
        left_array, right_array = text.find("["), text.rfind("]")
        if left_array >= 0 and right_array > left_array:
            return json.loads(text[left_array:right_array + 1])
        left_obj, right_obj = text.find("{"), text.rfind("}")
        if left_obj >= 0 and right_obj > left_obj:
            return json.loads(text[left_obj:right_obj + 1])
        raise


def generate_json(system_prompt: str, user_prompt: str, max_tokens: int = 3500) -> Any:

    settings = get_settings()
    if not settings.nvidia_api_key:
        raise RuntimeError("NVIDIA_API_KEY is not configured. Add it to your .env file and restart the server.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("The OpenAI SDK is not installed. Run: pip install -r requirements.txt") from exc

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            client = OpenAI(
                base_url=settings.nvidia_base_url,
                api_key=settings.nvidia_api_key,
                timeout=90.0,
                max_retries=0,
            )
            completion = client.chat.completions.create(
                model=settings.nvidia_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=max_tokens,
                stream=False,
            )
            content = completion.choices[0].message.content or ""
            parsed = _parse_json(content)
            logger.debug("NVIDIA JSON call parsed successfully")
            return parsed
        except Exception as exc:
            last_error = exc
            if attempt >= 2:
                break
            delay = min(8, 2 ** attempt)
            logger.warning("NVIDIA request/JSON parsing failed (attempt %d/3); retrying in %ds: %s", attempt + 1, delay, exc)
            time.sleep(delay)
    raise RuntimeError(f"NVIDIA API failed after 3 attempts: {last_error}") from last_error
