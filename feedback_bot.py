import asyncio
import logging
import re
from typing import TYPE_CHECKING, List, Optional, Union

import attr
from aiotgbot import (BaseFilter, BaseStorage, Bot, BotBlocked, BotUpdate,
                      Chat, ContentType, GroupChatFilter, HandlerTable,
                      InlineKeyboardButton, InlineKeyboardMarkup, ParseMode,
                      PrivateChatFilter, TelegramError, entities_to_html)
from aiotgbot.storage_sqlite import SQLiteStorage

logger = logging.getLogger('feedback_bot')

handlers = HandlerTable()


def user_name(chat: Chat) -> str:
    assert chat.first_name is not None
    if chat.last_name is not None:
        return f'{chat.first_name} {chat.last_name}'
    else:
        return chat.first_name


def user_link(chat: Chat) -> str:
    return f'<a href="tg://user?id={chat.id}">{user_name(chat)}</a>'


async def set_chat(_storage: BaseStorage, key: str,
                   chat: Optional[Chat] = None) -> None:
    await _storage.set(key, chat.to_dict() if chat is not None else None)


async def get_chat(_storage: BaseStorage, key: str) -> Optional[Chat]:
    data = await _storage.get(key)
    return Chat.from_dict(data) if data is not None else None


async def set_chat_list(_storage: BaseStorage, key: str,
                        chat_list: List[Chat]) -> None:
    await _storage.set(key, [chat.to_dict() for chat in chat_list])


async def get_chat_list(_storage: BaseStorage, key: str) -> List[Chat]:
    return [Chat.from_dict(item) for item in await _storage.get(key)]


@attr.s(slots=True, frozen=True, auto_attribs=True)
class FromUserFilter(BaseFilter):

    async def check(self, bot: Bot, update: BotUpdate) -> bool:
        if 'admin_username' not in bot:
            raise RuntimeError('Admin username not set')

        return (update.message is not None and
                update.message.from_ is not None and
                update.message.from_.username != bot['admin_username'])


@attr.s(slots=True, frozen=True, auto_attribs=True)
class FromAdminFilter(BaseFilter):

    async def check(self, bot: Bot, update: BotUpdate) -> bool:
        if 'admin_username' not in bot:
            raise RuntimeError('Admin username not set')

        return (update.message is not None and
                update.message.from_ is not None and
                update.message.from_.username == bot['admin_username'])


@handlers.message(commands=['start'],
                  filters=[PrivateChatFilter(), FromUserFilter()])
async def user_start_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Start command from "%s"', update.message.from_.to_dict())
    await set_chat(bot.storage, f'chat-{update.message.chat.id}',
                   update.message.chat)
    await bot.send_message(update.message.chat.id,
                           'Пришлите сообщение или задайте вопрос.')


@handlers.message(commands=['help'],
                  filters=[PrivateChatFilter(), FromUserFilter()])
async def user_help_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Help command from "%s"', update.message.from_.to_dict())
    await bot.send_message(update.message.chat.id,
                           'Пришлите сообщение или задайте вопрос.')


@handlers.message(commands=['start'],
                  filters=[PrivateChatFilter(), FromAdminFilter()])
async def admin_start_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    logger.info('Start command from admin')
    await bot.storage.set('admin_chat_id', update.message.chat.id)

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
    await bot.storage.set('wait_reply_from_id')
    await bot.storage.set('current_chat')
    await bot.send_message(update.message.chat.id, 'Состояние сброшено.')


@handlers.message(commands=['add_to_group'],
                  filters=[PrivateChatFilter(), FromAdminFilter()])
async def add_to_group_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None

    logger.info('Add to group command from "%s"',
                update.message.from_.to_dict())
    if await get_chat(bot.storage, 'group_chat') is not None:
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
    group_chat = await get_chat(bot.storage, 'group_chat')
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

    await set_chat(bot.storage, 'group_chat')
    await set_chat(bot.storage, 'current_chat')

    logger.info('Removed from group "%s"', group_chat.to_dict())


@handlers.message(commands=['start'],
                  filters=[GroupChatFilter(), FromAdminFilter()])
