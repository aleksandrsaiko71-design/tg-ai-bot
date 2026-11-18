"""Минимальный клиент OpenAI-совместимого API (OpenAI, OpenRouter, DeepSeek, Gemini и др.)."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Sequence

import httpx

log = logging.getLogger(__name__)


class AIError(Exception):
    """Ошибка AI-провайдера с понятным для пользователя кодом."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail


class AIClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        system_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 900,
        timeout: float = 90.0,
        chat_url: str | None = None,
        demo_mode: bool = False,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.chat_url = chat_url
        self.demo_mode = demo_mode
        self.model = model
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={"User-Agent": "tg-ai-bot/1.0"},
        )

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) or self.demo_mode

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def complete(self, history: Sequence[dict[str, str]]) -> str:
        if not self.enabled:
            raise AIError("no_key")

        payload = {
            "model": self.model,
            "messages": [self._system_message(), *history],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_error: AIError | None = None
        for attempt in range(2):
            try:
                response = await self._client.post(
                    self.chat_url or f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
            except httpx.TimeoutException as exc:
                last_error = AIError("timeout", str(exc))
            except httpx.HTTPError as exc:
                last_error = AIError("network", str(exc))
            else:
                if response.status_code == 200:
                    return self._parse(response)

                # Бесплатный демо-режим: один ответ в 45 секунд на IP.
                # Вместо ошибки честно ждём свободный слот (Telegram уже получил 200).
                if response.status_code == 402 and self.demo_mode and attempt == 0:
                    wait = self._retry_after(response)
                    log.info("Демо-лимит: жду %.0f с до следующего бесплатного ответа", wait)
                    await asyncio.sleep(wait + 1)
                    last_error = AIError("demo_limit", response.text[:300])
                    continue

                last_error = self._status_error(response)
                if response.status_code < 500 and response.status_code != 429:
                    raise last_error

            if attempt == 0:
                await asyncio.sleep(1.5)

        raise last_error or AIError("unknown")

    @staticmethod
    def _retry_after(response: httpx.Response) -> float:
        header = response.headers.get("Retry-After", "")
        if header.isdigit():
            return min(float(header), 45.0)
        match = re.search(r"retry after (\d+)", response.text, re.IGNORECASE)
        if match:
            return min(float(match.group(1)), 45.0)
        return 45.0

    def _system_message(self) -> dict[str, str]:
        today = datetime.now().strftime("%d.%m.%Y")
        return {
            "role": "system",
            "content": f"{self.system_prompt}\n\nСегодняшняя дата: {today}.",
        }

    def _parse(self, response: httpx.Response) -> str:
        try:
            data = response.json()
            message = data["choices"][0]["message"]
            content = (message.get("content") or "").strip()
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AIError("bad_response", str(exc)) from exc

        usage = data.get("usage") or {}
        log.info(
            "AI ответ получен: model=%s tokens=%s",
            data.get("model", self.model),
            usage.get("total_tokens", "?"),
        )
        if not content:
            raise AIError("empty")
        return content

    def _status_error(self, response: httpx.Response) -> AIError:
        body = response.text[:300]
        log.warning("AI API вернул %s: %s", response.status_code, body)
        status = response.status_code
        if status in (401, 403):
            return AIError("auth", body)
        if status == 404:
            return AIError("model", body)
        if status == 429:
            return AIError("rate_limit", body)
        if status == 402:
            return AIError("demo_limit" if self.demo_mode else "payment", body)
        if status >= 500:
            return AIError("server", body)
        return AIError("bad_request", body)
