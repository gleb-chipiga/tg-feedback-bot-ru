from __future__ import annotations

import asyncio
import logging
import re
import sys
from collections.abc import AsyncIterator, Callable
from textwrap import dedent
from typing import Final, cast

from aiorunner import Runner
from aiotgbot import (
    Bot,
    BotUpdate,
    ContentType,
    GroupChatFilter,
    HandlerTable,
    ParseMode,
    PollBot,
    PrivateChatFilter,
    TelegramError,
)
from aiotgbot.api_types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    ChatId,
    LinkPreviewOptions,
)
from aiotgbot.storage_sqlalchemy import SqlalchemyStorage
from sqlalchemy.ext.asyncio import create_async_engine

from .album_forwarder import (
    ALBUM_FORWARDER_KEY,
    AlbumForwarder,
    send_user_message,
)
from .filters import FromAdminFilter, FromUserFilter
from .helpers import (
    CHAT_LIST_KEY,
    REPLY_PREFIX,
    add_chat_to_list,
    chat_key,
    get_admin_chat_id,
    get_chat,
    get_software,
    get_wait_reply_from_id,
    remove_chat_from_list,
    reply_menu,
    send_from_message,
    set_admin_chat_id,
    set_chat,
    set_wait_reply_from_id,
    user_link,
)
from .settings import SETTINGS_KEY, Settings
from .stopped import Stopped
from .storage_keys import (
    ADMIN_CHAT_ID_KEY,
    CURRENT_CHAT_KEY,
    GROUP_CHAT_KEY,
    WAIT_REPLY_FROM_ID_KEY,
)

SOFTWARE: Final[str] = get_software()
USER_COMMANDS: Final[tuple[BotCommand, ...]] = (
    BotCommand(command="help", description="Помощь"),
    BotCommand(
        command="stop",
        description="Остановить и не получать больше сообщения",
    ),
)
ADMIN_COMMANDS: Final[tuple[BotCommand, ...]] = (
    BotCommand(command="help", description="Помощь"),
    BotCommand(command="reply", description="Ответить пользователю"),
    BotCommand(command="add_to_group", description="Добавить в группу"),
    BotCommand(command="remove_from_group", description="Удалить из группы"),
    BotCommand(command="reset", description="Сбросить состояние"),
)
GROUP_COMMANDS: Final[tuple[BotCommand, ...]] = (
    BotCommand(command="help", description="Помощь"),
    BotCommand(command="reply", description="Ответить пользователю"),
)
CHAT_ID_GROUP: Final[str] = "chat_id"
REPLY_RXP: Final[re.Pattern[str]] = re.compile(
    rf"^{REPLY_PREFIX}\|(?P<{CHAT_ID_GROUP}>-?\d+)$"
)
TZ_KEY: Final[str] = "TZ"


logger = logging.getLogger("feedback-bot")
handlers = HandlerTable()


@handlers.message(commands=["start"], filters=[PrivateChatFilter(), FromUserFilter()])
async def user_start_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Start command from "%s"', update.message.from_.to_builtins())
    stopped = await Stopped.get(bot, update.message.chat.id)
    if stopped is not None:
        await Stopped.delete(bot, update.message.chat.id)
        _ = await bot.send_message(update.message.chat.id, "С возвращением!")
    await set_chat(bot, chat_key(update.message.chat.id), update.message.chat)
    _ = await bot.send_message(
        update.message.chat.id,
        dedent(
            """\
            Пришлите сообщение или задайте вопрос.

            Также вы можете использовать следующие команды:
            /help — Помощь
            /stop — Остановить и не получать больше сообщения"""
        ),
    )


@handlers.message(commands=["help"], filters=[PrivateChatFilter(), FromUserFilter()])
async def user_help_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Help command from "%s"', update.message.from_.to_builtins())
    _ = await bot.send_message(
        update.message.chat.id,
        dedent(
            """\
            Пришлите сообщение или задайте вопрос.

            Также вы можете использовать следующие команды:
            /help — Помощь
            /stop — Остановить и не получать больше сообщения"""
        ),
    )


