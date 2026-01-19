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

# MongoDB Connection (5 စက္ကန့်အတွင်း ချိတ်မရရင် timeout ပေးမည်)
client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
db = client['telegram_bot_db']
config_col = db['settings']    
backup_logs = db['backup_logs']

bot = telebot.TeleBot(BOT_TOKEN)

# ==========================================
# BACKUP LOGIC (WITH RETRY & STABILITY)
# ==========================================

def is_already_backed_up(source_chat_id, target_chat_id, message_id):
    return backup_logs.find_one({
        "source_chat": str(source_chat_id), 
        "target_chat": str(target_chat_id), 
        "msg_id": message_id
    })

def log_backup(source_chat_id, target_chat_id, message_id):
    backup_logs.insert_one({
        "source_chat": str(source_chat_id), 
        "target_chat": str(target_chat_id), 
        "msg_id": message_id, 
        "timestamp": time.time()
    })

@bot.message_handler(commands=['backup'])
def start_backup(message):
    if message.from_user.id != ADMIN_ID: return

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

        cfg = config_col.find_one({"_id": "bot_config"})
        custom_txt = cfg.get('custom_caption') if cfg else None

        for msg_id in range(start_id, end_id + 1):
            if is_already_backed_up(source_chat, target_chat, msg_id):
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
                    log_backup(source_chat, target_chat, msg_id)
                    success_count += 1
                    success = True
                    break 
                except Exception as e:
                    print(f"Error: {e}")
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
        print(f"Error: {e}")
        bot.reply_to(message, f"❌ Backup Error: {e}")
        
@bot.message_handler(commands=['clearlogs'])
def clear_backup_logs(message):
    if message.from_user.id != ADMIN_ID: return
    backup_logs.delete_many({})
    bot.reply_to(message, "🗑 Backup logs have been cleared.")

# ==========================================
# DATABASE HELPER FUNCTIONS
# ==========================================
def get_config():
    data = config_col.find_one({"_id": "bot_config"})
    if not data:
        default_channel = os.getenv('TARGET_CHANNEL_ID')
        new_data = {
            "_id": "bot_config",
            "channel_id": default_channel,
            "authorized_users": [ADMIN_ID],
            "custom_caption": None
        }
        config_col.insert_one(new_data)
        return new_data
    return data

def update_channel_id(new_id):
    config_col.update_one({"_id": "bot_config"}, {"$set": {"channel_id": new_id}})

def add_auth_user(user_id):
    config_col.update_one({"_id": "bot_config"}, {"$addToSet": {"authorized_users": user_id}})

def remove_auth_user(user_id):
    config_col.update_one({"_id": "bot_config"}, {"$pull": {"authorized_users": user_id}})

# ==========================================
# MEMORY CACHE
# ==========================================
current_config = get_config()
pending_files = {}
batch_data = {} 

# ==========================================
# WEB SERVER
# ==========================================
app = Flask('')

@app.route('/')
def home():
    return "Bot is Running with MongoDB! 🤖"

def run_http():
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 8080)))

def keep_alive():
    t = Thread(target=run_http)
    t.start()

# ==========================================
# ADMIN & AUTH COMMANDS
# ==========================================

def is_authorized(user_id):
    if user_id == ADMIN_ID: return True
    return user_id in current_config.get('authorized_users', [])

@bot.message_handler(commands=['setchannel'])
def set_channel(message):
    if message.from_user.id != ADMIN_ID: return
    try:
        parts = message.text.split()
        if len(parts) == 2:
            new_id = parts[1]
            update_channel_id(new_id)
            current_config['channel_id'] = new_id
            bot.reply_to(message, f"✅ Database Saved! Target Channel changed to `{new_id}`")
        else:
            bot.reply_to(message, "⚠️ Usage: `/setchannel -100xxxxxxx`")
    except Exception as e:
        print(f"Error: {e}")
        bot.reply_to(message, f"❌ Error: {e}")

