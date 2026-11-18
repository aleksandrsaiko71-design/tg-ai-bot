"""Точка входа: polling локально, webhook на хостинге (Render и аналоги)."""

from __future__ import annotations

import asyncio
import logging
import signal
import time

try:  # На Windows с антивирусом/корпоративным прокси системные сертификаты надёжнее certifi.
    import truststore

    truststore.inject_into_ssl()
except ImportError:  # pragma: no cover
    pass

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from ai import AIClient
from config import Settings, load_settings
from handlers import create_router
from storage import HistoryStore

log = logging.getLogger("tg-ai-bot")
STARTED_AT = time.monotonic()

BOT_COMMANDS = [
    BotCommand(command="start", description="Начать общение"),
    BotCommand(command="reset", description="Новый диалог"),
    BotCommand(command="help", description="Как пользоваться ботом"),
]


def create_app(bot: Bot, dp: Dispatcher, settings: Settings) -> web.Application:
    app = web.Application()

    async def root(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "service": "tg-ai-bot",
                "ai_configured": bool(settings.ai_api_key),
                "model": settings.ai_model,
                "uptime_seconds": round(time.monotonic() - STARTED_AT, 1),
            }
        )

    app.router.add_get("/", root)
    app.router.add_get("/healthz", root)

    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=settings.webhook_secret,
    )
    webhook_handler.register(app, path=settings.webhook_path)
    setup_application(app, dp, bot=bot)
    return app


async def run_webhook(bot: Bot, dp: Dispatcher, settings: Settings) -> None:
    app = create_app(bot, dp, settings)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.port)
    await site.start()
    log.info("Webhook-сервер слушает порт %s", settings.port)
    log.info("Публичный URL: %s", settings.webhook_url)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    try:
        await stop.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await runner.cleanup()
        log.info("Сервер остановлен")


async def run_polling(bot: Bot, dp: Dispatcher) -> None:
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Запущен режим polling (локальная разработка)")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


async def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(link_preview_is_disabled=True),
    )
    ai = AIClient(
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,
        chat_url=settings.ai_chat_url,
        demo_mode=settings.demo_mode,
        model=settings.ai_model,
        system_prompt=settings.system_prompt,
        temperature=settings.ai_temperature,
        max_tokens=settings.ai_max_tokens,
        timeout=settings.request_timeout,
    )
    store = HistoryStore(max_messages=settings.max_history)
    dp = Dispatcher()
    dp.include_router(create_router(ai, store, settings))

    async def on_startup(bot: Bot) -> None:
        await bot.set_my_commands(BOT_COMMANDS)
        if settings.use_webhook and settings.skip_webhook_setup:
            log.warning("SKIP_WEBHOOK_SETUP=1: регистрация webhook в Telegram пропущена")
        elif settings.use_webhook:
            await bot.set_webhook(
                url=settings.webhook_url,
                secret_token=settings.webhook_secret,
                allowed_updates=dp.resolve_used_update_types(),
                # Не сбрасываем очередь: на free-плане Render сообщение,
                # разбудившее сервис, должно дойти до бота.
                drop_pending_updates=False,
            )
            log.info("Webhook зарегистрирован в Telegram: %s", settings.webhook_url)
        else:
            await bot.delete_webhook(drop_pending_updates=True)

        log.info("Бот @%s готов. Модель: %s", (await bot.get_me()).username, settings.ai_model)
        if settings.demo_mode:
            log.warning(
                "AI_API_KEY не задан — включён бесплатный демо-режим (%s). "
                "Для приватных диалогов добавьте свой ключ.",
                settings.ai_chat_url,
            )

    dp.startup.register(on_startup)

    async def on_shutdown(_bot: Bot) -> None:
        await ai.close()

    dp.shutdown.register(on_shutdown)

    try:
        if settings.use_webhook:
            await run_webhook(bot, dp, settings)
        else:
            await run_polling(bot, dp)
    finally:
        await ai.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Остановка по Ctrl+C")
