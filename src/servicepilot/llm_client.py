from __future__ import annotations

import json
import os
import re
from pathlib import Path
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def load_dotenv(path: str | Path) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


def extract_json(content: str) -> dict:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("model response contains no JSON object")
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise TypeError("model response JSON must be an object")
    return value


class OpenAICompatibleJSONClient:
    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 45.0) -> None:
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is missing")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.usage: dict[str, int | float] = {
            "calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "api_seconds": 0.0,
        }

    @classmethod
    def from_env(cls, env_path: str | Path) -> OpenAICompatibleJSONClient:
        load_dotenv(env_path)
        return cls(
            os.environ.get("DEEPSEEK_API_KEY", ""),
            os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        )

    def chat_json(self, system: str, user: str) -> dict:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0,
                "thinking": {"type": "disabled"},
                "response_format": {"type": "json_object"},
                "max_tokens": 512,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        started = perf_counter()
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"LLM API returned HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"LLM API request failed: {exc.reason}") from exc
        self.usage["calls"] += 1
        self.usage["api_seconds"] += perf_counter() - started
        api_usage = body.get("usage", {})
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if isinstance(api_usage.get(field), int):
                self.usage[field] += api_usage[field]
        return extract_json(body["choices"][0]["message"]["content"])
