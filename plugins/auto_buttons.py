import logging
from urllib.parse import urlparse

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo.errors import PyMongoError

from info import ADMINS
from database.users_chats_db import db
from plugins.Dreamxfutures.button_style import api_edit, COLORS_ENABLED

logger = logging.getLogger(__name__)

# =========================================
# CONFIGURATION
# =========================================

SETTINGS_ID = "channel_button_manager_v1"
TARGET_CHANNEL_ID = -1002413838031

VALID_STYLES = {"primary", "success", "danger"}

DEFAULT_BUTTONS = [
    [
        {
            "text": "🔰 ᴍᴏᴠɪᴇ ꜱᴇᴀʀᴄʜ ɢʀᴏᴜᴘ 🔰",
            "url": "https://t.me/newmovieswebseries_group",
            "style": "success",
        }
    ],
    [
        {
            "text": "📢 ɴᴇᴡ ᴍᴏᴠɪᴇ ɴᴏᴛɪꜰɪᴄᴀᴛɪᴏɴ ᴄʜᴀɴɴᴇʟ 📢",
            "url": "https://t.me/new_movie_update_2026",
            "style": "primary",
        }
    ],
]

# Temporary state for interactive button creation.
PENDING_BUTTONS = {}


# =========================================
# DATABASE
# =========================================

async def get_button_config():
    await db.misc.update_one(
        {"_id": SETTINGS_ID},
        {
            "$setOnInsert": {
                "enabled": True,
                "channels": {
                    str(TARGET_CHANNEL_ID): DEFAULT_BUTTONS
                },
            }
        },
        upsert=True,
    )

    config = await db.misc.find_one({"_id": SETTINGS_ID})

    return config or {
        "enabled": True,
        "channels": {
            str(TARGET_CHANNEL_ID): DEFAULT_BUTTONS
        },
    }


async def save_button_config(config):
    await db.misc.update_one(
        {"_id": SETTINGS_ID},
        {
            "$set": {
                "enabled": bool(config.get("enabled", True)),
                "channels": config.get("channels", {}),
            }
        },
        upsert=True,
    )


# =========================================
# KEYBOARD HELPERS
# =========================================

def create_plain_markup(rows):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    button["text"],
                    **{
                        key: button[key]
                        for key in (
                            "url",
                            "callback_data",
                            "switch_inline_query_current_chat",
                            "switch_inline_query",
                        )
                        if button.get(key) is not None
                    },
                )
                for button in row
            ]
            for row in rows
        ]
    )


def valid_button_url(value):
    try:
        parsed = urlparse(value.strip())

        return (
            parsed.scheme in ("http", "https", "tg")
            and bool(parsed.netloc)
        )
    except Exception:
        return False


# =========================================
# AUTO BUTTON ON / OFF
# =========================================

@Client.on_message(
    filters.command("auto_button") & filters.user(ADMINS)
)
async def auto_button_command(client, message):
    config = await get_button_config()

    if len(message.command) == 1:
        status = "ON ✅" if config.get("enabled", True) else "OFF ❌"

        await message.reply_text(
            f"🔘 **Automatic Button Status: {status}**\n\n"
            "`/auto_button on` - Enable\n"
            "`/auto_button off` - Disable"
        )
        return

    state = message.command[1].lower()

    if state not in ("on", "off"):
        await message.reply_text(
            "⚠️ **Invalid command!**\n\n"
            "Use `/auto_button on` or `/auto_button off`."
        )
        return

    config["enabled"] = state == "on"
    await save_button_config(config)

    if state == "on":
        await message.reply_text(
            "✅ **Automatic channel buttons enabled!**"
        )
    else:
        await message.reply_text(
            "❌ **Automatic channel buttons disabled!**"
        )


# =========================================
# CREATE NEW BUTTON
# =========================================

