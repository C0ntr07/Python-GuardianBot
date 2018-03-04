# -*- coding: utf-8 -*-
import logging
import os
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError, BadRequest
from telegram.ext import Updater, MessageHandler, Filters, CallbackQueryHandler, CommandHandler

from Incident import Incident
from Incidents import Incidents
from MessageFilters.AdminMentionFilter import AdminMentionFilter
from MessageFilters.AllowedChatsFilter import AllowedChatsFilter
from MessageFilters.ChannelForwardFilter import ChannelForwardFilter
from MessageFilters.JoinChatLinkFilter import JoinChatLinkFilter
from MessageFilters.UsernameFilter import UsernameFilter

from config import BOT_TOKEN, admin_channel_id, admins, chats

logfile_dir_path = os.path.dirname(os.path.abspath(__file__))
logfile_abs_path = os.path.join(logfile_dir_path, "logs", "bot.log")
logfile_handler = logging.FileHandler(logfile_abs_path, 'a', 'utf-8')

if not os.path.exists(os.path.join(logfile_dir_path, "logs")):
    os.makedirs(os.path.join(logfile_dir_path, "logs"))


logger = logging.getLogger(__name__)
# logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO, handlers=[logfile_handler])
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Check if bot token is valid
if not re.match("[0-9]+:[a-zA-Z0-9\-_]+", BOT_TOKEN):
    logging.error("Bot token not correct - please check.")
    exit(1)

updater = Updater(token=BOT_TOKEN)
dp = updater.dispatcher
incidents = Incidents()

for chat_id in chats:
    my_admins = list(admins)
    try:
        for admin in updater.bot.getChatAdministrators(chat_id):
            my_admins.append(admin.user.id)
        admins = set(my_admins)
    except BadRequest:
        logger.error("Couldn't fetch admins. Are you sure the bot is member of chat {}?".format(chat_id))


# Message will be called if spam is detected. The message will be removed
# and the sender will be kicked
def spam_detected(bot, update):
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id

    try:
        # ban user from chat
        bot.kickChatMember(chat_id, user_id)
    except TelegramError:
        logger.warning("Not able to kick user {}: {}".format(user_id, update.message))
        # TODO send message to admins so they check it

    try:
        # Delete message
        bot.deleteMessage(chat_id, message_id=update.message.message_id)
    except TelegramError:
        logger.warning("Not able to delete message: {}".format(update.message))
        # TODO send message to admins so they check it


# Method which will be called, when the message could potentially be spam, but
# a human needs to decide
def ask_admins(bot, update):
    # Ask admins if message is spam
    chat_id = update.message.chat.id
    message_id = update.message.message_id
    user_id = update.message.from_user.id

    spam_button = InlineKeyboardButton("Spam", callback_data='{user_id}_{chat_id}_{message_id}_spam'.format(user_id=user_id, chat_id=chat_id, message_id=message_id))
    no_spam_button = InlineKeyboardButton("No Spam", callback_data='{user_id}_{chat_id}_{message_id}_nospam'.format(user_id=user_id, chat_id=chat_id, message_id=message_id))
    reply_markup = InlineKeyboardMarkup([[spam_button, no_spam_button]])

    new_message = bot.forwardMessage(chat_id=admin_channel_id, from_chat_id=chat_id, message_id=message_id)
    admin_message = bot.sendMessage(chat_id=admin_channel_id, text="Is this message spam?", reply_to_message_id=new_message.message_id, reply_markup=reply_markup)

    # Create a new "incident" which will be handled by the admins
    new_incident = Incident(chat_id=chat_id, message_id=message_id, admin_channel_message_id=admin_message.message_id)
    incidents.append(new_incident)


# This function will be called, when someone adds this bot to any group which
# is not mentioned in the AllowedGroups filter
def leave_group(bot, update):
    update.message.reply_text("I am currently only for private use! Goodbye!")
    logger.info("Leaving group '{g_name}' - {g_id}".format(g_name=update.message.chat.title, g_id=update.message.chat.id))
    bot.leaveChat(update.message.chat_id)


