"""Обработчики сообщений Telegram."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup
from aiogram.utils.chat_action import ChatActionSender

from ai import AIClient, AIError
from config import Settings
from storage import HistoryStore

log = logging.getLogger(__name__)

RESET_BUTTON = "🔄 Новый диалог"
HELP_BUTTON = "❓ Помощь"

WELCOME = (
    "Привет, {name}! 👋\n\n"
    "Я AI-собеседник: могу объяснить сложное простыми словами, помочь с текстом, "
    "идеями и кодом, просто поговорить.\n\n"
    "Напишите сообщение — и начнём. Контекст диалога я помню, "
    "а кнопка «{reset}» начинает разговор с чистого листа."
)

HELP = (
    "Как со мной общаться\n\n"
    "— Просто отправьте текстовое сообщение с вопросом или темой.\n"
    "— Я помню последние сообщения диалога, поэтому можно спрашивать «а почему?» и уточнять.\n"
    f"— «{RESET_BUTTON}» или /reset — забыть контекст и начать заново.\n\n"
    "Команды:\n"
    "/start — приветствие\n"
    "/reset — новый диалог\n"
    "/help — эта справка\n\n"
    "Пока я понимаю только текст: фото, видео и голосовые не разбираю."
)

NOT_CONFIGURED = (
    "🤖 Бот запущен и работает 24/7, но AI-ключ ещё не добавлен.\n\n"
    "Владельцу бота: добавьте переменную окружения AI_API_KEY "
    "(и при необходимости AI_BASE_URL / AI_MODEL) в настройках хостинга "
    "или в файле .env, затем перезапустите сервис."
)

ERROR_MESSAGES = {
    "auth": "🔑 Ключ AI-провайдера неверный или истёк. Проверьте AI_API_KEY в настройках.",
    "model": "⚙️ Указанная модель недоступна. Проверьте AI_MODEL и AI_BASE_URL.",
    "rate_limit": "⏳ AI-провайдер перегружен или закончился лимит запросов. Попробуйте через минуту.",
    "payment": "💳 У AI-провайдера закончился баланс. Пополните счёт или смените ключ.",
    "demo_limit": (
        "⏳ Бесплатный демо-режим перегружен: он даёт один ответ примерно в 45 секунд. "
        "Подождите минуту и повторите — или добавьте свой AI_API_KEY, чтобы отвечать быстро."
    ),
    "timeout": "⏱️ AI не ответил вовремя. Попробуйте ещё раз или задайте вопрос короче.",
    "network": "🌐 Не удалось связаться с AI-провайдером. Попробуйте чуть позже.",
    "server": "🛠️ Ошибка на стороне AI-провайдера. Попробуйте чуть позже.",
    "empty": "🤷 Модель вернула пустой ответ. Переформулируйте вопрос, пожалуйста.",
    "bad_request": "⚠️ AI-провайдер отклонил запрос. Проверьте настройки модели.",
}


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        resize_keyboard=True,
        input_field_placeholder="Напишите сообщение…",
        keyboard=[
            [KeyboardButton(text=RESET_BUTTON)],
            [KeyboardButton(text=HELP_BUTTON)],
        ],
    )


class Throttle:
    """Не даёт одному пользователю запускать несколько генераций одновременно."""

    def __init__(self, min_interval: float = 0.6) -> None:
        self.min_interval = min_interval
        self._last_started: dict[int, float] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    @asynccontextmanager
    async def slot(self, user_id: int) -> AsyncIterator[bool]:
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        if lock.locked():
            yield False
            return
        await lock.acquire()
        try:
            now = time.monotonic()
            if now - self._last_started.get(user_id, 0.0) < self.min_interval:
                yield False
                return
            self._last_started[user_id] = now
            yield True
        finally:
            lock.release()


def split_text(text: str, limit: int = 4000) -> list[str]:
    """Telegram принимает не больше 4096 символов за сообщение."""
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts


async def send_long(message: Message, text: str) -> None:
    for chunk in split_text(text):
        await message.answer(chunk)


def create_router(ai: AIClient, store: HistoryStore, settings: Settings) -> Router:
    router = Router(name="chat")
    throttle = Throttle()

    @router.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        name = message.from_user.first_name if message.from_user else "друг"
        text = WELCOME.format(name=name, reset=RESET_BUTTON)
        if ai.demo_mode:
            text += (
                "\n\n⚠️ Бот работает в демо-режиме: ответы генерирует бесплатный внешний сервис "
                "без ключа. Не отправляйте сюда пароли и личные документы."
            )
        await message.answer(text, reply_markup=main_keyboard())
        if not ai.enabled:
            await message.answer(NOT_CONFIGURED)

    @router.message(Command("help"))
    @router.message(F.text == HELP_BUTTON)
    async def cmd_help(message: Message) -> None:
        await message.answer(HELP, reply_markup=main_keyboard())

    @router.message(Command("reset"))
    @router.message(F.text == RESET_BUTTON)
    async def cmd_reset(message: Message) -> None:
        if message.from_user:
            store.reset(message.from_user.id)
        await message.answer(
            "Готово ✨ Контекст очищен, начинаем новый разговор.",
            reply_markup=main_keyboard(),
        )

    @router.message(F.text.startswith("/"))
    async def unknown_command(message: Message) -> None:
        await message.answer("Неизвестная команда. Список команд — /help.")

    @router.message(F.text)
    async def chat(message: Message) -> None:
        text = (message.text or "").strip()
        if not text or message.from_user is None:
            return

        if not ai.enabled:
            await message.answer(NOT_CONFIGURED)
            return

        user_id = message.from_user.id

        async with throttle.slot(user_id) as allowed:
            if not allowed:
                await message.answer("⏳ Секунду, я ещё отвечаю на предыдущее сообщение.")
                return

            history = store.get(user_id)
            history.append({"role": "user", "content": text})

            try:
                async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
                    answer = await ai.complete(history)
            except AIError as exc:
                log.warning("Ошибка AI для user=%s: kind=%s detail=%s", user_id, exc.kind, exc.detail)
                await message.answer(
                    ERROR_MESSAGES.get(exc.kind, "😔 Не получилось получить ответ. Попробуйте ещё раз.")
                )
                return
            except Exception:
                log.exception("Непредвиденная ошибка при ответе user=%s", user_id)
                await message.answer("😔 Что-то пошло не так. Попробуйте ещё раз чуть позже.")
                return

            store.append_many(user_id, ("user", text), ("assistant", answer))
            await send_long(message, answer)

    @router.message()
    async def unsupported(message: Message) -> None:
        if (message.text or "").startswith("/"):
            await message.answer("Неизвестная команда. Список команд — /help.")
            return
        await message.answer("Пока я понимаю только текст 🙂 Напишите вопрос словами.")

    return router
