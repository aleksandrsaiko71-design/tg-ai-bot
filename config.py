"""Настройки бота: читаются из переменных окружения или файла .env."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Бесплатный OpenAI-совместимый эндпоинт без ключа — используется, пока не добавлен AI_API_KEY.
DEMO_CHAT_URL = "https://text.pollinations.ai/openai"
DEMO_MODEL = "openai"

DEFAULT_SYSTEM_PROMPT = (
    "Ты — дружелюбный и эрудированный собеседник в Telegram. "
    "Отвечай на языке пользователя, понятно и по делу, без воды. "
    "Сообщения отправляются как обычный текст, поэтому не используй markdown-таблицы, "
    "заголовки и сложное форматирование — только обычный текст, списки через «—» и эмодзи по минимуму. "
    "Если вопрос непонятен или данных не хватает — задай один короткий уточняющий вопрос. "
    "Если не знаешь ответа, честно скажи об этом, не выдумывай факты."
)


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    bot_token: str
    ai_api_key: str
    ai_base_url: str
    ai_chat_url: str | None
    ai_model: str
    demo_mode: bool
    system_prompt: str
    ai_temperature: float
    ai_max_tokens: int
    max_history: int
    request_timeout: float
    webhook_base_url: str | None
    webhook_path: str
    webhook_secret: str
    port: int
    log_level: str
    skip_webhook_setup: bool

    @property
    def use_webhook(self) -> bool:
        return bool(self.webhook_base_url)

    @property
    def webhook_url(self) -> str:
        assert self.webhook_base_url  # проверяется в use_webhook
        return f"{self.webhook_base_url.rstrip('/')}{self.webhook_path}"


def load_settings() -> Settings:
    # .env нужен только для локального запуска, на хостинге переменные задаются в панели.
    load_dotenv(BASE_DIR / ".env")

    token = _env("BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Не задан BOT_TOKEN.\n"
            "Локально: создайте файл .env по образцу .env.example и вставьте токен от @BotFather.\n"
            "На хостинге: добавьте переменную окружения BOT_TOKEN."
        )

    # Render сам сообщает публичный адрес сервиса — webhook включается автоматически.
    webhook_base = _env("WEBHOOK_BASE_URL") or _env("RENDER_EXTERNAL_URL")

    ai_key = _env("AI_API_KEY", "") or ""
    demo_mode = not ai_key and _env_bool("DEMO_AI", True)
    # В демо-режиме параметры платного провайдера игнорируются.
    ai_model = DEMO_MODEL if demo_mode else (_env("AI_MODEL", "gpt-4o-mini") or "")

    return Settings(
        bot_token=token,
        ai_api_key=ai_key,
        ai_base_url=_env("AI_BASE_URL", "https://api.openai.com/v1") or "",
        ai_chat_url=_env("AI_CHAT_URL") or (DEMO_CHAT_URL if demo_mode else None),
        ai_model=ai_model,
        demo_mode=demo_mode,
        system_prompt=_env("SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT) or DEFAULT_SYSTEM_PROMPT,
        ai_temperature=_env_float("AI_TEMPERATURE", 0.7),
        ai_max_tokens=_env_int("AI_MAX_TOKENS", 900),
        max_history=_env_int("MAX_HISTORY", 24),
        request_timeout=_env_float("AI_TIMEOUT", 90.0),
        webhook_base_url=webhook_base,
        webhook_path=_env("WEBHOOK_PATH", "/webhook") or "/webhook",
        webhook_secret=_env("WEBHOOK_SECRET") or secrets.token_urlsafe(32),
        port=_env_int("PORT", 8080),
        log_level=(_env("LOG_LEVEL", "INFO") or "INFO").upper(),
        skip_webhook_setup=_env_bool("SKIP_WEBHOOK_SETUP"),
    )
