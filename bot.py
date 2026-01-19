import telebot
import os
import re
from flask import Flask
from threading import Thread, Timer
import time
from pymongo import MongoClient

# ==========================================
# CONFIGURATION & DATABASE CONNECTION
# ==========================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
MONGO_URL = os.getenv('MONGO_URL')

client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
db = client['telegram_bot_db']
config_col = db['settings']    
backup_logs = db['backup_logs']

bot = telebot.TeleBot(BOT_TOKEN)

# ==========================================
# DATABASE HELPER FUNCTIONS (USER-BASED)
# ==========================================

def get_user_config(user_id):
    """User တစ်ဦးချင်းစီအတွက် Setting ကို Database ကနေ ဆွဲယူရန်"""
    data = config_col.find_one({"_id": str(user_id)})
    if not data:
        default_channel = os.getenv('TARGET_CHANNEL_ID')
        new_data = {
            "_id": str(user_id),
            "channel_id": default_channel,
            "authorized_users": [ADMIN_ID],
            "custom_caption": None
        }
        config_col.insert_one(new_data)
        return new_data
    return data

def update_user_setting(user_id, field, value):
    """User တစ်ဦးချင်းစီရဲ့ field တွေကို Update လုပ်ရန်"""
    config_col.update_one({"_id": str(user_id)}, {"$set": {field: value}}, upsert=True)

# ==========================================
# BACKUP LOGIC (WITH USER_ID SUPPORT)
# ==========================================

def is_already_backed_up(user_id, source_chat_id, target_chat_id, message_id):
    """ကိုယ့် Log ထဲမှာပဲ စစ်ဆေးရန်"""
    return backup_logs.find_one({
        "user_id": str(user_id),
        "source_chat": str(source_chat_id), 
        "target_chat": str(target_chat_id), 
        "msg_id": message_id
    })

def log_backup(user_id, source_chat_id, target_chat_id, message_id):
    """Log မှတ်တဲ့အခါ user_id ပါ တွဲမှတ်ရန်"""
    backup_logs.insert_one({
        "user_id": str(user_id),
        "source_chat": str(source_chat_id), 
        "target_chat": str(target_chat_id), 
        "msg_id": message_id, 
        "timestamp": time.time()
    })

@bot.message_handler(commands=['backup'])
def start_backup(message):
    user_id = message.from_user.id
    if not is_authorized(user_id): return

    try:
        parts = message.text.split()
        if len(parts) < 5:
            bot.reply_to(message, "⚠️ Usage: `/backup [SourceID] [TargetID] [StartID] [EndID]`")
            return

        source_chat = parts[1]
        target_chat = parts[2]
        start_id = int(parts[3])
        end_id = int(parts[4])

        status_msg = bot.reply_to(message, "🚀 Backup Process စတင်ပါပြီ...")
        
        success_count = 0
        fail_count = 0
        skip_count = 0
        failed_ids = []

        # User-specific config ဆွဲယူခြင်း
        cfg = get_user_config(user_id)
        custom_txt = cfg.get('custom_caption')

        for msg_id in range(start_id, end_id + 1):
            if is_already_backed_up(user_id, source_chat, target_chat, msg_id):
                skip_count += 1
                continue

            success = False
            for attempt in range(3):
                try:
                    bot.copy_message(
                        chat_id=target_chat,
                        from_chat_id=source_chat,
                        message_id=msg_id,
                        caption=custom_txt if custom_txt else None
                    )
                    log_backup(user_id, source_chat, target_chat, msg_id)
                    success_count += 1
                    success = True
                    break 
                except Exception as e:
                    if attempt < 2:
                        time.sleep(5) 
                    else:
                        fail_count += 1
                        failed_ids.append(str(msg_id))

            if success:
                time.sleep(2.5)
            
            if (success_count + skip_count + fail_count) % 5 == 0:
                try:
                    bot.edit_message_text(
                        f"🔄 Progress: {msg_id - start_id + 1}/{end_id - start_id + 1}\n✅ Done: {success_count} | ⏭ Skip: {skip_count}",
                        chat_id=message.chat.id,
                        message_id=status_msg.message_id
                    )
                except: pass

        final_text = (
            f"📊 **Backup Result**\n"
            f"✅ Success: {success_count}\n"
            f"⏭ Skipped (Dup): {skip_count}\n"
            f"❌ Failed: {fail_count}"
        )
        bot.send_message(message.chat.id, final_text, parse_mode="Markdown")
        
        if failed_ids:
            bot.send_message(message.chat.id, f"⚠️ **Error IDs:** `{', '.join(failed_ids[:30])}`")

    except Exception as e:
        bot.reply_to(message, f"❌ Backup Error: {e}")
        
