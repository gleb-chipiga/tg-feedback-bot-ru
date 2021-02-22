import asyncio
import logging
import re
from typing import AsyncIterator, Final, Tuple

from aiotgbot import (Bot, BotUpdate, ContentType, GroupChatFilter,
                      HandlerTable, ParseMode, PrivateChatFilter, Runner,
                      TelegramError)
from aiotgbot.api_types import BotCommand
from aiotgbot.bot import PollBot
from aiotgbot.storage_sqlite import SQLiteStorage

from .helpers import (ADMIN_USERNAME_KEY, CHAT_LIST_KEY, CHAT_LIST_SIZE_KEY,
                      REPLY_PREFIX, AlbumForwarder, Config, FromAdminFilter,
                      FromUserFilter, Stopped, add_chat_to_list, chat_key,
                      debug, get_chat, get_software, path,
                      remove_chat_from_list, reply_menu, send_from_message,
                      send_user_message, set_chat, user_link)

SOFTWARE: Final[str] = get_software()
COMMANDS: Final[Tuple[BotCommand, ...]] = (
    BotCommand('start', 'Начать работу'),
    BotCommand('help', 'Помощь'),
    BotCommand('stop', 'Остановить')
)
CHAT_ID_GROUP: Final[str] = 'chat_id'
REPLY_RXP: 'Final[re.Pattern[str]]' = re.compile(
    rf'^{REPLY_PREFIX}\|(?P<{CHAT_ID_GROUP}>-?\d+)$')
ALBUM_FORWARDER_KEY: Final[str] = 'album_forwarder'
GROUP_CHAT_KEY: Final[str] = 'group_chat'
ADMIN_CHAT_ID_KEY: Final[str] = 'admin_chat_id'
CURRENT_CHAT_KEY: Final[str] = 'current_chat'
WAIT_REPLY_FROM_ID_KEY: Final[str] = 'wait_reply_from_id'
STORAGE_PATH_KEY: Final[str] = 'storage_path'
TG_TOKEN_KEY: Final[str] = 'token'
TZ_KEY: Final['str'] = 'TZ'

logger = logging.getLogger('feedback_bot')
handlers = HandlerTable()


@handlers.message(commands=['start'],
                  filters=[PrivateChatFilter(), FromUserFilter()])
async def user_start_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Start command from "%s"', update.message.from_.to_dict())
    stopped = await Stopped.get(bot, update.message.chat.id)
    if stopped is not None:
        await Stopped.delete(bot, update.message.chat.id)
        await bot.send_message(update.message.chat.id, 'С возвращением!')
    await set_chat(bot, chat_key(update.message.chat.id), update.message.chat)
    await bot.send_message(update.message.chat.id,
                           'Пришлите сообщение или задайте вопрос. '
                           'Также вы можете использовать следующие команды:\n'
                           '/help - помощь\n'
                           '/stop - остановить и не получать больше сообщения')


@handlers.message(commands=['help'],
                  filters=[PrivateChatFilter(), FromUserFilter()])
async def user_help_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Help command from "%s"', update.message.from_.to_dict())
    await bot.send_message(update.message.chat.id,
                           'Пришлите сообщение или задайте вопрос. '
                           'Также вы можете использовать следующие команды:\n'
                           '/help - помощь\n'
                           '/stop - остановить и не получать больше сообщения')


@handlers.message(commands=['stop'],
                  filters=[PrivateChatFilter(), FromUserFilter()])
async def user_stop_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Stop command from "%s"', update.message.from_.to_dict())
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
        f'{user_link(update.message.from_)} меня заблокировал '
        f'{stopped.dt:%Y-%m-%d %H:%M:%S %Z}.',
        parse_mode=ParseMode.HTML)
    current_chat = await get_chat(bot, CURRENT_CHAT_KEY)
    if current_chat is not None and current_chat.id == update.message.from_.id:
        await bot.storage.set(WAIT_REPLY_FROM_ID_KEY)
        await set_chat(bot, CURRENT_CHAT_KEY)


@handlers.message(commands=['start'],
                  filters=[PrivateChatFilter(), FromAdminFilter()])
async def admin_start_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    logger.info('Start command from admin')
    await bot.storage.set(ADMIN_CHAT_ID_KEY, update.message.chat.id)

    await bot.send_message(update.message.chat.id,
                           '/help - помощь\n'
                           '/reply - ответить пользователю\n'
                           '/add_to_group - добавить в группу\n'
                           '/remove_from_group - удалить из группы\n'
                           '/reset - сбросить состояние')


