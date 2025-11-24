from __future__ import annotations

import logging
from html import escape
from typing import Final, cast

from aiotgbot import (
    Bot,
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ParseMode,
)
from aiotgbot.api_types import ChatId, User, UserId
from more_itertools import chunked

from .settings import SETTINGS_KEY
from .storage_keys import ADMIN_CHAT_ID_KEY, WAIT_REPLY_FROM_ID_KEY
from .types import Json

__all__ = (
    "CHAT_LIST_KEY",
    "REPLY_PREFIX",
    "add_chat_to_list",
    "chat_key",
    "get_admin_chat_id",
    "get_chat",
    "get_chat_list",
    "get_software",
    "get_wait_reply_from_id",
    "remove_chat_from_list",
    "reply_menu",
    "send_from_message",
    "set_admin_chat_id",
    "set_chat",
    "set_chat_list",
    "set_wait_reply_from_id",
    "user_link",
    "user_name",
)

CHAT_LIST_KEY: Final[str] = "chat_list"
REPLY_PREFIX: Final[str] = "reply"

logger = logging.getLogger("feedback-bot")


def get_software() -> str:
    from aiotgbot import __version__ as aiotgbot_version  # isort: skip
    from aiotgbot.helpers import get_python_version  # isort: skip

    from . import __version__  # isort: skip

    return (
        f"Python/{get_python_version()} "
        f"aiotgbot/{aiotgbot_version} "
        f"feedback-bot/{__version__}"
    )


def user_name(user_chat: User | Chat) -> str:
    if user_chat.first_name is None:
        raise RuntimeError("First name of private chat must be not empty")
    if user_chat.last_name is not None:
        return f"{user_chat.first_name} {user_chat.last_name}"
    return user_chat.first_name


def user_link(user_chat: User | Chat) -> str:
    return f'<a href="tg://user?id={user_chat.id}">{escape(user_name(user_chat))}</a>'


def chat_key(chat_id: ChatId) -> str:
    return f"chat|{chat_id}"


async def set_chat(bot: Bot, key: str, chat: Chat | None = None) -> None:
    payload: Json | None = cast(Json, chat.to_builtins()) if chat is not None else None
    await bot.storage.set(key, payload)


async def get_chat(bot: Bot, key: str) -> Chat | None:
    data = await bot.storage.get(key)
    if isinstance(data, dict):
        return Chat.convert(data)
    if data is None:
        return None
    raise RuntimeError("Chat data is not dict or None")


async def get_chat_list(bot: Bot) -> list[Chat]:
    chat_list = await bot.storage.get(CHAT_LIST_KEY)
    if chat_list is None:
        raise RuntimeError("Chat list not in storage")
    if not isinstance(chat_list, list) or not all(
        isinstance(item, dict) for item in chat_list
    ):
        raise RuntimeError("Chat list must be list of dicts")
    return [Chat.convert(item) for item in chat_list]


async def set_chat_list(bot: Bot, chat_list: list[Chat]) -> None:
    payload: list[Json] = [cast(Json, chat.to_builtins()) for chat in chat_list]
    await bot.storage.set(CHAT_LIST_KEY, payload)


async def add_chat_to_list(bot: Bot, chat: Chat) -> None:
    settings = bot[SETTINGS_KEY]
    chat_list = await get_chat_list(bot)
    if all(item.id != chat.id for item in chat_list):
        chat_list.append(chat)
        if len(chat_list) > settings.chat_list_size:
            _ = chat_list.pop(0)
        await set_chat_list(bot, chat_list)


async def remove_chat_from_list(bot: Bot, remove_id: ChatId) -> None:
    chat_list = await get_chat_list(bot)
    chat_list = [chat for chat in chat_list if chat.id != remove_id]
    await set_chat_list(bot, chat_list)


async def send_from_message(bot: Bot, chat_id: ChatId, from_chat: Chat) -> None:
    _ = await bot.send_message(
        chat_id,
        f"От {user_link(from_chat)}",
        parse_mode=ParseMode.HTML,
    )


async def reply_menu(bot: Bot, chat_id: ChatId | str) -> None:
    chat_list = await get_chat_list(bot)
    if not chat_list:
        _ = await bot.send_message(chat_id, "Некому отвечать.")
        return
    keyboard = [
        [
            InlineKeyboardButton(
                text=user_name(chat),
                callback_data=f"{REPLY_PREFIX}|{chat.id}",
            )
            for chat in chunk
        ]
        for chunk in chunked(chat_list, 2)
    ]
    _ = await bot.send_message(
        chat_id,
        "Выберите пользователя для ответа.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )


async def get_admin_chat_id(bot: Bot) -> ChatId | None:
    value = await bot.storage.get(ADMIN_CHAT_ID_KEY)
    if value is None:
        return None
    if not isinstance(value, int):
        raise RuntimeError("Admin chat id must be int or None")
    return ChatId(value)


async def set_admin_chat_id(bot: Bot, admin_chat_id: ChatId | None) -> None:
    await bot.storage.set(ADMIN_CHAT_ID_KEY, admin_chat_id)


async def get_wait_reply_from_id(bot: Bot) -> UserId | None:
    value = await bot.storage.get(WAIT_REPLY_FROM_ID_KEY)
    if value is None:
        return None
    if not isinstance(value, int):
        raise RuntimeError("wait reply id must be int or None")
    return UserId(value)


async def set_wait_reply_from_id(bot: Bot, value: UserId | None) -> None:
    await bot.storage.set(WAIT_REPLY_FROM_ID_KEY, value)