@bot.message_handler(commands=['checkchannel'])
def check_channel(message):
    if message.from_user.id != ADMIN_ID: return
    channel_id = current_config['channel_id']
    try:
        chat = bot.get_chat(channel_id)
        chat_title = chat.title
        if chat.username:
            link = f"https://t.me/{chat.username}"
        else:
            clean_id = str(channel_id).replace("-100", "")
            link = f"https://t.me/c/{clean_id}/1"
        text = (
            f"📡 **Target Channel Info**\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📛 Name: **{chat_title}**\n"
            f"🆔 ID: `{channel_id}`\n"
            f"🔗 Link: [Click Here]({link})"
        )
    except Exception as e:
        print(f"Error: {e}")
        text = (
            f"📡 **Current ID:** `{channel_id}`\n\n"
            f"❌ Channel အချက်အလက်ကို ဆွဲယူမရပါ။\n"
            f"(Bot ကို Channel Admin ပေးထားမှ Link ထုတ်ပေးနိုင်ပါမည်)"
        )
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(commands=['auth'])
def add_user(message):
    if message.from_user.id != ADMIN_ID: return
    try:
        new_user_id = int(message.text.split()[1])
        add_auth_user(new_user_id)
        if new_user_id not in current_config['authorized_users']:
             current_config['authorized_users'].append(new_user_id)
        bot.reply_to(message, f"✅ User ID `{new_user_id}` added to Database.")
    except:
        bot.reply_to(message, "⚠️ Usage: `/auth 123456789`")

@bot.message_handler(commands=['unauth'])
def remove_user(message):
    if message.from_user.id != ADMIN_ID: return
    try:
        target_id = int(message.text.split()[1])
        if target_id == ADMIN_ID:
            bot.reply_to(message, "❌ Cannot remove Admin.")
            return
        remove_auth_user(target_id)
        if target_id in current_config['authorized_users']:
            current_config['authorized_users'].remove(target_id)
        bot.reply_to(message, f"🗑 User ID `{target_id}` removed from Database.")
    except:
        bot.reply_to(message, "Error.")

@bot.message_handler(commands=['setcaption'])
def set_custom_caption_text(message):
    if not is_authorized(message.from_user.id): return
    try:
        caption_text = message.text.split(maxsplit=1)[1]
        config_col.update_one({"_id": "bot_config"}, {"$set": {"custom_caption": caption_text}})
        current_config['custom_caption'] = caption_text
        bot.reply_to(message, f"✅ ပုံသေစာသား သတ်မှတ်ပြီးပါပြီ:\n\n`{caption_text}`", parse_mode="Markdown")
    except IndexError:
        bot.reply_to(message, "⚠️ Usage: `/setcaption Your Text Here`")

@bot.message_handler(commands=['delcaption'])
def delete_custom_caption_text(message):
    if not is_authorized(message.from_user.id): return
    config_col.update_one({"_id": "bot_config"}, {"$set": {"custom_caption": None}})
    current_config['custom_caption'] = None
    bot.reply_to(message, "🗑 ပုံသေစာသားကို ဖျက်လိုက်ပါပြီ။")

@bot.message_handler(commands=['users'])
def list_authorized_users(message):
    if message.from_user.id != ADMIN_ID: return
    user_list = current_config.get('authorized_users', [])
    text = f"👥 **Authorized Users Total: {len(user_list)}**\n"
    text += "━━━━━━━━━━━━━━━━\n"
    for uid in user_list:
        try:
            user = bot.get_chat(uid)
            name = user.first_name
            username = f"(@{user.username})" if user.username else ""
            text += f"👤 {name} {username}\n🆔 `{uid}`\n\n"
        except:
            text += f"👤 Unknown User\n🆔 `{uid}`\n\n"
    bot.reply_to(message, text, parse_mode="Markdown")