@bot.message_handler(commands=['clearlogs'])
def clear_backup_logs(message):
    user_id = message.from_user.id
    parts = message.text.split()
    
    # Admin ဖြစ်ပြီး User ID သတ်မှတ်ထားရင် အဲ့ဒီ User ရဲ့ log ကိုဖျက်မယ်
    if user_id == ADMIN_ID and len(parts) == 2:
        target_uid = parts[1]
        backup_logs.delete_many({"user_id": str(target_uid)})
        bot.reply_to(message, f"🗑 Backup logs for User `{target_uid}` have been cleared.")
    # ပုံမှန် User ဆိုရင် ကိုယ့် log ကိုပဲ ဖျက်ခွင့်ပေးမယ်
    elif is_authorized(user_id):
        backup_logs.delete_many({"user_id": str(user_id)})
        bot.reply_to(message, "🗑 Your backup logs have been cleared.")

# ==========================================
# AUTH & CACHE LOGIC
# ==========================================
def is_authorized(user_id):
    if user_id == ADMIN_ID: return True
    # Admin မဟုတ်ရင် DB ကနေ User တစ်ဦးချင်းစီရဲ့ settings ထဲက authorized_users ကိုစစ်မယ်
    cfg = get_user_config(ADMIN_ID) # Global Admin Settings ကနေ list ယူခြင်း
    return user_id in cfg.get('authorized_users', [])

# ==========================================
# WEB SERVER (KEEPALIVE)
# ==========================================
app = Flask('')

@app.route('/')
def home():
    return "Bot is Running with Multi-User MongoDB Support! 🤖"

def run_http():
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 8080)))

def keep_alive():
    t = Thread(target=run_http)
    t.start()

# ==========================================
# ADMIN & USER COMMANDS
# ==========================================

@bot.message_handler(commands=['setchannel'])
def set_channel(message):
    user_id = message.from_user.id
    if not is_authorized(user_id): return
    try:
        parts = message.text.split()
        if len(parts) == 2:
            new_id = parts[1]
            update_user_setting(user_id, "channel_id", new_id)
            bot.reply_to(message, f"✅ Target Channel changed to `{new_id}` for you.")
        else:
            bot.reply_to(message, "⚠️ Usage: `/setchannel -100xxxxxxx`")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

@bot.message_handler(commands=['checkchannel'])
def check_channel(message):
    user_id = message.from_user.id
    if not is_authorized(user_id): return
    cfg = get_user_config(user_id)
    channel_id = cfg.get('channel_id')
    try:
        chat = bot.get_chat(channel_id)
        chat_title = chat.title
        link = f"https://t.me/c/{str(channel_id).replace('-100', '')}/1" if not chat.username else f"https://t.me/{chat.username}"
        text = f"📡 **Your Target Channel Info**\n📛 Name: **{chat_title}**\n🆔 ID: `{channel_id}`\n🔗 Link: [Click Here]({link})"
    except:
        text = f"📡 **Current ID:** `{channel_id}`\n❌ Channel Access Error."
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(commands=['setcaption'])
def set_custom_caption_text(message):
    user_id = message.from_user.id
    if not is_authorized(user_id): return
    try:
        caption_text = message.text.split(maxsplit=1)[1]
        update_user_setting(user_id, "custom_caption", caption_text)
        bot.reply_to(message, f"✅ Your Custom Caption set to:\n\n`{caption_text}`", parse_mode="Markdown")
    except IndexError:
        bot.reply_to(message, "⚠️ Usage: `/setcaption Your Text Here`")