async def group_start_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Start in group command from "%s"',
                update.message.from_.to_dict())
    if await get_chat(bot.storage, 'group_chat'):
        logger.info('Attempt start in group "%s"',
                    update.message.chat.to_dict())
        return

    await set_chat(bot.storage, 'group_chat', update.message.chat)
    await set_chat(bot.storage, 'current_chat')

    admin_chat_id = await bot.storage.get('admin_chat_id')
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


async def reply_menu(bot: Bot, chat_id: Union[int, str]) -> None:
    await bot.send_message(
        chat_id,
        ('Выберите пользователя для ответа.'
         if await bot.storage.get('chat_list') else 'Некому отвечать.'),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(
                user_name(chat), callback_data=f'reply-to-{chat.id}'
            )] for chat in await get_chat_list(bot.storage, 'chat_list')]
        )
    )


@handlers.message(commands=['reply'],
                  filters=[PrivateChatFilter(), FromAdminFilter()])
async def admin_reply_command(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Reply command from admin "%s"',
                update.message.from_.to_dict())
    if await get_chat(bot.storage, 'group_chat') is not None:
        await bot.send_message(update.message.chat.id,
                               'Принимаю сообщения в группе.')
        logger.debug('Ignore reply command in private chat')
        return
    if await bot.storage.get('wait_reply_from_id') is not None:
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
    group_chat = await get_chat(bot.storage, 'group_chat')
    if group_chat is not None and group_chat.id != update.message.chat.id:
        await bot.leave_chat(update.message.chat.id)
        return
    if group_chat is None:
        await bot.send_message(update.message.chat.id,
                               'Не принимаю сообщения.')
        logger.debug('Ignore reply command in group')
        return
    if await bot.storage.get('wait_reply_from_id') is not None:
        await bot.send_message(update.message.chat.id, 'Уже жду сообщение.')
        logger.debug('Already wait message. Ignore command')
        return

    await reply_menu(bot, update.message.chat.id)