@Client.on_message(
    filters.command("new_button") & filters.user(ADMINS)
)
async def new_button_command(client, message):
    PENDING_BUTTONS[message.from_user.id] = {
        "step": "channel"
    }

    await message.reply_text(
        "➕ **NEW BUTTON CREATOR**\n\n"
        "Step 1️⃣: Send the channel ID.\n\n"
        "Example:\n"
        "`-1001234567890`\n\n"
        "Bot ko us channel ka administrator hona chahiye.\n\n"
        "Cancel karne ke liye `/cancel` bhejein."
    )


@Client.on_message(
    filters.command("cancel") & filters.user(ADMINS)
)
async def cancel_button_command(client, message):
    if PENDING_BUTTONS.pop(message.from_user.id, None):
        await message.reply_text(
            "❌ Button creation cancelled."
        )
    else:
        await message.reply_text(
            "ℹ️ No button creation is running."
        )


@Client.on_message(
    filters.private
    & filters.user(ADMINS)
    & filters.text
)
async def process_button_creation(client, message):
    user_id = message.from_user.id
    pending = PENDING_BUTTONS.get(user_id)

    if not pending:
        return

    value = (message.text or "").strip()

    if value.startswith("/"):
        return

    if not value:
        await message.reply_text(
            "⚠️ Please send valid text."
        )
        return

    # STEP 1: CHANNEL ID
    if pending["step"] == "channel":
        try:
            channel_id = int(value)

            if channel_id >= 0:
                raise ValueError

        except ValueError:
            await message.reply_text(
                "⚠️ Invalid channel ID.\n"
                "Example: `-1001234567890`"
            )
            return

        pending["channel_id"] = channel_id
        pending["step"] = "text"

        await message.reply_text(
            "Step 2️⃣: Send the button text.\n\n"
            "Maximum 64 characters."
        )
        return

    # STEP 2: BUTTON TEXT
    if pending["step"] == "text":
        if len(value) > 64:
            await message.reply_text(
                "⚠️ Button text must not exceed 64 characters."
            )
            return

        pending["text"] = value
        pending["step"] = "url"

        await message.reply_text(
            "Step 3️⃣: Send the button URL."
        )
        return

    # STEP 3: BUTTON URL
    if pending["step"] == "url":
        if not valid_button_url(value):
            await message.reply_text(
                "⚠️ Invalid URL. Please send a valid URL."
            )
            return

        pending["url"] = value
        pending["step"] = "style"

        await message.reply_text(
            "Step 4️⃣: Select button style.\n\n"
            "🔵 `primary` - Blue\n"
            "🟢 `success` - Green\n"
            "🔴 `danger` - Red\n"
            "⚪ `default` - Normal\n\n"
            "Send one style name."
        )
        return

    # STEP 4: STYLE AND SAVE
    if pending["step"] == "style":
        style = value.lower()

        if style not in VALID_STYLES | {"default"}:
            await message.reply_text(
                "⚠️ Choose primary, success, danger or default."
            )
            return

        button = {
            "text": pending["text"],
            "url": pending["url"],
        }

        if style != "default":
            button["style"] = style

        config = await get_button_config()

        channels = config.setdefault("channels", {})
        channel_key = str(pending["channel_id"])

        channels.setdefault(channel_key, []).append([button])

        await save_button_config(config)

        PENDING_BUTTONS.pop(user_id, None)

        await message.reply_text(
            "✅ **BUTTON CREATED SUCCESSFULLY!**\n\n"
            f"📢 Channel: `{channel_key}`\n"
            f"🔘 Text: {button['text']}\n"
            f"🎨 Style: `{style}`\n\n"
            "Button will be added to new media posts in this channel."
        )


# =========================================
# LIST BUTTONS
# =========================================

@Client.on_message(
    filters.command("list_buttons") & filters.user(ADMINS)
)
async def list_buttons_command(client, message):
    config = await get_button_config()
    channels = config.get("channels", {})

    if len(message.command) > 1:
        try:
            channel_ids = [str(int(message.command[1]))]
        except ValueError:
            await message.reply_text(
                "Usage: `/list_buttons` or `/list_buttons CHANNEL_ID`"
            )
            return
    else:
        channel_ids = list(channels.keys())

    output = []

    for channel_id in channel_ids:
        output.append(f"📢 **Channel:** `{channel_id}`")

        rows = channels.get(channel_id, [])

        if not rows:
            output.append("No buttons configured.")
            continue

        number = 0

        for row in rows:
            for button in row:
                number += 1

                output.append(
                    f"\n{number}. {button['text']}\n"
                    f"🎨 Style: `{button.get('style', 'default')}`\n"
                    f"🔗 {button.get('url', '')}"
                )

    await message.reply_text(
        "\n".join(output) if output else "No buttons configured."
    )


