import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from operator import attrgetter
from pathlib import Path
from typing import Dict, Final, List, Optional, Union

import aiojobs
import attr
from aiojobs_protocols import SchedulerProtocol
from aiotgbot import (Bot, BotBlocked, BotUpdate, Chat, InlineKeyboardButton,
                      InlineKeyboardMarkup, Message, ParseMode)
from aiotgbot.api_types import (InputMediaAudio, InputMediaDocument,
                                InputMediaPhoto, InputMediaVideo, User)
from more_itertools import chunked

ALBUM_WAIT_TIMEOUT = 1  # seconds
CHAT_LIST_KEY: Final[str] = 'chat_list'
CHAT_LIST_SIZE_KEY: Final[str] = 'chat_list_size'
ADMIN_USERNAME_KEY: Final[str] = 'admin_username'
REPLY_PREFIX: Final[str] = 'reply'

logger = logging.getLogger('feedback_bot')


def get_software() -> str:
    import aiotgbot.helpers  # isort:skip
    from . import __version__
    return f'{aiotgbot.helpers.get_software()} feedback-bot/{__version__}'


def path(_str: str) -> Path:
    return Path(_str)


def debug() -> bool:
    return __debug__


def user_name(user_chat: Union[User, Chat]) -> str:
    if user_chat.first_name is None:
        raise RuntimeError('First name of private chat must be not empty')
    if user_chat.last_name is not None:
        return f'{user_chat.first_name} {user_chat.last_name}'
    else:
        return user_chat.first_name


def user_link(user_chat: Union[User, Chat]) -> str:
    return f'<a href="tg://user?id={user_chat.id}">{user_name(user_chat)}</a>'


def chat_key(chat_id: int) -> str:
    return f'chat|{chat_id}'


async def set_chat(bot: Bot, key: str, chat: Optional[Chat] = None) -> None:
    await bot.storage.set(key, chat.to_dict() if chat is not None else None)


async def get_chat(bot: Bot, key: str) -> Optional[Chat]:
    data = await bot.storage.get(key)
    if isinstance(data, dict):
        return Chat.from_dict(data)
    elif data is None:
        return None
    else:
        raise RuntimeError('Chat data is not dict or None')


async def get_chat_list(bot: Bot) -> List[Chat]:
    chat_list = await bot.storage.get(CHAT_LIST_KEY)
    if chat_list is None:
        raise RuntimeError('Chat list not in storage')
    assert isinstance(chat_list, list)
    assert all(isinstance(item, dict) for item in chat_list)
    return [Chat.from_dict(item) for item in chat_list]


async def set_chat_list(bot: Bot, chat_list: List[Chat]) -> None:
    await bot.storage.set(
        CHAT_LIST_KEY,
        [chat.to_dict() for chat in chat_list]
    )


async def add_chat_to_list(bot: Bot, chat: Chat) -> None:
    chat_list = await get_chat_list(bot)
    if all(item.id != chat.id for item in chat_list):
        chat_list.append(chat)
        if len(chat_list) > bot[CHAT_LIST_SIZE_KEY]:
            chat_list.pop(0)
        await set_chat_list(bot, chat_list)


async def remove_chat_from_list(bot: Bot, remove_id: int) -> None:
    chat_list = await get_chat_list(bot)
    chat_list = [chat for chat in chat_list if chat.id != remove_id]
    await set_chat_list(bot, chat_list)


async def send_from_message(bot: Bot, chat_id: int, from_chat: Chat) -> None:
    await bot.send_message(chat_id, f'От {user_link(from_chat)}',
                           parse_mode=ParseMode.HTML)


async def reply_menu(bot: Bot, chat_id: Union[int, str]) -> None:
    chat_list = await get_chat_list(bot)
    if len(chat_list) == 0:
        await bot.send_message(chat_id, 'Некому отвечать.')
    else:
        await bot.send_message(
            chat_id, 'Выберите пользователя для ответа.',
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(
                    user_name(chat), callback_data=f'{REPLY_PREFIX}|{chat.id}'
                ) for chat in chunk] for chunk in chunked(chat_list, 2)]
            )
        )