@handlers.message(commands=['help'],
                  filters=[PrivateChatFilter(), FromAdminFilter()])
async def admin_help_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    logger.info('Help command from admin')
    await bot.send_message(update.message.chat.id,
                           '/help - помощь\n'
                           '/reply - ответить пользователю\n'
                           '/add_to_group - добавить в группу\n'
                           '/remove_from_group - удалить из группы\n'
                           '/reset - сбросить состояние')


@handlers.message(commands=['reset'],
                  filters=[PrivateChatFilter(), FromAdminFilter()])
async def admin_reset_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    logger.info('Reset command from admin')
    await bot.storage.set(WAIT_REPLY_FROM_ID_KEY)
    await bot.storage.set(CURRENT_CHAT_KEY)
    await bot.send_message(update.message.chat.id, 'Состояние сброшено.')


@handlers.message(commands=['add_to_group'],
                  filters=[PrivateChatFilter(), FromAdminFilter()])
async def add_to_group_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None

    logger.info('Add to group command from "%s"',
                update.message.from_.to_dict())
    if await get_chat(bot, GROUP_CHAT_KEY) is not None:
        logger.info('Already in group. Ignore command')
        await bot.send_message(update.message.chat.id, 'Уже в группе.')
        return

    bot_username = (await bot.get_me()).username
    link = f'tg://resolve?domain={bot_username}&startgroup=startgroup'
    await bot.send_message(
        update.message.chat.id,
        f'Для добавления в группу <a href="{link}">перейдите по ссылке</a>.',
        parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@handlers.message(commands=['remove_from_group'],
                  filters=[PrivateChatFilter(), FromAdminFilter()])
async def remove_from_group_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Remove from group command from "%s"',
                update.message.from_.to_dict())
    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    if group_chat is None:
        logger.info('Not in group. Ignore command')
        await bot.send_message(update.message.chat.id, 'Не в группе.')
        return

    try:
        await bot.leave_chat(group_chat.id)
    except TelegramError as exception:
        logger.error('Leave chat error "%s"', exception)

    await bot.send_message(update.message.chat.id,
                           f'Удален из группы <b>{group_chat.title}</b>.',
                           parse_mode=ParseMode.HTML,
                           disable_web_page_preview=True)

    await set_chat(bot, GROUP_CHAT_KEY)
    await set_chat(bot, CURRENT_CHAT_KEY)

    logger.info('Removed from group "%s"', group_chat.to_dict())


@handlers.message(commands=['start'],
                  filters=[GroupChatFilter(), FromAdminFilter()])
async def group_start_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Start in group command from "%s"',
                update.message.from_.to_dict())
    if await get_chat(bot, GROUP_CHAT_KEY):
        logger.info('Attempt start in group "%s"',
                    update.message.chat.to_dict())
        return

    await set_chat(bot, GROUP_CHAT_KEY, update.message.chat)
    await set_chat(bot, CURRENT_CHAT_KEY)

    admin_chat_id = await bot.storage.get(ADMIN_CHAT_ID_KEY)
    assert isinstance(admin_chat_id, int)
    await bot.send_message(
        admin_chat_id, f'Запущен в <b>{update.message.chat.title}</b>.',
        parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    logger.info('Started in group "%s"', update.message.chat.to_dict())


@handlers.message(commands=['help'], filters=[GroupChatFilter()])
async def group_help_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Help message in group from "%s"',
                update.message.from_.to_dict())
    await bot.send_message(update.message.chat.id,
                           '/help - помощь\n'
                           '/reply - ответить пользователю')


@handlers.message(commands=['reply'],
                  filters=[PrivateChatFilter(), FromAdminFilter()])
async def admin_reply_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Reply command from admin "%s"',
                update.message.from_.to_dict())
    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    if group_chat is not None:
        await bot.send_message(
            update.message.chat.id,
            f'Принимаю сообщения в группе <b>{group_chat.title}</b>.',
            parse_mode=ParseMode.HTML
        )
        logger.debug('Ignore reply command in private chat')
        return
    if await bot.storage.get(WAIT_REPLY_FROM_ID_KEY) is not None:
        await bot.send_message(update.message.chat.id, 'Уже жду сообщение.')
        logger.debug('Already wait message. Ignore command')
        return

    await reply_menu(bot, update.message.chat.id)


