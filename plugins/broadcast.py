# ----------------- User Broadcast -----------------
##This feature is developed by 【𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫_𝐁𝐨𝐲™(𝓐𝓷𝓴𝓲𝓽_𝓜𝓮𝓮𝓷𝓪😝)】
# Don't remove the my credit please 🙏
@Client.on_message(filters.command("broadcast") & filters.user(ADMINS) & filters.reply)
async def broadcast_users(bot, message):
    if lock.locked():
        return await message.reply("⚠️ Another broadcast is in progress. Please wait...")

    ask = await message.reply(
        "<b>Do you want to pin this message in users?</b>",
        reply_markup=ReplyKeyboardMarkup([["Yes", "No"]], one_time_keyboard=True, resize_keyboard=True)
    )
    try:
        user_response = await bot.listen(chat_id=message.chat.id, user_id=message.from_user.id, timeout=60)
    except asyncio.TimeoutError:
        await ask.delete()
        return await message.reply("❌ Timed out. Broadcast cancelled.")
    await ask.delete()

    if user_response.text not in ("Yes", "No"):
        return await message.reply("❌ Invalid input. Broadcast cancelled.")
    is_pin = user_response.text == "Yes"

    # Ask for auto-delete time
    ask_time = await message.reply("<b>Enter auto-delete time in seconds (0 to disable auto-delete):</b>")
    try:
        time_response = await bot.listen(chat_id=message.chat.id, user_id=message.from_user.id, timeout=60)
        auto_delete_time = int(time_response.text)
    except:
        await ask_time.delete()
        return await message.reply("❌ Invalid or no response. Broadcast cancelled.")
    await ask_time.delete()

    b_msg = message.reply_to_message

    # ✅ Get all users
    users = []
    async for user in db.get_all_users():
        users.append(user)
    total_users = len(users)

    if total_users == 0:
        return await message.reply("⚠️ No users found in database.")

    status_msg = await message.reply_text("📤 <b>Broadcasting your message...</b>")
    success = blocked = deleted = failed = 0
    start_time = time.time()
    cancelled = False

    # ✅ Updated send_message version (with pin + auto-delete)
    async def send_message_with_features(user):
        try:
            sent_msg = None
            reply_markup = b_msg.reply_markup
            if b_msg.text:
                sent_msg = await bot.send_message(int(user["id"]), b_msg.text, reply_markup=reply_markup)
            elif b_msg.photo:
                sent_msg = await bot.send_photo(int(user["id"]), b_msg.photo.file_id, caption=b_msg.caption, reply_markup=reply_markup)
            elif b_msg.video:
                sent_msg = await bot.send_video(int(user["id"]), b_msg.video.file_id, caption=b_msg.caption, reply_markup=reply_markup)
            elif b_msg.document:
                sent_msg = await bot.send_document(int(user["id"]), b_msg.document.file_id, caption=b_msg.caption, reply_markup=reply_markup)

            if sent_msg:
                # ✅ Pin with small delay
                if is_pin:
                    try:
                        await asyncio.sleep(0.5)
                        await sent_msg.pin(disable_notification=True)
                    except:
                        pass

                # ✅ Auto delete
                if auto_delete_time > 0:
                    asyncio.create_task(auto_delete(sent_msg, auto_delete_time))

                await track_message(sent_msg, "user")
                return "Success"

        except Exception as e:
            err = str(e).lower()
            if "blocked" in err:
                return "Blocked"
            elif "chat not found" in err or "deleted" in err:
                return "Deleted"
            else:
                return "Error"

    # ✅ Broadcast loop with cancellation & progress
    async with lock:
        for i in range(0, total_users, 50):
            if temp.B_USERS_CANCEL:
                temp.B_USERS_CANCEL = False
                cancelled = True
                break

            batch = users[i:i + 50]
            results = await asyncio.gather(*[send_message_with_features(user) for user in batch])

            for res in results:
                if res == "Success": success += 1
                elif res == "Blocked": blocked += 1
                elif res == "Deleted": deleted += 1
                elif res == "Error": failed += 1

            done = i + len(batch)
            elapsed = get_readable_time(time.time() - start_time)
            await status_msg.edit(
                f"📣 <b>Broadcast Progress:</b>\n\n"
                f"👥 Total: <code>{total_users}</code>\n"
                f"✅ Done: <code>{done}</code>\n"
                f"📬 Success: <code>{success}</code>\n"
                f"⛔ Blocked: <code>{blocked}</code>\n"
                f"🗑️ Deleted: <code>{deleted}</code>\n"
                f"❌ Failed: <code>{failed}</code>\n"
                f"⏱️ Time: {elapsed}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ CANCEL", callback_data="broadcast_cancel#users")]])
            )
            await asyncio.sleep(0.2)

    elapsed = get_readable_time(time.time() - start_time)
    final_status = (
        f"{'❌ <b>Broadcast Cancelled.</b>' if cancelled else '✅ <b>Broadcast Completed.</b>'}\n\n"
        f"🕒 Time: {elapsed}\n"
        f"👥 Total: <code>{total_users}</code>\n"
        f"📬 Success: <code>{success}</code>\n"
        f"⛔ Blocked: <code>{blocked}</code>\n"
        f"🗑️ Deleted: <code>{deleted}</code>\n"
        f"❌ Failed: <code>{failed}</code>\n\n"
        f"🌿<blockquote> Maintained by :【𝐃𝐞𝐯𝐞𝐥𝐨𝐩𝐞𝐫_𝐁𝐨𝐲™(𝓐𝓷𝓴𝓲𝓽_𝓜𝓮𝓮𝓷𝓪😝)】</blockquote>"
    )
    await status_msg.edit(final_status)