# ==========================================
# BATCH PROCESSING
# ==========================================
def process_batch(chat_id):
    if chat_id not in batch_data: return
    messages = batch_data[chat_id]['messages']
    target_channel = current_config['channel_id'] 

    if len(messages) > 1:
        total_files = len(messages)
        bot.send_message(chat_id, f"✅ ဇာတ်ကား {total_files} ကား လက်ခံရရှိသည်။ Channel သို့ ပို့နေပါပြီ...")
        success_count = 0
        failed_messages = []
        for msg in messages:
            try:
                original_caption = msg.caption if msg.caption else ""
                custom_txt = current_config.get('custom_caption', "")
                if custom_txt:
                    max_original_len = 1024 - len(custom_txt) - 2
                    safe_original = original_caption[:max_original_len]
                    final_caption = f"{safe_original}\n\n{custom_txt}"
                else:
                    final_caption = original_caption[:1024]
                bot.copy_message(chat_id=target_channel, from_chat_id=chat_id, message_id=msg.message_id, caption=final_caption)
                success_count += 1
                time.sleep(3)
            except Exception as e:
                print(f"Error sending msg {msg.message_id}: {e}")
                failed_messages.append(msg)
        
        report_text = f"📊 **Batch Report**\n✅ Success: {success_count}\n❌ Failed: {len(failed_messages)}"
        bot.send_message(chat_id, report_text, parse_mode="Markdown")
    
    elif len(messages) == 1:
        msg = messages[0]
        pending_files[chat_id] = {'message_id': msg.message_id, 'from_chat_id': chat_id}
        bot.reply_to(msg, "✏️ **ဒီကားအတွက် Caption ရေးပို့ပေးပါ...**")

    if chat_id in batch_data: del batch_data[chat_id]

@bot.message_handler(content_types=['video', 'document', 'photo'])
def receive_video(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔️ You are not authorized.")
        return
    chat_id = message.chat.id
    if chat_id in batch_data and batch_data[chat_id]['timer']:
        batch_data[chat_id]['timer'].cancel()
    if chat_id not in batch_data:
        batch_data[chat_id] = {'messages': [], 'timer': None}
    batch_data[chat_id]['messages'].append(message)
    batch_data[chat_id]['timer'] = Timer(2.0, process_batch, [chat_id])
    batch_data[chat_id]['timer'].start()

@bot.message_handler(func=lambda m: m.chat.id in pending_files, content_types=['text'])
def receive_caption(message):
    if not is_authorized(message.from_user.id): return
    chat_id = message.chat.id
    user_input = message.text
    file_info = pending_files.get(chat_id)
    target_channel = current_config['channel_id']
    if not file_info: return
    try:
        custom_txt = current_config.get('custom_caption')
        if custom_txt:
            final_caption = f"{user_input}\n\n{custom_txt}"
            if len(final_caption) > 1024:
                max_input_len = 1024 - len(custom_txt) - 4
                final_caption = f"{user_input[:max_input_len]}...\n\n{custom_txt}"
        else:
            final_caption = user_input[:1024]

        bot.copy_message(chat_id=target_channel, from_chat_id=file_info['from_chat_id'], message_id=file_info['message_id'], caption=final_caption)
        bot.reply_to(message, "✅ Channel သို့ ပို့ပြီးပါပြီ။")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")
    if chat_id in pending_files: del pending_files[chat_id]

@bot.message_handler(func=lambda m: m.text and "t.me/" in m.text)
def handle_post_link(message):
    if not is_authorized(message.from_user.id): return
    link = message.text.strip()
    match = re.search(r"t\.me/([^/]+)/(\d+)", link)
    if match:
        source_username = match.group(1)
        message_id = int(match.group(2))
        target_channel = current_config['channel_id']
        try:
            bot.copy_message(chat_id=target_channel, from_chat_id=f"@{source_username}", message_id=message_id)
            bot.reply_to(message, "✅ Sent.")
        except Exception as e:
            bot.reply_to(message, f"❌ Error: {e}")

if __name__ == "__main__":
    keep_alive()
    print("🤖 Bot Started with MongoDB Support...")
    bot.infinity_polling()
