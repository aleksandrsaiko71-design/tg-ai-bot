"""История диалогов в памяти процесса.

Плюс: ничего не стоит и не требует базы данных.
Минус: при перезапуске сервиса (или засыпании бесплатного инстанса Render)
контекст диалога сбрасывается. Для постоянного хранения замените этот класс
на SQLite/Redis — интерфейс специально сделан простым.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

Role = Literal["user", "assistant"]
Message = dict[str, str]


@dataclass
class _Session:
    messages: list[Message] = field(default_factory=list)
    updated_at: float = field(default_factory=time.monotonic)


class HistoryStore:
    def __init__(
        self,
        *,
        max_messages: int = 24,
        ttl_seconds: float = 6 * 60 * 60,
        max_users: int = 5000,
        max_message_chars: int = 4000,
    ) -> None:
        self.max_messages = max(2, max_messages)
        self.ttl_seconds = ttl_seconds
        self.max_users = max_users
        self.max_message_chars = max_message_chars
        self._sessions: dict[int, _Session] = {}

    def get(self, user_id: int) -> list[Message]:
        self._prune()
        session = self._sessions.get(user_id)
        if session is None:
            return []
        return [dict(item) for item in session.messages]

    def append_many(self, user_id: int, *items: tuple[Role, str]) -> None:
        self._prune()
        session = self._sessions.setdefault(user_id, _Session())
        for role, content in items:
            content = content.strip()
            if not content:
                continue
            if len(content) > self.max_message_chars:
                content = content[: self.max_message_chars] + "…"
            session.messages.append({"role": role, "content": content})
        if len(session.messages) > self.max_messages:
            session.messages = session.messages[-self.max_messages :]
        session.updated_at = time.monotonic()

    def reset(self, user_id: int) -> None:
        self._sessions.pop(user_id, None)

    def _prune(self) -> None:
        now = time.monotonic()
        expired = [
            user_id
            for user_id, session in self._sessions.items()
            if now - session.updated_at > self.ttl_seconds
        ]
        for user_id in expired:
            self._sessions.pop(user_id, None)

        # Страховка от роста памяти: удаляем самые давние диалоги.
        while len(self._sessions) > self.max_users:
            oldest = min(self._sessions, key=lambda uid: self._sessions[uid].updated_at)
            self._sessions.pop(oldest, None)
