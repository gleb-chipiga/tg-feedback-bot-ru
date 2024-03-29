import asyncio
import logging
import re
from pathlib import Path
from typing import AsyncIterator, Final

import msgspec
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
    LinkPreviewOptions,
)
from aiotgbot.storage_sqlite import SQLiteStorage

from .helpers import (
    CHAT_LIST_KEY,
    REPLY_PREFIX,
    AlbumForwarder,
    FromAdminFilter,
    FromUserFilter,
    Stopped,
    add_chat_to_list,
    chat_key,
    debug,
    get_chat,
    get_software,
    path,
    remove_chat_from_list,
    reply_menu,
    send_from_message,
    send_user_message,
    set_chat,
    user_link,
)
from .settings import SETTINGS_KEY, Settings

SOFTWARE: Final[str] = get_software()
USER_COMMANDS: Final[tuple[BotCommand, ...]] = (
    BotCommand(command="help", description="Помощь"),
    BotCommand(
        command="stop", description="Остановить и не получать больше сообщения"
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
REPLY_RXP: "Final[re.Pattern[str]]" = re.compile(
    rf"^{REPLY_PREFIX}\|(?P<{CHAT_ID_GROUP}>-?\d+)$"
)
ALBUM_FORWARDER_KEY: Final[str] = "album_forwarder"
GROUP_CHAT_KEY: Final[str] = "group_chat"
ADMIN_CHAT_ID_KEY: Final[str] = "admin_chat_id"
CURRENT_CHAT_KEY: Final[str] = "current_chat"
WAIT_REPLY_FROM_ID_KEY: Final[str] = "wait_reply_from_id"
TZ_KEY: Final["str"] = "TZ"


logger = logging.getLogger("feedback-bot")
handlers = HandlerTable()


@handlers.message(
    commands=["start"], filters=[PrivateChatFilter(), FromUserFilter()]
)
async def user_start_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info(
        'Start command from "%s"',
        msgspec.to_builtins(update.message.from_),
    )
    stopped = await Stopped.get(bot, update.message.chat.id)
    if stopped is not None:
        await Stopped.delete(bot, update.message.chat.id)
        await bot.send_message(update.message.chat.id, "С возвращением!")
    await set_chat(bot, chat_key(update.message.chat.id), update.message.chat)
    await bot.send_message(
        update.message.chat.id,
        "Пришлите сообщение или задайте вопрос. "
        "Также вы можете использовать следующие команды:\n"
        "/help — Помощь\n"
        "/stop — Остановить и не получать больше сообщения",
    )


@handlers.message(
    commands=["help"], filters=[PrivateChatFilter(), FromUserFilter()]
)
async def user_help_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info(
        'Help command from "%s"',
        msgspec.to_builtins(update.message.from_),
    )
    await bot.send_message(
        update.message.chat.id,
        "Пришлите сообщение или задайте вопрос. "
        "Также вы можете использовать следующие команды:\n"
        "/help — Помощь\n"
        "/stop — Остановить и не получать больше сообщения",
    )


@handlers.message(
    commands=["stop"], filters=[PrivateChatFilter(), FromUserFilter()]
)
async def user_stop_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info(
        'Stop command from "%s"',
        msgspec.to_builtins(update.message.from_),
    )
    stopped = Stopped()
    await stopped.set(bot, update.message.from_.id)
    await remove_chat_from_list(bot, update.message.from_.id)
    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    if group_chat is None:
        notify_chat_id = await bot.storage.get(ADMIN_CHAT_ID_KEY)
        assert isinstance(notify_chat_id, int)
    else:
        notify_chat_id = group_chat.id
    await bot.send_message(
        notify_chat_id,
        f"{user_link(update.message.from_)} меня заблокировал "
        f"{stopped.dt:%Y-%m-%d %H:%M:%S %Z}.",
        parse_mode=ParseMode.HTML,
    )
    current_chat = await get_chat(bot, CURRENT_CHAT_KEY)
    if current_chat is not None and current_chat.id == update.message.from_.id:
        await bot.storage.set(WAIT_REPLY_FROM_ID_KEY)
        await set_chat(bot, CURRENT_CHAT_KEY)


@handlers.message(
    commands=["start"], filters=[PrivateChatFilter(), FromAdminFilter()]
)
async def admin_start_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    logger.info("Start command from admin")
    await bot.storage.set(ADMIN_CHAT_ID_KEY, update.message.chat.id)

    await bot.send_message(
        update.message.chat.id,
        "/help — Помощь\n"
        "/reply — Ответить пользователю\n"
        "/add_to_group — Добавить в группу\n"
        "/remove_from_group — Удалить из группы\n"
        "/reset — Сбросить состояние",
    )


@handlers.message(
    commands=["help"], filters=[PrivateChatFilter(), FromAdminFilter()]
)
async def admin_help_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    logger.info("Help command from admin")
    await bot.send_message(
        update.message.chat.id,
        "/help — Помощь\n"
        "/reply — Ответить пользователю\n"
        "/add_to_group — Добавить в группу\n"
        "/remove_from_group — Удалить из группы\n"
        "/reset — Сбросить состояние",
    )


@handlers.message(
    commands=["reset"], filters=[PrivateChatFilter(), FromAdminFilter()]
)
async def admin_reset_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    logger.info("Reset command from admin")
    await bot.storage.set(WAIT_REPLY_FROM_ID_KEY)
    await bot.storage.set(CURRENT_CHAT_KEY)
    await bot.send_message(update.message.chat.id, "Состояние сброшено.")


@handlers.message(
    commands=["add_to_group"], filters=[PrivateChatFilter(), FromAdminFilter()]
)
async def add_to_group_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None

    logger.info(
        'Add to group command from "%s"',
        msgspec.to_builtins(update.message.from_),
    )
    if await get_chat(bot, GROUP_CHAT_KEY) is not None:
        logger.info("Already in group. Ignore command")
        await bot.send_message(update.message.chat.id, "Уже в группе.")
        return

    bot_username = (await bot.get_me()).username
    link = f"tg://resolve?domain={bot_username}&startgroup=startgroup"
    await bot.send_message(
        update.message.chat.id,
        f'Для добавления в группу <a href="{link}">перейдите по ссылке</a>.',
        parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


@handlers.message(
    commands=["remove_from_group"],
    filters=[PrivateChatFilter(), FromAdminFilter()],
)
async def remove_from_group_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info(
        'Remove from group command from "%s"',
        msgspec.to_builtins(update.message.from_),
    )
    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    if group_chat is None:
        logger.info("Not in group. Ignore command")
        await bot.send_message(update.message.chat.id, "Не в группе.")
        return

    try:
        await bot.leave_chat(group_chat.id)
    except TelegramError as exception:
        logger.error('Leave chat error "%s"', exception)

    await bot.send_message(
        update.message.chat.id,
        f"Удален из группы <b>{group_chat.title}</b>.",
        parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )

    await set_chat(bot, GROUP_CHAT_KEY)
    await set_chat(bot, CURRENT_CHAT_KEY)

    logger.info(
        'Removed from group "%s"',
        msgspec.to_builtins(group_chat),
    )


@handlers.message(
    commands=["start"], filters=[GroupChatFilter(), FromAdminFilter()]
)
async def group_start_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info(
        'Start in group command from "%s"',
        msgspec.to_builtins(update.message.from_),
    )
    if await get_chat(bot, GROUP_CHAT_KEY):
        logger.info(
            'Attempt start in group "%s"',
            msgspec.to_builtins(update.message.chat),
        )
        return

    await set_chat(bot, GROUP_CHAT_KEY, update.message.chat)
    await set_chat(bot, CURRENT_CHAT_KEY)

    await bot.set_my_commands(
        GROUP_COMMANDS, BotCommandScopeChat(chat_id=update.message.chat.id)
    )

    admin_chat_id = await bot.storage.get(ADMIN_CHAT_ID_KEY)
    assert isinstance(admin_chat_id, int)
    await bot.send_message(
        admin_chat_id,
        f"Запущен в <b>{update.message.chat.title}</b>.",
        parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )

    logger.info(
        'Started in group "%s"',
        msgspec.to_builtins(update.message.chat),
    )


@handlers.message(commands=["help"], filters=[GroupChatFilter()])
async def group_help_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info(
        'Help message in group from "%s"',
        msgspec.to_builtins(update.message.from_),
    )
    await bot.send_message(
        update.message.chat.id,
        "/help — Помощь\n" "/reply — Ответить пользователю",
    )


@handlers.message(
    commands=["reply"], filters=[PrivateChatFilter(), FromAdminFilter()]
)
async def admin_reply_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info(
        'Reply command from admin "%s"',
        msgspec.to_builtins(update.message.from_),
    )
    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    if group_chat is not None:
        await bot.send_message(
            update.message.chat.id,
            f"Принимаю сообщения в группе <b>{group_chat.title}</b>.",
            parse_mode=ParseMode.HTML,
        )
        logger.debug("Ignore reply command in private chat")
        return
    if await bot.storage.get(WAIT_REPLY_FROM_ID_KEY) is not None:
        await bot.send_message(update.message.chat.id, "Уже жду сообщение.")
        logger.debug("Already wait message. Ignore command")
        return

    await reply_menu(bot, update.message.chat.id)


@handlers.message(commands=["reply"], filters=[GroupChatFilter()])
async def group_reply_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info(
        'Reply in group command from "%s"',
        msgspec.to_builtins(update.message.from_),
    )
    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    if group_chat is not None and group_chat.id != update.message.chat.id:
        await bot.leave_chat(update.message.chat.id)
        return
    if group_chat is None:
        await bot.send_message(
            update.message.chat.id, "Не принимаю сообщения."
        )
        logger.debug("Ignore reply command in group")
        return
    wait_reply_from_id = await bot.storage.get(WAIT_REPLY_FROM_ID_KEY)
    if wait_reply_from_id is not None:
        assert isinstance(wait_reply_from_id, int)
        member = await bot.get_chat_member(
            update.message.chat.id, wait_reply_from_id
        )
        member_link = (
            user_link(member.user)
            if member.user.username is None
            else f"@{member.user.username}"
        )
        await bot.send_message(
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
    for user in update.message.new_chat_members:
        if user.id == me.id:
            admin_chat_id = await bot.storage.get(ADMIN_CHAT_ID_KEY)
            assert isinstance(admin_chat_id, int)
            await bot.send_message(
                admin_chat_id,
                f"Добавлен в группу <b>{update.message.chat.title}</b>.",
                parse_mode=ParseMode.HTML,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
            logger.info(
                'Bot added to grouip "%s"',
                msgspec.to_builtins(update.message.chat),
            )
            group_chat = await get_chat(bot, GROUP_CHAT_KEY)
            if (
                group_chat is not None
                and group_chat.id != update.message.chat.id
            ):
                await bot.leave_chat(update.message.chat.id)
            elif (
                group_chat is not None
                and group_chat.id == update.message.chat.id
            ):
                await bot.set_my_commands(
                    GROUP_COMMANDS,
                    BotCommandScopeChat(chat_id=update.message.chat.id),
                )
            break


@handlers.message(content_types=[ContentType.LEFT_CHAT_MEMBER])
async def group_left_member(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.left_chat_member is not None
    logger.info(
        'Left group member message "%s"',
        msgspec.to_builtins(update.message),
    )
    me = await bot.get_me()
    if update.message.left_chat_member.id == me.id:
        admin_chat_id = await bot.storage.get(ADMIN_CHAT_ID_KEY)
        assert isinstance(admin_chat_id, int)
        await bot.send_message(
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
    album_forwarder = bot.get(ALBUM_FORWARDER_KEY)
    assert isinstance(album_forwarder, AlbumForwarder)
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info(
        'Message from "%s"',
        msgspec.to_builtins(update.message.from_),
    )
    await set_chat(bot, chat_key(update.message.chat.id), update.message.chat)
    stopped = await Stopped.get(bot, update.message.chat.id)
    if stopped is not None:
        await Stopped.delete(bot, update.message.chat.id)
        await bot.send_message(update.message.chat.id, "С возвращением!")

    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    if group_chat is None:
        forward_chat_id = await bot.storage.get(ADMIN_CHAT_ID_KEY)
        assert isinstance(forward_chat_id, int)
    else:
        forward_chat_id = group_chat.id

    if update.message.audio is not None or update.message.sticker is not None:
        logger.info(
            'Message from user "%s" contains audio or sticker',
            msgspec.to_builtins(update.message.from_),
        )
        await send_from_message(bot, forward_chat_id, update.message.chat)

    if update.message.media_group_id is not None:
        await album_forwarder.add_message(
            update.message, forward_chat_id, add_from_info=True
        )
    else:
        await bot.forward_message(
            forward_chat_id, update.message.chat.id, update.message.message_id
        )

    await add_chat_to_list(bot, update.message.chat)


@handlers.message(filters=[GroupChatFilter()])
async def group_message(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info(
        'Reply messgae in group from "%s"',
        msgspec.to_builtins(update.message.from_),
    )
    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    if group_chat is not None and group_chat.id != update.message.chat.id:
        await bot.leave_chat(update.message.chat.id)
        return
    wait_reply_from_id = await bot.storage.get(WAIT_REPLY_FROM_ID_KEY)
    if (
        wait_reply_from_id != update.message.from_.id
        and update.message.media_group_id is None
    ):
        logger.info(
            'Ignore message from group "%s" user "%s"',
            update.message.chat.title,
            msgspec.to_builtins(update.message.from_),
        )
        return

    await send_user_message(bot, update.message)

    await bot.storage.set(WAIT_REPLY_FROM_ID_KEY)
    await set_chat(bot, CURRENT_CHAT_KEY)


@handlers.message(filters=[PrivateChatFilter(), FromAdminFilter()])
async def admin_message(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    logger.info('Message from admin "%s"', msgspec.to_builtins(update.message))
    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    if group_chat is not None:
        await bot.send_message(
            update.message.chat.id,
            f"Принимаю сообщения в группе <b>{group_chat.title}</b>.",
            parse_mode=ParseMode.HTML,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        logger.info("Ignore message in private chat with admin")
        return
    wait_reply_from_id = await bot.storage.get(WAIT_REPLY_FROM_ID_KEY)
    if wait_reply_from_id is None and update.message.media_group_id is None:
        logger.info("Ignore message from admin")
        return

    await send_user_message(bot, update.message)

    await bot.storage.set(WAIT_REPLY_FROM_ID_KEY)
    await set_chat(bot, CURRENT_CHAT_KEY)


@handlers.callback_query(data_match=REPLY_RXP)
async def reply_callback(bot: Bot, update: BotUpdate) -> None:
    assert update.callback_query is not None
    assert update.callback_query.data is not None
    assert update.callback_query.message is not None
    logger.info(
        'Reply callback query from "%s"',
        msgspec.to_builtins(update.callback_query.from_),
    )
    await bot.answer_callback_query(update.callback_query.id)

    data_match = REPLY_RXP.match(update.callback_query.data)
    assert data_match is not None, "Reply to data not match format"
    current_chat_id = int(data_match.group(CHAT_ID_GROUP))
    current_chat = await get_chat(bot, chat_key(current_chat_id))
    if current_chat is None:
        await bot.edit_message_text(
            "Ошибка. Сообщение не отправить.",
            chat_id=update.callback_query.message.chat.id,
            message_id=update.callback_query.message.message_id,
        )
        logger.info(
            'Skip message sending to unknown user from "%s"',
            msgspec.to_builtins(update.callback_query.from_),
        )
        return
    stopped = await Stopped.get(bot, current_chat_id)
    if stopped is not None:
        await bot.edit_message_text(
            f"{user_link(current_chat)} меня заблокировал "
            f"{stopped.dt:%Y-%m-%d %H:%M:%S %Z}.",
            chat_id=update.callback_query.message.chat.id,
            message_id=update.callback_query.message.message_id,
            parse_mode=ParseMode.HTML,
        )
        return
    await bot.storage.set(
        WAIT_REPLY_FROM_ID_KEY, update.callback_query.from_.id
    )
    await set_chat(bot, CURRENT_CHAT_KEY, current_chat)
    await bot.edit_message_text(
        f"Введите сообщение для {user_link(current_chat)}.",
        chat_id=update.callback_query.message.chat.id,
        message_id=update.callback_query.message.message_id,
        parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


async def run_context(
    _: Runner, storage_path: Path, settings: Settings
) -> AsyncIterator[None]:
    if debug():
        asyncio.get_running_loop().slow_callback_duration = 0.01

    storage = SQLiteStorage(storage_path)
    await storage.connect()
    if await storage.get(CHAT_LIST_KEY) is None:
        await storage.set(CHAT_LIST_KEY, [])
    if await storage.get(CURRENT_CHAT_KEY) is None:
        await storage.set(CURRENT_CHAT_KEY)
    if await storage.get(ADMIN_CHAT_ID_KEY) is None:
        await storage.set(ADMIN_CHAT_ID_KEY)
    if await storage.get(GROUP_CHAT_KEY) is None:
        await storage.set(GROUP_CHAT_KEY)

    handlers.freeze()
    bot = PollBot(settings.tg_token.get_secret_value(), handlers, storage)
    bot[SETTINGS_KEY] = settings
    await bot.start()

    album_forwarder = AlbumForwarder(bot)
    bot[ALBUM_FORWARDER_KEY] = album_forwarder
    await bot[ALBUM_FORWARDER_KEY].start()

    admin_chat_id = await bot.storage.get(ADMIN_CHAT_ID_KEY)
    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    await bot.delete_my_commands()
    await bot.set_my_commands(USER_COMMANDS, BotCommandScopeAllPrivateChats())
    if admin_chat_id is not None:
        assert isinstance(admin_chat_id, int)
        await bot.set_my_commands(
            ADMIN_COMMANDS, BotCommandScopeChat(chat_id=admin_chat_id)
        )
    if group_chat is not None:
        try:
            await bot.set_my_commands(
                GROUP_COMMANDS, BotCommandScopeChat(chat_id=group_chat.id)
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
    await storage.close()


def setup_logging() -> None:
    import os

    log_format = "%(asctime)s %(name)s %(levelname)s: %(message)s"
    if debug():
        logging.basicConfig(level=logging.DEBUG, format=log_format)
        logging.getLogger("asyncio").setLevel(logging.ERROR)
        logging.getLogger("aiosqlite").setLevel(logging.INFO)
    else:
        logging.basicConfig(level=logging.INFO, format=log_format)
    logger.info("PYTHONOPTIMIZE=%s", os.environ.get("PYTHONOPTIMIZE"))
    logger.info(SOFTWARE)


def main() -> None:
    import argparse  # isort:skip
    import os  # isort:skip
    import uvloop

    parser = argparse.ArgumentParser(description="Feedback aiotgbot bot")
    parser.add_argument("storage_path", type=path, help="storage path")
    args = parser.parse_args()
    if not (args.storage_path.is_file() or args.storage_path.parent.is_dir()):
        parser.error(
            f'config file "{args.storage_path}" does not exist '
            f"and parent path is not dir"
        )
    if TZ_KEY not in os.environ:
        parser.error("Env var TZ is not set")
    settings = Settings()

    setup_logging()
    uvloop.install()
    runner = Runner(
        run_context,
        debug=debug(),
        storage_path=args.storage_path,
        settings=settings,
    )
    runner.run()


if __name__ == "__main__":  # pragma: nocover
    main()
