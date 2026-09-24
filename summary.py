"""OpenAI call summaries and private API key storage."""

import json
import os
import tempfile
from pathlib import Path
from urllib import error, request


DEFAULT_KEY_PATH = Path.home() / "keys" / "openai_key.txt"
CUSTOM_KEY_PATH = Path.home() / ".config" / "transcriber" / "openai_api_key"
MODEL = "gpt-5-nano"


def read_api_key():
    for path in (CUSTOM_KEY_PATH, DEFAULT_KEY_PATH):
        try:
            key = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if key:
            return key
    return ""


def has_custom_key():
    return CUSTOM_KEY_PATH.is_file()


def save_api_key(key):
    key = key.strip()
    if not key:
        raise ValueError("Введите ключ OpenAI API")
    directory = CUSTOM_KEY_PATH.parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=".openai_api_key-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(key + "\n")
        os.replace(temporary, CUSTOM_KEY_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def clear_custom_key():
    CUSTOM_KEY_PATH.unlink(missing_ok=True)


def summarize_call(transcript):
    key = read_api_key()
    if not key:
        raise RuntimeError("Ключ OpenAI API не задан в настройках")
    if not transcript.strip():
        raise RuntimeError("Расшифровка пуста")
    payload = {
        "model": MODEL,
        "store": False,
        "reasoning": {"effort": "minimal"},
        "max_output_tokens": 180,
        "instructions": (
            "Составь ровно одно короткое предложение на русском о телефонном разговоре: "
            "кто с кем говорил и о чём. Указывай имена и роли только если они ясны из текста; "
            "если собеседники не названы, используй «собеседники». Не выдумывай факты. "
            "Верни только предложение без заголовка."
        ),
        "input": transcript,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    api_request = request.Request(
        "https://api.openai.com/v1/responses",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(api_request, timeout=60) as response:
            result = json.load(response)
    except error.HTTPError as exc:
        raise RuntimeError(f"OpenAI API: HTTP {exc.code}") from exc
    except error.URLError as exc:
        raise RuntimeError("Не удалось подключиться к OpenAI API") from exc
    if result.get("status") != "completed":
        raise RuntimeError("OpenAI API не завершил создание саммари")
    parts = [
        content["text"]
        for item in result.get("output", [])
        if item.get("type") == "message"
        for content in item.get("content", [])
        if content.get("type") == "output_text" and content.get("text")
    ]
    summary = " ".join(" ".join(parts).split()).strip(' "«»')
    if not summary:
        raise RuntimeError("OpenAI API вернул пустое саммари")
    return summary