@handlers.message(content_types=[ContentType.NEW_CHAT_MEMBERS])
async def group_new_members(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.new_chat_members is not None
    logger.info('New group members message "%s"', update.message.chat)
    group_chat = await get_chat(bot.storage, 'group_chat')
    for user in update.message.new_chat_members:
        if user.username == (await bot.get_me()).username:
            await bot.send_message(
                await bot.storage.get('admin_chat_id'),
                f'Добавлен в группу <b>{update.message.chat.title}</b>.',
                parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            logger.info('Bot added to grouip "%s"',
                        update.message.chat.to_dict())

            if (
                group_chat is not None and
                group_chat.id != update.message.chat.id
            ):
                await bot.leave_chat(update.message.chat.id)

            break


@handlers.message(content_types=[ContentType.LEFT_CHAT_MEMBER])
async def group_left_member(bot: Bot, update: BotUpdate):
    assert update.message is not None
    assert update.message.left_chat_member is not None
    logger.info('Left group member message "%s"', update.message.to_dict())
    me = await bot.get_me()
    if update.message.left_chat_member.id == me.id:
        await bot.send_message(
            await bot.storage.get('admin_chat_id'),
            f'Вышел из группы <b>{update.message.chat.title}</b>.',
            parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        logger.info('Leave chat "%s"', update.message.chat.title)
        group_chat = await get_chat(bot.storage, 'group_chat')
        if group_chat is not None and update.message.chat.id == group_chat.id:
            await set_chat(bot.storage, 'group_chat')
            logger.info('Forget chat "%s"', update.message.chat.title)


@handlers.message(filters=[PrivateChatFilter(), FromUserFilter()])
async def user_message(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Message from "%s"', update.message.from_.to_dict())
    await bot.storage.set(f'chat-{update.message.chat.id}',
                          update.message.chat.to_dict())

    group_chat = await get_chat(bot.storage, 'group_chat')
    if group_chat is not None:
        forward_chat_id = group_chat.id
    else:
        forward_chat_id = await bot.storage.get('admin_chat_id')

    if update.message.audio is not None or update.message.sticker is not None:
        logger.info('Message from user "%s" contains audio or sticker',
                    update.message.from_.to_dict())
        link = user_link(update.message.chat)
        await bot.send_message(forward_chat_id, f'От {link}',
                               parse_mode=ParseMode.HTML)

    await bot.forward_message(forward_chat_id, update.message.chat.id,
                              update.message.message_id)

    chat_list = await get_chat_list(bot.storage, 'chat_list')
    if all(item.id != update.message.chat.id for item in chat_list):
        chat_list.append(update.message.chat)
        if len(chat_list) > bot['chat_list_size']:
            chat_list.pop(0)
        await set_chat_list(bot.storage, 'chat_list', chat_list)


async def send_user_message(bot: Bot, admin_chat_id: int,
                            html_text: str) -> None:
    current_chat = await get_chat(bot.storage, 'current_chat')
    if current_chat is None:
        await bot.send_message(admin_chat_id, 'Нет текущего пользователя')
        logger.info('Skip message to user: no current user')
        return
    logger.info('Send message to "%s"', current_chat.to_dict())
    try:
        await bot.send_message(current_chat.id, html_text,
                               parse_mode=ParseMode.HTML)
    except BotBlocked:
        chat_list = await get_chat_list(bot.storage, 'chat_list')
        chat_list = [item for item in chat_list if item.id != current_chat.id]
        await set_chat_list(bot.storage, 'chat_list', chat_list)
        await bot.storage.delete(f'chat-{current_chat.id}')
        await bot.send_message(
            admin_chat_id, f'{user_link(current_chat)} меня заблокировал.',
            parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        logger.info('Blocked by user "%s"', current_chat.to_dict())
        return
    else:
        await bot.send_message(
            admin_chat_id, f'Сообщение отправлено {user_link(current_chat)}.',
            parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@handlers.message(filters=[GroupChatFilter()])
async def group_message(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    assert update.message.from_ is not None
    logger.info('Reply messgae in group from "%s"',
                update.message.from_.to_dict())
    group_chat = await get_chat(bot.storage, 'group_chat')
    if group_chat is not None and group_chat.id != update.message.chat.id:
        await bot.leave_chat(update.message.chat.id)
        return
    if await bot.storage.get('wait_reply_from_id') != update.message.from_.id:
        logger.info('Ignore message from group "%s" user "%s"',
                    update.message.chat.title, update.message.from_.to_dict())
        return
    if update.message.text is None:
        await bot.send_message(update.message.chat.id,
                               'Поддерживаются только текстовые сообщения.')
        logger.info('Skip message without text')
        return

    await send_user_message(bot, update.message.chat.id,
                            entities_to_html(update.message.entities,
                                             update.message.text))

    await bot.storage.set('wait_reply_from_id')
    await set_chat(bot.storage, 'current_chat')


@handlers.message(filters=[PrivateChatFilter(), FromAdminFilter()])
async def admin_message(bot: Bot, update: BotUpdate) -> None:
    assert update.message is not None
    logger.info('Message from admin "%s"', update.message.to_dict())
    group_chat = await get_chat(bot.storage, 'group_chat')
    if group_chat is not None:
        await bot.send_message(
            update.message.chat.id,
            f'Принимаю сообщения в группе <b>{group_chat.title}</b>.',
            parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        logger.info('Ignore message in private chat with admin')
        return
    wait_reply_from_id = await bot.storage.get('wait_reply_from_id')
    if wait_reply_from_id is None:
        logger.info('Skip message without text from admin')
        return
    if update.message.text is None:
        await bot.send_message(update.message.chat.id,
                               'Поддерживаются только текстовые сообщения.')
        logger.info('Skip message without text from admin')
        return

    await send_user_message(bot, update.message.chat.id,
                            entities_to_html(update.message.entities,
                                             update.message.text))

    await bot.storage.set('wait_reply_from_id')
    await set_chat(bot.storage, 'current_chat')


@handlers.callback_query(data_match=r'^reply-to-\d+$')
async def reply_callback(bot: Bot, update: BotUpdate) -> None:
    assert update.callback_query is not None
    assert update.callback_query.data is not None
    assert update.callback_query.message is not None
    logger.info('Reply callback query from "%s"',
                update.callback_query.from_.to_dict())
    await bot.answer_callback_query(update.callback_query.id)

    data_match = re.match(r'^reply-to-(?P<chat_id>\d+)$',
                          update.callback_query.data)
    assert data_match is not None, 'Reply to data not match format'

    if not await get_chat(bot.storage, f'chat-{data_match.group("chat_id")}'):
        await bot.edit_message_text(
            'Сообщение не отправлено. Пользователь неактивен.',
            chat_id=update.callback_query.message.chat.id,
            message_id=update.callback_query.message.message_id)
        logger.info('Skip message sending fo user inactive from "%s"',
                    update.callback_query.from_.to_dict())
        return

    await bot.storage.set('wait_reply_from_id', update.callback_query.from_.id)
    current_key = f'chat-{data_match.group("chat_id")}'
    current_chat = await get_chat(bot.storage, current_key)
    if current_chat is None:
        logger.info('Selected chat not found in storage "%s"', current_key)
        await bot.edit_message_text(
            f'Ошибка.', chat_id=update.callback_query.message.chat.id,
            message_id=update.callback_query.message.message_id,
            disable_web_page_preview=True)
        return
    await set_chat(bot.storage, 'current_chat', current_chat)
    await bot.edit_message_text(
        f'Введите сообщение для {user_link(current_chat)}.',
        chat_id=update.callback_query.message.chat.id,
        message_id=update.callback_query.message.message_id,
        parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def on_startup(bot: Bot) -> None:
    if await bot.storage.get('chat_list') is None:
        await bot.storage.set('chat_list', [])
    if await bot.storage.get('current_chat') is None:
        await bot.storage.set('current_chat')
    if await bot.storage.get('admin_chat_id') is None:
        await bot.storage.set('admin_chat_id')
    if await bot.storage.get('group_chat') is None:
        await bot.storage.set('group_chat')


def main():
    import argparse
    import os
    import pathlib

    parser = argparse.ArgumentParser(description='Feedback aiotgbot bot.')
    parser.add_argument('storage_path', type=pathlib.Path,
                        help='aiotgbot bot API token')
    parser.add_argument('-a', dest='admin_username',
                        default=os.environ.get('ADMIN_USERNAME', ''),
                        type=str, help='admin username')
    parser.add_argument('-t', dest='token',
                        default=os.environ.get('TG_BOT_TOKEN', ''),
                        type=str, help='aiotgbot bot API token')
    parser.add_argument('-l', dest='chat_list_size', type=int, default=5,
                        help='size of chat list')
    parser.add_argument('-d', dest='debug', action='store_true',
                        help='enable debug mode')
    args = parser.parse_args()

    if args.admin_username == '':
        parser.error('admin username is empty')

    if args.token == '':
        parser.error('token is empty')

    debug = args.debug or os.environ.get('DEBUG', '0') == '1'

    if not (args.storage_path.is_file() or args.storage_path.parent.is_dir()):
        parser.error(f'config file "{args.storage_path}" does not exist '
                     f'and parent path is not dir')

    log_format = '%(asctime)s %(name)s %(levelname)s: %(message)s'
    if debug:
        logging.basicConfig(level=logging.DEBUG, format=log_format)
        logging.getLogger('asyncio').setLevel(logging.ERROR)
        logging.getLogger('aiosqlite').setLevel(logging.INFO)
    else:
        logging.basicConfig(level=logging.INFO, format=log_format)

    storage = SQLiteStorage(args.storage_path)
    feedback_bot = Bot(args.token, handlers, storage)
    feedback_bot['admin_username'] = args.admin_username
    feedback_bot['chat_list_size'] = args.chat_list_size

    if not TYPE_CHECKING:
        try:
            import uvloop
        except ImportError:
            pass
        else:
            asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

    asyncio.run(feedback_bot.poll(on_startup=on_startup), debug=debug)


if __name__ == '__main__':  # pragma: nocover
    main()