@handlers.message(commands=["stop"], filters=[PrivateChatFilter(), FromUserFilter()])
async def user_stop_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    user_chat_id = ChatId(update.message.from_.id)
    logger.info('Stop command from "%s"', update.message.from_.to_builtins())
    stopped = Stopped()
    await stopped.set(bot, user_chat_id)
    await remove_chat_from_list(bot, user_chat_id)
    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    if group_chat is None:
        notify_chat_id = await get_admin_chat_id(bot)
        if notify_chat_id is None:
            logger.error("Admin chat id is not set")
            return
    else:
        notify_chat_id = group_chat.id
    blocked_text = (
        f"{user_link(update.message.from_)} меня заблокировал "
        f"{stopped.date_time:%Y-%m-%d %H:%M:%S %Z}."
    )
    _ = await bot.send_message(
        notify_chat_id,
        blocked_text,
        parse_mode=ParseMode.HTML,
    )
    current_chat = await get_chat(bot, CURRENT_CHAT_KEY)
    if current_chat is not None and ChatId(current_chat.id) == user_chat_id:
        await set_wait_reply_from_id(bot, None)
        await set_chat(bot, CURRENT_CHAT_KEY)


@handlers.message(commands=["start"], filters=[PrivateChatFilter(), FromAdminFilter()])
async def admin_start_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    logger.info("Start command from admin")
    await set_admin_chat_id(bot, update.message.chat.id)

    _ = await bot.send_message(
        update.message.chat.id,
        dedent(
            """\
            /help — Помощь
            /reply — Ответить пользователю
            /add_to_group — Добавить в группу
            /remove_from_group — Удалить из группы
            /reset — Сбросить состояние"""
        ),
    )


@handlers.message(commands=["help"], filters=[PrivateChatFilter(), FromAdminFilter()])
async def admin_help_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    logger.info("Help command from admin")
    _ = await bot.send_message(
        update.message.chat.id,
        dedent(
            """\
            /help — Помощь
            /reply — Ответить пользователю
            /add_to_group — Добавить в группу
            /remove_from_group — Удалить из группы
            /reset — Сбросить состояние"""
        ),
    )


@handlers.message(commands=["reset"], filters=[PrivateChatFilter(), FromAdminFilter()])
async def admin_reset_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    logger.info("Reset command from admin")
    await set_wait_reply_from_id(bot, None)
    await bot.storage.set(CURRENT_CHAT_KEY)
    _ = await bot.send_message(update.message.chat.id, "Состояние сброшено.")


