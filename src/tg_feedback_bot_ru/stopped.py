from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Final, Self, cast

import msgspec
from aiotgbot import Bot
from aiotgbot.api_types import ChatId
from msgspec import Struct, field

from .types import Json

STOPPED_KEY_PREFIX: Final[str] = "stopped"

__all__ = ("Stopped",)


def _now_with_tz() -> datetime:
    return datetime.now(timezone(timedelta(seconds=-time.timezone)))


class Stopped(Struct, frozen=True):
    date_time: datetime = field(default_factory=_now_with_tz)
    blocked: bool = False

    @staticmethod
    def _key(chat_id: ChatId) -> str:
        return f"{STOPPED_KEY_PREFIX}|{chat_id}"

    async def set(self, bot: Bot, chat_id: ChatId) -> None:
        payload = cast(Json, msgspec.to_builtins(self))
        await bot.storage.set(self._key(chat_id), payload)

    @classmethod
    async def get(cls, bot: Bot, chat_id: ChatId) -> Self | None:
        data = await bot.storage.get(Stopped._key(chat_id))
        return cast(Self | None, msgspec.convert(data, cls | None))

    @staticmethod
    async def delete(bot: Bot, chat_id: ChatId) -> None:
        await bot.storage.delete(Stopped._key(chat_id))