def callback_handler(bot, update):
    orig_user_id = update.callback_query.from_user.id
    orig_chat_id = update.callback_query.message.chat.id
    orig_message_id = update.callback_query.message.message_id
    callback_query_id = update.callback_query.id
    data = update.callback_query.data

    # Only admins are allowed to use admin callback functions
    if orig_user_id not in admins:
        logger.error("User {} used admin callback, but not in admin list!".format(orig_user_id))
        return

    # Get the data from the callback_query
    user_id, chat_id, message_id, action = data.split("_")

    # Create a new incident and check if it's still present
    current_incident = Incident(chat_id=chat_id, message_id=message_id)

    if current_incident not in incidents:
        text = "The incident couldn't be found!"
        bot.editMessageText(chat_id=orig_chat_id, message_id=orig_message_id, text=text)
        bot.answerCallbackQuery(callback_query_id=callback_query_id, text=text)
        return

    if action == "spam":
        try:
            # Delete message
            incidents.handle(current_incident)
            text = "Message is spam. I deleted it."
            bot.deleteMessage(chat_id=chat_id, message_id=message_id)
        except TelegramError as e:
            logger.warning("{} - {}".format(chat_id, message_id))
            logger.warning(e)
            text = "Not able to delete message! Maybe already deleted!"
            logger.warning("Not able to delete message: {}. Maybe already deleted or I'm not an admin!".format(message_id))

        text = "Incident handled by {}\n{}".format(update.callback_query.from_user.first_name, text)
        bot.editMessageText(chat_id=orig_chat_id, message_id=orig_message_id, text=text)
        bot.answerCallbackQuery(callback_query_id=callback_query_id, text=text)

        try:
            bot.kickChatMember(chat_id, user_id)
        except TelegramError:
            text += "\nCouldn't kick user! Maybe he already left!"
            try:
                bot.editMessageText(chat_id=orig_chat_id, message_id=orig_message_id, text=text)
            except:
                pass
            logger.warning("Not able to kick user: {}. Maybe he already left or I'm not an admin!".format(user_id))
    elif action == "nospam":
        incidents.handle(Incident(chat_id=chat_id, message_id=message_id))
        text = "Incident handled by {}\nNo spam. Keeping the message!".format(update.callback_query.from_user.first_name)
        bot.editMessageText(chat_id=orig_chat_id, message_id=orig_message_id, text=text)
        bot.answerCallbackQuery(callback_query_id=callback_query_id, text=text)


def admin_mention(bot, update):
    if update.message.chat.username is None:
        return

    # for admin in admins:
    bot.sendMessage(admin_channel_id, text="*Someone needs an admin!*\n"
                                        "\n*Chat:* {chat}"
                                        "\n*Name:* {user}"
                                        "\n\n[Direct Link](https://t.me/{g_name}/{m_id})".format(chat=update.message.chat.title,
                                                                                                 user=update.message.from_user.first_name,
                                                                                                 g_name=update.message.chat.username,
                                                                                                 m_id=update.message.message_id),
                    parse_mode="Markdown")


dp.add_handler(MessageHandler(Filters.group & (~ AllowedChatsFilter()), leave_group))
dp.add_handler(MessageHandler(Filters.group & ChannelForwardFilter(), spam_detected))
dp.add_handler(MessageHandler(Filters.group & JoinChatLinkFilter(), spam_detected))
dp.add_handler(MessageHandler(Filters.group & AdminMentionFilter(), admin_mention))
dp.add_handler(MessageHandler(Filters.group & UsernameFilter(), ask_admins))
dp.add_handler(CallbackQueryHandler(callback_handler))

updater.start_polling()
logger.info("Bot started")
logger.info("Admins are: {}".format(admins))
updater.idle()