@handlers.message(
    commands=["add_to_group"], filters=[PrivateChatFilter(), FromAdminFilter()]
)
async def add_to_group_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Add to group command from "%s"', update.message.from_.to_builtins())
    if await get_chat(bot, GROUP_CHAT_KEY) is not None:
        _ = await bot.send_message(update.message.chat.id, "Уже в группе.")
        return
    bot_username = (await bot.get_me()).username
    link = f"tg://resolve?domain={bot_username}&startgroup=startgroup"
    _ = await bot.send_message(
        update.message.chat.id,
        f'Для добавления в группу <a href="{link}">перейдите по ссылке</a>.',
        parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


@handlers.message(
    commands=["remove_from_group"], filters=[PrivateChatFilter(), FromAdminFilter()]
)
async def remove_from_group_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info(
        'Remove from group command from "%s"', update.message.from_.to_builtins()
    )
    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    if group_chat is None:
        _ = await bot.send_message(update.message.chat.id, "Не в группе.")
        return
    try:
        _ = await bot.leave_chat(group_chat.id)
    except TelegramError as exception:
        logger.error('Leave chat error "%s"', exception)
    _ = await bot.send_message(
        update.message.chat.id,
        f"Удален из группы <b>{group_chat.title}</b>.",
        parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    await set_chat(bot, GROUP_CHAT_KEY)
    await set_chat(bot, CURRENT_CHAT_KEY)


@handlers.message(commands=["start"], filters=[GroupChatFilter(), FromAdminFilter()])
async def group_start_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Start in group command from "%s"', update.message.from_.to_builtins())
    if await get_chat(bot, GROUP_CHAT_KEY):
        logger.info('Attempt start in group "%s"', update.message.chat.to_builtins())
        return

    await set_chat(bot, GROUP_CHAT_KEY, update.message.chat)
    await set_chat(bot, CURRENT_CHAT_KEY)

    _ = await bot.set_my_commands(
        GROUP_COMMANDS,
        BotCommandScopeChat(chat_id=update.message.chat.id),
    )

    admin_chat_id = await get_admin_chat_id(bot)
    if admin_chat_id is None:
        _ = await bot.send_message(
            ChatId(update.message.from_.id), "Что-то сломалось внутри бота."
        )
        logger.error("Admin user id not set")
        return

    _ = await bot.send_message(
        admin_chat_id,
        f"Запущен в <b>{update.message.chat.title}</b>.",
        parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    logger.info('Started in group "%s"', update.message.chat.to_builtins())


@handlers.message(commands=["help"], filters=[GroupChatFilter()])
async def group_help_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Help message in group from "%s"', update.message.from_.to_builtins())
    _ = await bot.send_message(
        update.message.chat.id,
        dedent(
            """\
            /help — Помощь
            /reply — Ответить пользователю"""
        ),
    )


@handlers.message(commands=["reply"], filters=[PrivateChatFilter(), FromAdminFilter()])
async def admin_reply_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Reply command from admin "%s"', update.message.from_.to_builtins())
    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    if group_chat is not None:
        _ = await bot.send_message(
            update.message.chat.id,
            f"Принимаю сообщения в группе <b>{group_chat.title}</b>.",
            parse_mode=ParseMode.HTML,
        )
        logger.debug("Ignore reply command in private chat")
        return
    if await get_wait_reply_from_id(bot) is not None:
        _ = await bot.send_message(update.message.chat.id, "Уже жду сообщение.")
        logger.debug("Already wait message. Ignore command")
        return
    await reply_menu(bot, update.message.chat.id)


@handlers.message(commands=["reply"], filters=[GroupChatFilter()])
async def group_reply_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Reply in group command from "%s"', update.message.from_.to_builtins())
    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    if group_chat is not None and group_chat.id != update.message.chat.id:
        _ = await bot.leave_chat(update.message.chat.id)
        return
    if group_chat is None:
        _ = await bot.send_message(update.message.chat.id, "Не принимаю сообщения.")
        logger.debug("Ignore reply command in group")
        return
    wait_reply_from_id = await get_wait_reply_from_id(bot)
    if wait_reply_from_id is not None:
        member = await bot.get_chat_member(update.message.chat.id, wait_reply_from_id)
        member_link = (
            user_link(member.user)
            if member.user.username is None
            else f"@{member.user.username}"
        )
        _ = await bot.send_message(
            update.message.chat.id,
            f"Уже жду сообщение от {member_link}.",
            parse_mode=ParseMode.HTML,
        )
        logger.debug("Already wait message. Ignore command")
        return

    await reply_menu(bot, update.message.chat.id)


@handlers.message(content_types=[ContentType.NEW_CHAT_MEMBERS])
async def group_new_members(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.new_chat_members is not None
    logger.info('New group members message "%s"', update.message.chat)
    me = await bot.get_me()
    admin_chat_id = await get_admin_chat_id(bot)
    if admin_chat_id is None:
        logger.error("Admin user id not set")
        return
    for user in update.message.new_chat_members:
        if user.id == me.id:
            _ = await bot.send_message(
                admin_chat_id,
                f"Добавлен в группу <b>{update.message.chat.title}</b>.",
                parse_mode=ParseMode.HTML,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
            logger.info('Bot added to group "%s"', update.message.chat.to_builtins())
            group_chat = await get_chat(bot, GROUP_CHAT_KEY)
            if group_chat is not None and group_chat.id != update.message.chat.id:
                _ = await bot.leave_chat(update.message.chat.id)
            elif group_chat is not None and group_chat.id == update.message.chat.id:
                _ = await bot.set_my_commands(
                    GROUP_COMMANDS,
                    BotCommandScopeChat(chat_id=update.message.chat.id),
                )
            break


@handlers.message(content_types=[ContentType.LEFT_CHAT_MEMBER])
async def group_left_member(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.left_chat_member is not None
    logger.info('Left group member message "%s"', update.message.to_builtins())
    me = await bot.get_me()
    admin_chat_id = await get_admin_chat_id(bot)
    if admin_chat_id is None:
        logger.error("Admin user id not set")
        return
    if update.message.left_chat_member.id == me.id:
        _ = await bot.send_message(
            admin_chat_id,
            f"Вышел из группы <b>{update.message.chat.title}</b>.",
            parse_mode=ParseMode.HTML,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        logger.info('Leave chat "%s"', update.message.chat.title)
        group_chat = await get_chat(bot, GROUP_CHAT_KEY)
        if group_chat is not None and update.message.chat.id == group_chat.id:
            await set_chat(bot, GROUP_CHAT_KEY)
            logger.info('Forget chat "%s"', update.message.chat.title)


@handlers.message(filters=[PrivateChatFilter(), FromUserFilter()])
async def user_message(bot: Bot, update: BotUpdate) -> None:
    album_forwarder = bot[ALBUM_FORWARDER_KEY]
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Message from "%s"', update.message.from_.to_builtins())
    await set_chat(bot, chat_key(update.message.chat.id), update.message.chat)
    stopped = await Stopped.get(bot, update.message.chat.id)
    if stopped is not None:
        await Stopped.delete(bot, update.message.chat.id)
        _ = await bot.send_message(update.message.chat.id, "С возвращением!")

    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    if group_chat is None:
        forward_chat_id = await get_admin_chat_id(bot)
        if forward_chat_id is None:
            _ = await bot.send_message(
                ChatId(update.message.from_.id), "Что-то сломалось внутри бота."
            )
            logger.error("Admin user id not set")
            return
    else:
        forward_chat_id = group_chat.id

    if update.message.audio is not None or update.message.sticker is not None:
        await send_from_message(bot, forward_chat_id, update.message.chat)

    if update.message.media_group_id is not None:
        await album_forwarder.add_message(
            update.message,
            forward_chat_id,
            add_from_info=True,
        )
    else:
        _ = await bot.forward_message(
            forward_chat_id,
            update.message.chat.id,
            update.message.message_id,
        )

    await add_chat_to_list(bot, update.message.chat)


@handlers.message(filters=[GroupChatFilter()])
async def group_message(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Reply message in group from "%s"', update.message.from_.to_builtins())
    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    if group_chat is not None and group_chat.id != update.message.chat.id:
        _ = await bot.leave_chat(update.message.chat.id)
        return
    wait_reply_from_id = await get_wait_reply_from_id(bot)
    if (
        wait_reply_from_id != update.message.from_.id
        and update.message.media_group_id is None
    ):
        logger.info(
            'Ignore message from group "%s" user "%s"',
            update.message.chat.title,
            update.message.from_.to_builtins(),
        )
        return

    await send_user_message(bot, update.message)
    await set_wait_reply_from_id(bot, None)
    await set_chat(bot, CURRENT_CHAT_KEY)


@handlers.message(filters=[PrivateChatFilter(), FromAdminFilter()])
async def admin_message(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    logger.info('Message from admin "%s"', update.message.to_builtins())
    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    if group_chat is not None:
        _ = await bot.send_message(
            update.message.chat.id,
            f"Принимаю сообщения в группе <b>{group_chat.title}</b>.",
            parse_mode=ParseMode.HTML,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        logger.info("Ignore message in private chat with admin")
        return
    wait_reply_from_id = await get_wait_reply_from_id(bot)
    if wait_reply_from_id is None and update.message.media_group_id is None:
        logger.info("Ignore message from admin")
        return

    await send_user_message(bot, update.message)
    await set_wait_reply_from_id(bot, None)
    await set_chat(bot, CURRENT_CHAT_KEY)


@handlers.callback_query(data_match=REPLY_RXP)
async def reply_callback(bot: Bot, update: BotUpdate) -> None:
    assert update.callback_query is not None
    assert update.callback_query.data is not None
    assert update.callback_query.message is not None
    logger.info(
        'Reply callback query from "%s"', update.callback_query.from_.to_builtins()
    )
    _ = await bot.answer_callback_query(update.callback_query.id)

    data_match = REPLY_RXP.match(update.callback_query.data)
    assert data_match is not None, "Reply data must match format"
    current_chat_id = ChatId(int(data_match.group(CHAT_ID_GROUP)))
    current_chat = await get_chat(bot, chat_key(current_chat_id))
    if current_chat is None:
        _ = await bot.edit_message_text(
            "Ошибка. Сообщение не отправить.",
            chat_id=update.callback_query.message.chat.id,
            message_id=update.callback_query.message.message_id,
        )
        logger.info(
            'Skip message sending to unknown user from "%s"',
            update.callback_query.from_.to_builtins(),
        )
        return
    stopped = await Stopped.get(bot, current_chat_id)
    if stopped is not None:
        blocked_text = (
            f"{user_link(current_chat)} меня заблокировал "
            f"{stopped.date_time:%Y-%m-%d %H:%M:%S %Z}."
        )
        _ = await bot.edit_message_text(
            blocked_text,
            chat_id=update.callback_query.message.chat.id,
            message_id=update.callback_query.message.message_id,
            parse_mode=ParseMode.HTML,
        )
        return
    await set_wait_reply_from_id(bot, update.callback_query.from_.id)
    await set_chat(bot, CURRENT_CHAT_KEY, current_chat)
    _ = await bot.edit_message_text(
        f"Введите сообщение для {user_link(current_chat)}.",
        chat_id=update.callback_query.message.chat.id,
        message_id=update.callback_query.message.message_id,
        parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


async def run_context(_: Runner, settings: Settings) -> AsyncIterator[None]:
    if __debug__:
        asyncio.get_running_loop().slow_callback_duration = 0.01

    engine = create_async_engine(str(settings.postgres_dsn))
    storage = SqlalchemyStorage(engine)
    await storage.connect()
    if await storage.get(CHAT_LIST_KEY) is None:
        await storage.set(CHAT_LIST_KEY, [])
    if await storage.get(CURRENT_CHAT_KEY) is None:
        await storage.set(CURRENT_CHAT_KEY)
    if await storage.get(ADMIN_CHAT_ID_KEY) is None:
        await storage.set(ADMIN_CHAT_ID_KEY)
    if await storage.get(GROUP_CHAT_KEY) is None:
        await storage.set(GROUP_CHAT_KEY)
    if await storage.get(WAIT_REPLY_FROM_ID_KEY) is None:
        await storage.set(WAIT_REPLY_FROM_ID_KEY)

    handlers.freeze()
    bot = PollBot(settings.tg_token.get_secret_value(), handlers, storage)
    bot[SETTINGS_KEY] = settings
    await bot.start()

    album_forwarder = AlbumForwarder(bot)
    await album_forwarder.start()
    bot[ALBUM_FORWARDER_KEY] = album_forwarder

    admin_chat_id = await get_admin_chat_id(bot)
    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    __ = await bot.delete_my_commands()
    __ = await bot.set_my_commands(USER_COMMANDS, BotCommandScopeAllPrivateChats())
    if admin_chat_id is not None:
        __ = await bot.set_my_commands(
            ADMIN_COMMANDS,
            BotCommandScopeChat(chat_id=admin_chat_id),
        )
    if group_chat is not None:
        try:
            __ = await bot.set_my_commands(
                GROUP_COMMANDS,
                BotCommandScopeChat(chat_id=group_chat.id),
            )
        except TelegramError as exception:
            if exception.error_code == 403:
                logger.info(
                    'Can\'t set commands in chat "%s": %s',
                    group_chat.id,
                    exception.description,
                )
            else:
                raise

    yield

    await album_forwarder.stop()
    await bot.stop()
    await engine.dispose()


def setup_logging() -> None:
    import os

    log_format = "%(asctime)s %(name)s %(levelname)s: %(message)s"
    if __debug__:
        logging.basicConfig(level=logging.DEBUG, format=log_format)
        logging.getLogger("asyncio").setLevel(logging.ERROR)
        logging.getLogger("aiosqlite").setLevel(logging.INFO)
    else:
        logging.basicConfig(level=logging.INFO, format=log_format)
    logger.info("PYTHONOPTIMIZE=%s", os.environ.get("PYTHONOPTIMIZE"))
    logger.info(SOFTWARE)


def main() -> None:
    import os  # isort: skip
    import uvloop

    if TZ_KEY not in os.environ:
        sys.exit("Env var TZ is not set")
    settings = Settings()  # pyright: ignore[reportCallIssue]

    setup_logging()
    install_uvloop = cast(Callable[[], None], uvloop.install)
    install_uvloop()
    runner = Runner(
        run_context,
        debug=__debug__,
        settings=settings,
    )
    runner.run()


if __name__ == "__main__":  # pragma: nocover
    main()
