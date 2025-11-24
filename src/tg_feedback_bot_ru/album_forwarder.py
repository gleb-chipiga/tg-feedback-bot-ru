from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from operator import attrgetter
from typing import Final

from aiojobs import Scheduler
from aiotgbot import Bot, BotBlocked, Chat, Message, ParseMode
from aiotgbot.api_types import (
    ChatId,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    LinkPreviewOptions,
)
from aiotgbot.helpers import BotKey

from .helpers import get_chat, remove_chat_from_list, send_from_message, user_link
from .stopped import Stopped
from .storage_keys import CURRENT_CHAT_KEY

__all__ = ("ALBUM_FORWARDER_KEY", "AlbumForwarder", "send_user_message")

ALBUM_WAIT_TIMEOUT = 1  # seconds

logger = logging.getLogger("feedback-bot")


class AlbumForwarder:
    __slots__: tuple[str, ...] = ("_bot", "_queues", "_scheduler")

    def __init__(self, bot: Bot) -> None:
        self._bot: Final[Bot] = bot
        self._queues: Final[dict[str, asyncio.Queue[Message]]] = {}
        self._scheduler: Scheduler | None = None

    async def add_message(
        self,
        message: Message,
        chat_id: ChatId | None = None,
        add_from_info: bool = False,
    ) -> None:
        if self._scheduler is None:
            raise RuntimeError("Album forwarder not started")
        if message.media_group_id is None:
            raise RuntimeError("Message in album must have media_group_id")
        if message.media_group_id in self._queues:
            self._queues[message.media_group_id].put_nowait(message)
            return
        if chat_id is None:
            logger.warning("Skip media group item as latecomer %s", message)
            return
        queue: asyncio.Queue[Message] = asyncio.Queue()
        queue.put_nowait(message)
        self._queues[message.media_group_id] = queue
        _ = await self._scheduler.spawn(
            self._send(message.media_group_id, chat_id, add_from_info)
        )

    async def _send(
        self,
        media_group_id: str,
        chat_id: ChatId,
        add_from_info: bool = False,
    ) -> None:
        assert media_group_id in self._queues
        media: list[
            InputMediaAudio | InputMediaPhoto | InputMediaVideo | InputMediaDocument
        ] = []
        from_chat: Chat | None = None
        message_count = 0
        queue = self._queues[media_group_id]
        while True:
            try:
                message = await asyncio.wait_for(
                    queue.get(), timeout=ALBUM_WAIT_TIMEOUT
                )
            except TimeoutError:
                break
            assert isinstance(message, Message)
            message_count += 1
            from_chat = message.chat
            if message.audio is not None:
                media.append(
                    InputMediaAudio(
                        media=message.audio.file_id,
                        caption=message.caption,
                        caption_entities=message.caption_entities,
                        duration=message.audio.duration,
                        performer=message.audio.performer,
                        title=message.audio.title,
                    )
                )
            elif message.document is not None:
                media.append(
                    InputMediaDocument(
                        media=message.document.file_id,
                        caption=message.caption,
                        caption_entities=message.caption_entities,
                    )
                )
            elif message.photo is not None:
                media.append(
                    InputMediaPhoto(
                        media=max(message.photo, key=attrgetter("file_size")).file_id,
                        caption=message.caption,
                        caption_entities=message.caption_entities,
                    )
                )
            elif message.video is not None:
                media.append(
                    InputMediaVideo(
                        media=message.video.file_id,
                        caption=message.caption,
                        caption_entities=message.caption_entities,
                        width=message.video.width,
                        height=message.video.height,
                        duration=message.video.duration,
                    )
                )
        if media:
            assert from_chat is not None
            if add_from_info:
                await send_from_message(self._bot, chat_id, from_chat)
            _ = await self._bot.send_media_group(chat_id, media)
            _ = await self._bot.send_message(
                from_chat.id,
                f"Переслано элементов группы: {len(media)}",
            )
            logger.debug("Forwarded %d media group items", len(media))
        elif from_chat is not None:
            message_text = (
                "Не удалось переслать элементов неподдерживаемого типа: "
                + f"{message_count}"
            )
            _ = await self._bot.send_message(
                from_chat.id,
                message_text,
            )
            logger.debug(
                "Failed to forward %d media group items of unsupported type",
                message_count,
            )
        else:
            logger.debug("No media group items to forward")
        _ = self._queues.pop(media_group_id, None)

    async def start(self) -> None:
        self._scheduler = Scheduler(
            close_timeout=float("inf"),
            exception_handler=self._scheduler_exception_handler,
        )

    async def stop(self) -> None:
        if self._scheduler is None:
            raise RuntimeError("Album forwarder not started")
        await self._scheduler.close()
        assert not self._queues

    @staticmethod
    def _scheduler_exception_handler(
        _: Scheduler, context: Mapping[str, object]
    ) -> None:
        exception = context.get("exception")
        if isinstance(exception, BaseException):
            logger.exception("Album forward error", exc_info=exception)
        else:
            logger.exception("Album forward error (no exception in context)")


ALBUM_FORWARDER_KEY: Final = BotKey("album_forwarder", AlbumForwarder)


async def send_user_message(bot: Bot, message: Message) -> None:
    current_chat = await get_chat(bot, CURRENT_CHAT_KEY)
    if current_chat is None and message.media_group_id is not None:
        await bot[ALBUM_FORWARDER_KEY].add_message(message)
        logger.debug("Add next media group item to forwarder")
        return
    if current_chat is None:
        _ = await bot.send_message(message.chat.id, "Нет текущего пользователя")
        logger.debug("Skip message to user: no current user")
        return

    stopped = await Stopped.get(bot, current_chat.id)
    if stopped is not None:
        blocked_text = (
            f"{user_link(current_chat)} меня заблокировал "
            f"{stopped.date_time:%Y-%m-%d %H:%M:%S %Z}."
        )
        _ = await bot.send_message(
            message.chat.id,
            blocked_text,
            parse_mode=ParseMode.HTML,
        )
        return

    if message.media_group_id is not None:
        await bot[ALBUM_FORWARDER_KEY].add_message(message, current_chat.id)
        logger.debug("Add first media group item to forwarder")
        return

    logger.debug('Send message to "%s"', current_chat.to_builtins())
    try:
        _ = await bot.copy_message(
            current_chat.id,
            message.chat.id,
            message.message_id,
        )
    except BotBlocked:
        await remove_chat_from_list(bot, current_chat.id)
        await Stopped(blocked=True).set(bot, current_chat.id)
        _ = await bot.send_message(
            message.chat.id,
            f"{user_link(current_chat)} меня заблокировал.",
            parse_mode=ParseMode.HTML,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        logger.info('Blocked by user "%s"', current_chat.to_builtins())
        return
    _ = await bot.send_message(
        message.chat.id,
        f"Сообщение отправлено {user_link(current_chat)}.",
        parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