# =========================================
# DELETE ONE BUTTON
# =========================================

@Client.on_message(
    filters.command("delete_button") & filters.user(ADMINS)
)
async def delete_button_command(client, message):
    if len(message.command) < 3:
        await message.reply_text(
            "Usage:\n"
            "`/delete_button CHANNEL_ID BUTTON_NUMBER`\n\n"
            "Example:\n"
            "`/delete_button -1002413838031 1`"
        )
        return

    try:
        channel_id = str(int(message.command[1]))
        button_number = int(message.command[2])

        if button_number < 1:
            raise ValueError

    except ValueError:
        await message.reply_text(
            "⚠️ Invalid channel ID or button number."
        )
        return

    config = await get_button_config()
    channels = config.setdefault("channels", {})
    rows = channels.get(channel_id, [])

    flattened = [
        (row_index, button_index)
        for row_index, row in enumerate(rows)
        for button_index, _ in enumerate(row)
    ]

    if button_number > len(flattened):
        await message.reply_text(
            "❌ Button not found. Use `/list_buttons`."
        )
        return

    row_index, button_index = flattened[button_number - 1]

    rows[row_index].pop(button_index)

    channels[channel_id] = [
        row for row in rows if row
    ]

    await save_button_config(config)

    await message.reply_text(
        "✅ Button deleted from configuration."
    )


# =========================================
# CLEAR CHANNEL BUTTONS
# =========================================

@Client.on_message(
    filters.command("clear_buttons") & filters.user(ADMINS)
)
async def clear_buttons_command(client, message):
    if len(message.command) < 2:
        await message.reply_text(
            "Usage: `/clear_buttons CHANNEL_ID`"
        )
        return

    try:
        channel_id = str(int(message.command[1]))
    except ValueError:
        await message.reply_text(
            "⚠️ Invalid channel ID."
        )
        return

    config = await get_button_config()

    config.setdefault("channels", {})[channel_id] = []

    await save_button_config(config)

    await message.reply_text(
        f"✅ All configured buttons cleared for `{channel_id}`."
    )


# =========================================
# AUTOMATIC CHANNEL BUTTON HANDLER
# =========================================

@Client.on_message(
    filters.channel & filters.media
)
async def add_channel_buttons(client, message):
    try:
        config = await get_button_config()

        # Persistent ON/OFF setting
        if not config.get("enabled", True):
            return

        channel_id = str(message.chat.id)

        rows = config.get("channels", {}).get(channel_id)

        if not rows:
            return

        # Use Bot API for coloured buttons.
        # Existing button_style.py provides api_edit.
        if COLORS_ENABLED and getattr(client, "bot_token", None):
            body = message.caption or message.text or ""

            try:
                await api_edit(
                    client,
                    message.chat.id,
                    message.id,
                    rows,
                    text=body,
                    is_photo=bool(message.media),
                )
                return

            except Exception as e:
                logger.warning(
                    "Coloured button edit failed for %s/%s: %s",
                    message.chat.id,
                    message.id,
                    e,
                )

        # Fallback: regular Pyrogram buttons without colour.
        try:
            await message.edit_reply_markup(
                reply_markup=create_plain_markup(rows)
            )

        except Exception as e:
            logger.error(
                "Failed to add channel buttons to %s/%s: %s",
                message.chat.id,
                message.id,
                e,
            )

    except PyMongoError:
        logger.exception(
            "MongoDB error in automatic channel button handler."
        )

    except Exception:
        logger.exception(
            "Unexpected error in automatic channel button handler."
        )