@handlers.message(commands=['reply'], filters=[GroupChatFilter()])
async def group_reply_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Reply in group command from "%s"',
                update.message.from_.to_dict())
    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    if group_chat is not None and group_chat.id != update.message.chat.id:
        await bot.leave_chat(update.message.chat.id)
        return
    if group_chat is None:
        await bot.send_message(update.message.chat.id,
                               'Не принимаю сообщения.')
        logger.debug('Ignore reply command in group')
        return
    wait_reply_from_id = await bot.storage.get(WAIT_REPLY_FROM_ID_KEY)
    if wait_reply_from_id is not None:
        assert isinstance(wait_reply_from_id, int)
        member = await bot.get_chat_member(update.message.chat.id,
                                           wait_reply_from_id)
        member_link = (user_link(member.user) if member.user.username is None
                       else f'@{member.user.username}')
        await bot.send_message(
            update.message.chat.id,
            f'Уже жду сообщение от {member_link}.',
            parse_mode=ParseMode.HTML
        )
        logger.debug('Already wait message. Ignore command')
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
                f'Добавлен в группу <b>{update.message.chat.title}</b>.',
                parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            logger.info('Bot added to grouip "%s"',
                        update.message.chat.to_dict())
            group_chat = await get_chat(bot, GROUP_CHAT_KEY)
            if (
                group_chat is not None and
                group_chat.id != update.message.chat.id
            ):
                await bot.leave_chat(update.message.chat.id)
            break


