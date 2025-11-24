from __future__ import annotations

from dataclasses import dataclass

from aiotgbot import Bot, BotUpdate

from .settings import SETTINGS_KEY


@dataclass(frozen=True, slots=True)
class FromUserFilter:
    async def check(self, bot: Bot, update: BotUpdate) -> bool:
        settings = bot[SETTINGS_KEY]
        return (
            update.message is not None
            and update.message.from_ is not None
            and update.message.from_.username != settings.admin_username
        )


@dataclass(frozen=True, slots=True)
class FromAdminFilter:
    async def check(self, bot: Bot, update: BotUpdate) -> bool:
        settings = bot[SETTINGS_KEY]
        return (
            update.message is not None
            and update.message.from_ is not None
            and update.message.from_.username == settings.admin_username
        )
