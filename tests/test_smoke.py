"""Быстрая проверка логики без Telegram и интернета: python tests/test_smoke.py"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from aiogram import Bot, Dispatcher
from aiohttp.test_utils import TestClient, TestServer

from ai import AIClient, AIError
from bot import create_app
from config import load_settings
from handlers import create_router, split_text
from storage import HistoryStore

FAKE_TOKEN = "123456789:" + "A" * 35


def test_history() -> None:
    store = HistoryStore(max_messages=4)
    store.append_many(1, ("user", "a"), ("assistant", "b"), ("user", "c"))
    assert [m["content"] for m in store.get(1)] == ["a", "b", "c"], store.get(1)

    store.append_many(1, ("user", "d"), ("assistant", "e"))
    messages = store.get(1)
    assert len(messages) == 4, messages
    assert messages[-1] == {"role": "assistant", "content": "e"}

    store.reset(1)
    assert store.get(1) == []
    print("ok  история диалога: обрезка и сброс")


def test_split_text() -> None:
    text = " ".join(["слово"] * 3000)
    parts = split_text(text, 4000)
    assert all(len(part) <= 4000 for part in parts), [len(p) for p in parts]
    assert " ".join(parts) == text

    solid = "x" * 9001
    parts = split_text(solid, 4000)
    assert "".join(parts) == solid
    print(f"ok  длинные сообщения: {len(text)} символов -> {len(split_text(text))} части")


async def test_ai_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "test-model"
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][-1]["content"] == "Привет"
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": "  Здравствуйте!  "}}],
                "usage": {"total_tokens": 42},
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ai = AIClient(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="test-model",
        system_prompt="Ты тестовый бот.",
        http_client=http_client,
    )
    answer = await ai.complete([{"role": "user", "content": "Привет"}])
    assert answer == "Здравствуйте!", repr(answer)
    await ai.close()
    await http_client.aclose()
    print("ok  AI-клиент: успешный ответ")


async def test_ai_errors() -> None:
    no_key = AIClient(api_key="", base_url="https://example.test/v1", model="m", system_prompt="s")
    try:
        await no_key.complete([])
    except AIError as exc:
        assert exc.kind == "no_key", exc.kind
    else:
        raise AssertionError("ожидалась ошибка no_key")
    await no_key.close()

    def unauthorized(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid api key"})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(unauthorized))
    ai = AIClient(
        api_key="bad",
        base_url="https://example.test/v1",
        model="m",
        system_prompt="s",
        http_client=http_client,
    )
    try:
        await ai.complete([{"role": "user", "content": "hi"}])
    except AIError as exc:
        assert exc.kind == "auth", exc.kind
    else:
        raise AssertionError("ожидалась ошибка auth")
    await ai.close()
    await http_client.aclose()
    print("ok  AI-клиент: понятные ошибки (нет ключа, неверный ключ)")


async def test_demo_wait() -> None:
    """402 от бесплатного демо-API превращается в ожидание, а не в ошибку."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                402,
                headers={"Retry-After": "0"},
                text='{"error":{"message":"Free limit reached: Retry after 0s"}}',
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Готово"}}]},
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ai = AIClient(
        api_key="",
        base_url="https://demo.test",
        chat_url="https://demo.test/openai",
        demo_mode=True,
        model="openai",
        system_prompt="s",
        http_client=http_client,
    )
    answer = await ai.complete([{"role": "user", "content": "hi"}])
    assert answer == "Готово", answer
    assert calls["n"] == 2, calls
    await ai.close()
    await http_client.aclose()
    print("ok  демо-режим: ожидание свободного слота вместо ошибки")


async def test_web_app() -> None:
    os.environ["BOT_TOKEN"] = FAKE_TOKEN
    os.environ["WEBHOOK_BASE_URL"] = "https://bot.example.com"
    os.environ["SKIP_WEBHOOK_SETUP"] = "1"
    os.environ["AI_API_KEY"] = "test-key"
    settings = load_settings()
    assert settings.use_webhook and settings.webhook_url == "https://bot.example.com/webhook"

    bot = Bot(settings.bot_token)
    ai = AIClient(
        api_key="test",
        base_url="https://example.test/v1",
        model="m",
        system_prompt="s",
    )
    dp = Dispatcher()
    dp.include_router(create_router(ai, HistoryStore(), settings))
    app = create_app(bot, dp, settings)

    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/healthz")
        assert response.status == 200, response.status
        data = await response.json()
        assert data["status"] == "ok", data
        assert data["ai_configured"] is True, data
        assert data["model"] == settings.ai_model, data

        # Без секретного заголовка Telegram webhook должен отклоняться.
        response = await client.post(settings.webhook_path, json={})
        assert response.status in (401, 403), response.status
    finally:
        await client.close()
        await ai.close()
        await bot.session.close()
    print("ok  webhook-приложение: /healthz отвечает, чужой запрос отклонён")


async def main() -> None:
    test_history()
    test_split_text()
    await test_ai_success()
    await test_ai_errors()
    await test_demo_wait()
    await test_web_app()
    print("\nВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ✅")


if __name__ == "__main__":
    asyncio.run(main())