@bot.message_handler(commands=['delcaption'])
def delete_custom_caption_text(message):
    user_id = message.from_user.id
    if not is_authorized(user_id): return
    update_user_setting(user_id, "custom_caption", None)
    bot.reply_to(message, "🗑 Your custom caption has been deleted.")

@bot.message_handler(commands=['auth'])
def add_user(message):
    if message.from_user.id != ADMIN_ID: return
    try:
        new_user_id = int(message.text.split()[1])
        config_col.update_one({"_id": str(ADMIN_ID)}, {"$addToSet": {"authorized_users": new_user_id}}, upsert=True)
        bot.reply_to(message, f"✅ User ID `{new_user_id}` authorized.")
    except:
        bot.reply_to(message, "⚠️ Usage: `/auth 123456789`")

# ==========================================
# BATCH & MESSAGE HANDLING
# ==========================================
pending_files = {}
batch_data = {}

def process_batch(chat_id, user_id):
    if chat_id not in batch_data: return
    messages = batch_data[chat_id]['messages']
    cfg = get_user_config(user_id)
    target_channel = cfg.get('channel_id')

    if len(messages) > 1:
        bot.send_message(chat_id, f"✅ Processing {len(messages)} files for your channel...")
        for msg in messages:
            try:
                original_caption = msg.caption if msg.caption else ""
                custom_txt = cfg.get('custom_caption', "")
                final_caption = f"{original_caption}\n\n{custom_txt}"[:1024] if custom_txt else original_caption[:1024]
                bot.copy_message(chat_id=target_channel, from_chat_id=chat_id, message_id=msg.message_id, caption=final_caption)
                time.sleep(3)
            except Exception as e: print(f"Error: {e}")
        bot.send_message(chat_id, "📊 Batch process completed.")
    
    elif len(messages) == 1:
        msg = messages[0]
        pending_files[chat_id] = {'message_id': msg.message_id, 'from_chat_id': chat_id, 'user_id': user_id}
        bot.reply_to(msg, "✏️ **Please send caption for this file...**")

    if chat_id in batch_data: del batch_data[chat_id]

@bot.message_handler(content_types=['video', 'document', 'photo'])
def receive_video(message):
    user_id = message.from_user.id
    if not is_authorized(user_id): return
    chat_id = message.chat.id
    if chat_id in batch_data and batch_data[chat_id]['timer']:
        batch_data[chat_id]['timer'].cancel()
    if chat_id not in batch_data:
        batch_data[chat_id] = {'messages': [], 'timer': None}
    batch_data[chat_id]['messages'].append(message)
    batch_data[chat_id]['timer'] = Timer(2.0, process_batch, [chat_id, user_id])
    batch_data[chat_id]['timer'].start()

@bot.message_handler(func=lambda m: m.chat.id in pending_files, content_types=['text'])
def receive_caption(message):
    user_id = message.from_user.id
    if not is_authorized(user_id): return
    chat_id = message.chat.id
    file_info = pending_files.get(chat_id)
    if not file_info: return
    
    cfg = get_user_config(user_id)
    target_channel = cfg.get('channel_id')
    custom_txt = cfg.get('custom_caption')
    final_caption = f"{message.text}\n\n{custom_txt}"[:1024] if custom_txt else message.text[:1024]

    try:
        bot.copy_message(chat_id=target_channel, from_chat_id=file_info['from_chat_id'], message_id=file_info['message_id'], caption=final_caption)
        bot.reply_to(message, "✅ Sent to your channel.")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")
    del pending_files[chat_id]

@bot.message_handler(func=lambda m: m.text and "t.me/" in m.text)
def handle_post_link(message):
    user_id = message.from_user.id
    if not is_authorized(user_id): return
    match = re.search(r"t\.me/([^/]+)/(\d+)", message.text)
    if match:
        cfg = get_user_config(user_id)
        try:
            bot.copy_message(chat_id=cfg.get('channel_id'), from_chat_id=f"@{match.group(1)}", message_id=int(match.group(2)))
            bot.reply_to(message, "✅ Sent.")
        except Exception as e:
            bot.reply_to(message, f"❌ Error: {e}")

if __name__ == "__main__":
    keep_alive()
    print("🤖 Multi-User Bot Started...")
    bot.infinity_polling()