@handlers.message(content_types=[ContentType.LEFT_CHAT_MEMBER])
async def group_left_member(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.left_chat_member is not None
    logger.info('Left group member message "%s"', update.message.to_dict())
    me = await bot.get_me()
    if update.message.left_chat_member.id == me.id:
        admin_chat_id = await bot.storage.get(ADMIN_CHAT_ID_KEY)
        assert isinstance(admin_chat_id, int)
        await bot.send_message(
            admin_chat_id,
            f'Вышел из группы <b>{update.message.chat.title}</b>.',
            parse_mode=ParseMode.HTML, disable_web_page_preview=True)
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
    logger.info('Message from "%s"', update.message.from_.to_dict())
    await set_chat(bot, chat_key(update.message.chat.id), update.message.chat)
    stopped = await Stopped.get(bot, update.message.chat.id)
    if stopped is not None:
        await Stopped.delete(bot, update.message.chat.id)
        await bot.send_message(update.message.chat.id, 'С возвращением!')

    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    if group_chat is None:
        forward_chat_id = await bot.storage.get(ADMIN_CHAT_ID_KEY)
        assert isinstance(forward_chat_id, int)
    else:
        forward_chat_id = group_chat.id

    if update.message.audio is not None or update.message.sticker is not None:
        logger.info('Message from user "%s" contains audio or sticker',
                    update.message.from_.to_dict())
        await send_from_message(bot, forward_chat_id, update.message.chat)

    if update.message.media_group_id is not None:
        await album_forwarder.add_message(
            update.message, forward_chat_id, add_from_info=True)
    else:
        await bot.forward_message(forward_chat_id, update.message.chat.id,
                                  update.message.message_id)

    await add_chat_to_list(bot, update.message.chat)


@handlers.message(filters=[GroupChatFilter()])
async def group_message(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Reply messgae in group from "%s"',
                update.message.from_.to_dict())
    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    if group_chat is not None and group_chat.id != update.message.chat.id:
        await bot.leave_chat(update.message.chat.id)
        return
    wait_reply_from_id = await bot.storage.get(WAIT_REPLY_FROM_ID_KEY)
    if (
        wait_reply_from_id != update.message.from_.id and
        update.message.media_group_id is None
    ):
        logger.info('Ignore message from group "%s" user "%s"',
                    update.message.chat.title, update.message.from_.to_dict())
        return

    await send_user_message(bot, update.message)

    await bot.storage.set(WAIT_REPLY_FROM_ID_KEY)
    await set_chat(bot, CURRENT_CHAT_KEY)


@handlers.message(filters=[PrivateChatFilter(), FromAdminFilter()])
async def admin_message(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    logger.info('Message from admin "%s"', update.message.to_dict())
    group_chat = await get_chat(bot, GROUP_CHAT_KEY)
    if group_chat is not None:
        await bot.send_message(
            update.message.chat.id,
            f'Принимаю сообщения в группе <b>{group_chat.title}</b>.',
            parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        logger.info('Ignore message in private chat with admin')
        return
    wait_reply_from_id = await bot.storage.get(WAIT_REPLY_FROM_ID_KEY)
    if wait_reply_from_id is None and update.message.media_group_id is None:
        logger.info('Ignore message from admin')
        return

    await send_user_message(bot, update.message)

    await bot.storage.set(WAIT_REPLY_FROM_ID_KEY)
    await set_chat(bot, CURRENT_CHAT_KEY)


@handlers.callback_query(data_match=REPLY_RXP)
async def reply_callback(bot: Bot, update: BotUpdate) -> None:
    assert update.callback_query is not None
    assert update.callback_query.data is not None
    assert update.callback_query.message is not None
    logger.info('Reply callback query from "%s"',
                update.callback_query.from_.to_dict())
    await bot.answer_callback_query(update.callback_query.id)

    data_match = REPLY_RXP.match(update.callback_query.data)
    assert data_match is not None, 'Reply to data not match format'
    current_chat_id = int(data_match.group(CHAT_ID_GROUP))
    current_chat = await get_chat(bot, chat_key(current_chat_id))
    if current_chat is None:
        await bot.edit_message_text(
            'Ошибка. Сообщение не отправить.',
            chat_id=update.callback_query.message.chat.id,
            message_id=update.callback_query.message.message_id)
        logger.info('Skip message sending to unknown user from "%s"',
                    update.callback_query.from_.to_dict())
        return
    stopped = await Stopped.get(bot, current_chat_id)
    if stopped is not None:
        await bot.edit_message_text(
            f'{user_link(current_chat)} меня заблокировал '
            f'{stopped.dt:%Y-%m-%d %H:%M:%S %Z}.',
            chat_id=update.callback_query.message.chat.id,
            message_id=update.callback_query.message.message_id,
            parse_mode=ParseMode.HTML)
        return
    await bot.storage.set(WAIT_REPLY_FROM_ID_KEY,
                          update.callback_query.from_.id)
    await set_chat(bot, CURRENT_CHAT_KEY, current_chat)
    await bot.edit_message_text(
        f'Введите сообщение для {user_link(current_chat)}.',
        chat_id=update.callback_query.message.chat.id,
        message_id=update.callback_query.message.message_id,
        parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def run_context(runner: Runner) -> AsyncIterator[None]:
    if debug():
        asyncio.get_running_loop().slow_callback_duration = 0.01

    storage = SQLiteStorage(runner[STORAGE_PATH_KEY])
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
    bot = PollBot(runner[TG_TOKEN_KEY], handlers, storage)
    bot[ADMIN_USERNAME_KEY] = runner[ADMIN_USERNAME_KEY]
    bot[CHAT_LIST_SIZE_KEY] = runner[CHAT_LIST_SIZE_KEY]
    await bot.start()

    bot[ALBUM_FORWARDER_KEY] = AlbumForwarder(bot)
    await bot[ALBUM_FORWARDER_KEY].start()

    if COMMANDS != await bot.get_my_commands():
        logger.info('Update bot commands')
        await bot.set_my_commands(COMMANDS)

    yield

    await bot[ALBUM_FORWARDER_KEY].stop()
    await bot.stop()
    await storage.close()


def setup_logging() -> None:
    import os

    log_format = '%(asctime)s %(name)s %(levelname)s: %(message)s'
    if debug():
        logging.basicConfig(level=logging.DEBUG, format=log_format)
        logging.getLogger('asyncio').setLevel(logging.ERROR)
        logging.getLogger('aiosqlite').setLevel(logging.INFO)
    else:
        logging.basicConfig(level=logging.INFO, format=log_format)
    logger.info('PYTHONOPTIMIZE=%s', os.environ.get('PYTHONOPTIMIZE'))
    logger.info(SOFTWARE)


def main() -> None:
    import argparse  # isort:skip
    import os  # isort:skip
    import uvloop

    parser = argparse.ArgumentParser(description='Feedback aiotgbot bot')
    parser.add_argument('config_path', type=path, help='config path')
    parser.add_argument('storage_path', type=path, help='storage path')
    args = parser.parse_args()
    if not args.config_path.exists():
        parser.error(f'config file "{args.config_path}" does not exist')
    if not (args.storage_path.is_file() or args.storage_path.parent.is_dir()):
        parser.error(f'config file "{args.storage_path}" does not exist '
                     f'and parent path is not dir')
    if TZ_KEY not in os.environ:
        parser.error('Env var TZ is not set')
    config = Config.load(args.config_path)

    setup_logging()
    uvloop.install()
    runner = Runner(run_context, debug=debug())
    runner[TG_TOKEN_KEY] = config.tg_token
    runner[ADMIN_USERNAME_KEY] = config.admin_username
    runner[CHAT_LIST_SIZE_KEY] = config.chat_list_size
    runner[STORAGE_PATH_KEY] = args.storage_path
    runner.run()


if __name__ == '__main__':  # pragma: nocover
    main()