async def send_user_message(bot: Bot, message: Message) -> None:
    assert 'album_forwarder' in bot
    assert isinstance(bot['album_forwarder'], AlbumForwarder)
    current_chat = await get_chat(bot, 'current_chat')
    if current_chat is None and message.media_group_id is not None:
        await bot['album_forwarder'].add_message(message)
        logger.debug('Add next media group item to forwarder')
        return
    if current_chat is None:
        await bot.send_message(message.chat.id, 'Нет текущего пользователя')
        logger.debug('Skip message to user: no current user')
        return

    stopped = await Stopped.get(bot, current_chat.id)
    if stopped is not None:
        await bot.send_message(
            message.chat.id,
            f'{user_link(current_chat)} меня заблокировал '
            f'{stopped.dt:%Y-%m-%d %H:%M:%S %Z}.',
            parse_mode=ParseMode.HTML)
        return

    if message.media_group_id is not None:
        await bot['album_forwarder'].add_message(message, current_chat.id)
        logger.debug('Add first media group item to forwarder')
        return

    logger.debug('Send message to "%s"', current_chat.to_dict())
    try:
        await bot.copy_message(current_chat.id, message.chat.id,
                               message.message_id)
    except BotBlocked:
        await remove_chat_from_list(bot, current_chat.id)
        await Stopped(blocked=True).set(bot, current_chat.id)
        await bot.send_message(
            message.chat.id, f'{user_link(current_chat)} меня заблокировал.',
            parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        logger.info('Blocked by user "%s"', current_chat.to_dict())
        return
    else:
        await bot.send_message(
            message.chat.id,
            f'Сообщение отправлено {user_link(current_chat)}.',
            parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@attr.s(slots=True, frozen=True, auto_attribs=True)
class FromUserFilter:

    async def check(self, bot: Bot, update: BotUpdate) -> bool:  # noqa
        if ADMIN_USERNAME_KEY not in bot:
            raise RuntimeError('Admin username not set')

        return (update.message is not None and
                update.message.from_ is not None and
                update.message.from_.username != bot[ADMIN_USERNAME_KEY])


@attr.s(slots=True, frozen=True, auto_attribs=True)
class FromAdminFilter:

    async def check(self, bot: Bot, update: BotUpdate) -> bool:  # noqa
        if ADMIN_USERNAME_KEY not in bot:
            raise RuntimeError('Admin username not set')

        return (update.message is not None and
                update.message.from_ is not None and
                update.message.from_.username == bot[ADMIN_USERNAME_KEY])


class AlbumForwarder:
    __slots__ = '_queues', '_scheduler', '_bot'

    def __init__(self, bot: Bot) -> None:
        self._queues: Final[Dict[str, asyncio.Queue]] = {}
        self._scheduler: Optional[SchedulerProtocol] = None
        self._bot: Final[Bot] = bot

    async def add_message(self, message: Message,
                          chat_id: Optional[int] = None,
                          add_from_info: bool = False) -> None:
        if self._scheduler is None:
            raise RuntimeError('Album forwarder not started')
        if message.media_group_id is None:
            raise RuntimeError('Message in album must have media_group_id')
        if message.media_group_id in self._queues:
            self._queues[message.media_group_id].put_nowait(message)
        elif chat_id is not None:
            self._queues[message.media_group_id] = asyncio.Queue()
            self._queues[message.media_group_id].put_nowait(message)
            await self._scheduler.spawn(
                self._send(message.media_group_id, chat_id, add_from_info))
        else:
            logger.warning('Skip media group item as latecomer %s', message)

    async def _send(self, media_group_id: str, chat_id: int,
                    add_from_info: bool = False) -> None:
        assert media_group_id in self._queues
        media: List[Union[InputMediaAudio, InputMediaPhoto, InputMediaVideo,
                          InputMediaDocument]] = []
        from_chat: Optional[Chat] = None
        message_count: int = 0
        while True:
            try:
                message = await asyncio.wait_for(
                    self._queues[media_group_id].get(),
                    timeout=ALBUM_WAIT_TIMEOUT)
            except asyncio.TimeoutError:
                break
            assert isinstance(message, Message)
            message_count += 1
            from_chat = message.chat
            if message.audio is not None:
                media.append(InputMediaAudio(
                    media=message.audio.file_id,
                    caption=message.caption,
                    caption_entities=message.caption_entities,
                    duration=message.audio.duration,
                    performer=message.audio.performer,
                    title=message.audio.title
                ))
            elif message.document is not None:
                media.append(InputMediaDocument(
                    media=message.document.file_id,
                    caption=message.caption,
                    caption_entities=message.caption_entities
                ))
            elif message.photo is not None:
                media.append(InputMediaPhoto(
                    media=max(message.photo,
                              key=attrgetter('file_size')).file_id,
                    caption=message.caption,
                    caption_entities=message.caption_entities
                ))
            elif message.video is not None:
                media.append(InputMediaVideo(
                    media=message.video.file_id,
                    caption=message.caption,
                    caption_entities=message.caption_entities,
                    width=message.video.width,
                    height=message.video.height,
                    duration=message.video.duration
                ))
        if len(media) > 0:
            assert from_chat is not None
            if add_from_info:
                await send_from_message(self._bot, chat_id, from_chat)
            await self._bot.send_media_group(chat_id, media)
            await self._bot.send_message(
                from_chat.id, f'Переслано элементов группы: {len(media)}')
            logger.debug('Forwarded %d media group items', len(media))
        elif from_chat is not None:
            await self._bot.send_message(
                from_chat.id, 'Не удалось переслать элементов '
                              f'непоодерживаемого типа: {message_count}')
            logger.debug('Failed to forward %d media group items of '
                         'unsupported type', message_count)
        else:
            logger.debug('No media group items to forward')
        self._queues.pop(media_group_id)

    async def start(self) -> None:
        self._scheduler = await aiojobs.create_scheduler(
            close_timeout=float('inf'),
            exception_handler=self._scheduler_exception_handler)

    async def stop(self) -> None:
        if self._scheduler is None:
            raise RuntimeError('Album forwarder not started')
        await self._scheduler.close()
        assert len(self._queues) == 0

    @staticmethod
    def _scheduler_exception_handler(_, context):
        logger.exception('Album forward error', exc_info=context['exception'])


def _now_with_tz() -> datetime:
    return datetime.now(timezone(timedelta(seconds=-time.timezone)))


@attr.s(slots=True, frozen=True, auto_attribs=True)
class Stopped:
    dt: datetime = attr.ib(factory=_now_with_tz)
    blocked: bool = False

    @staticmethod
    def _key(chat_id: int) -> str:
        return f'stopped|{chat_id}'

    async def set(self, bot: Bot, chat_id: int):
        await bot.storage.set(
            self._key(chat_id),
            {'dt': self.dt.isoformat(), 'error': self.blocked})

    @staticmethod
    async def get(bot: Bot, chat_id: int) -> Optional['Stopped']:
        data = await bot.storage.get(Stopped._key(chat_id))
        if data is not None:
            assert isinstance(data, dict)
            return Stopped(datetime.fromisoformat(data['dt']), data['error'])
        else:
            return None

    @staticmethod
    async def delete(bot: Bot, chat_id: int) -> None:
        await bot.storage.delete(Stopped._key(chat_id))
