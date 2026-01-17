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

# MongoDB Connection
client = MongoClient(MONGO_URL)
db = client['telegram_bot_db'] # Database Name
config_col = db['settings']    # Collection Name

bot = telebot.TeleBot(BOT_TOKEN)

# ==========================================
# DATABASE HELPER FUNCTIONS
# ==========================================
def get_config():
    """Database ထဲက Config ကို ဆွဲယူမည်။ မရှိသေးရင် အသစ်ဆောက်မည်။"""
    data = config_col.find_one({"_id": "bot_config"})
    
    if not data:
        # DB မှာ မရှိသေးရင် Env Var က Default တွေကို ယူပြီး DB မှာ အသစ်ဆောက်မယ်
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
    """Channel ID အသစ်ကို DB မှာ သိမ်းမည်"""
    config_col.update_one({"_id": "bot_config"}, {"$set": {"channel_id": new_id}})

def add_auth_user(user_id):
    """User အသစ်ကို DB မှာ ထည့်မည်"""
    config_col.update_one({"_id": "bot_config"}, {"$addToSet": {"authorized_users": user_id}})

def remove_auth_user(user_id):
    """User ကို DB မှ ဖယ်ရှားမည်"""
    config_col.update_one({"_id": "bot_config"}, {"$pull": {"authorized_users": user_id}})

# ==========================================
# MEMORY CACHE (DB ကို ခဏခဏ မခေါ်ရအောင်)
# ==========================================
# Bot စrun တာနဲ့ DB ထဲက Data ကို ဆွဲတင်ထားမယ်
current_config = get_config()

# Single file တွေအတွက် caption စောင့်ဖို့
pending_files = {}
# Batch (အများကြီး) လာရင် ခဏထိန်းထားဖို့
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
    # Memory ထဲက List ကိုပဲ စစ်မယ် (မြန်အောင်လို့)
    # Admin ID ကိုတော့ အမြဲတမ်း ခွင့်ပြုမယ်
    if user_id == ADMIN_ID: return True
    return user_id in current_config.get('authorized_users', [])

@bot.message_handler(commands=['setchannel'])
def set_channel(message):
    if message.from_user.id != ADMIN_ID: return

    try:
        parts = message.text.split()
        if len(parts) == 2:
            new_id = parts[1]
            
            # 1. DB မှာ ပြင်မယ်
            update_channel_id(new_id)
            # 2. Memory မှာ ပြင်မယ် (ချက်ချင်းသက်ရောက်အောင်)
            current_config['channel_id'] = new_id
            
            bot.reply_to(message, f"✅ Database Saved! Target Channel changed to `{new_id}`")
        else:
            bot.reply_to(message, "⚠️ Usage: `/setchannel -100xxxxxxx`")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

@bot.message_handler(commands=['checkchannel'])
def check_channel(message):
    if message.from_user.id != ADMIN_ID: return
    
    channel_id = current_config['channel_id']
    
    try:
        # Telegram API ကို လှမ်းမေးပြီး Channel အချက်အလက်ယူမယ်
        chat = bot.get_chat(channel_id)
        chat_title = chat.title
        
        if chat.username:
            # Public Channel ဆိုရင် username နဲ့ Link လုပ်မယ်
            link = f"https://t.me/{chat.username}"
        else:
            # Private Channel ဆိုရင် ID နဲ့ Link ဖန်တီးမယ်
            # -100 ကို ဖြုတ်ပြီး /c/ ထည့်ရပါတယ်
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
        # Bot က Channel ထဲမှာ Admin မဟုတ်ရင် Detail ကြည့်လို့မရပါဘူး
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
        
        # DB & Memory Update
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

        # DB & Memory Update
        remove_auth_user(target_id)
        if target_id in current_config['authorized_users']:
            current_config['authorized_users'].remove(target_id)

        bot.reply_to(message, f"🗑 User ID `{target_id}` removed from Database.")
    except:
        bot.reply_to(message, "Error.")

# ==========================================
# CAPTION SETTINGS (NEW)
# ==========================================
@bot.message_handler(commands=['setcaption'])
def set_custom_caption_text(message):
    if not is_authorized(message.from_user.id): return

    try:
        # /setcaption နောက်က စာသားကို ယူမည်
        caption_text = message.text.split(maxsplit=1)[1]
        
        # DB & Memory Update
        config_col.update_one({"_id": "bot_config"}, {"$set": {"custom_caption": caption_text}})
        current_config['custom_caption'] = caption_text
        
        bot.reply_to(message, f"✅ ပုံသေစာသား သတ်မှတ်ပြီးပါပြီ:\n\n`{caption_text}`", parse_mode="Markdown")
    except IndexError:
        bot.reply_to(message, "⚠️ Usage: `/setcaption Your Text Here`")

@bot.message_handler(commands=['delcaption'])
def delete_custom_caption_text(message):
    if not is_authorized(message.from_user.id): return

    # DB & Memory Update (None ပြန်လုပ်မည်)
    config_col.update_one({"_id": "bot_config"}, {"$set": {"custom_caption": None}})
    current_config['custom_caption'] = None
    
    bot.reply_to(message, "🗑 ပုံသေစာသားကို ဖျက်လိုက်ပါပြီ။")

# Authorized Users စာရင်းကို ကြည့်ရန်
# သုံးပုံ: /users
@bot.message_handler(commands=['users'])
def list_authorized_users(message):
    if message.from_user.id != ADMIN_ID: return
    
    user_list = current_config.get('authorized_users', [])
    
    text = f"👥 **Authorized Users Total: {len(user_list)}**\n"
    text += "━━━━━━━━━━━━━━━━\n"
    
    for uid in user_list:
        try:
            # User ID ကနေ နာမည်လှမ်းစစ်မယ်
            user = bot.get_chat(uid)
            name = user.first_name
            # Username ရှိရင် ထည့်ပြမယ်၊ မရှိရင် ဗလာထားမယ်
            username = f"(@{user.username})" if user.username else ""
            
            text += f"👤 {name} {username}\n🆔 `{uid}`\n\n"
        except:
            # User က Bot ကို Block ထားရင် နာမည်ပေါ်မှာ မဟုတ်ပါ
            text += f"👤 Unknown User\n🆔 `{uid}`\n\n"
            
    bot.reply_to(message, text, parse_mode="Markdown")

# BATCH PROCESSING LOGIC (UPDATED)
# ==========================================
def process_batch(chat_id):
    if chat_id not in batch_data:
        return

    messages = batch_data[chat_id]['messages']
    target_channel = current_config['channel_id'] 

    if len(messages) > 1:
        total_files = len(messages)
        bot.send_message(chat_id, f"✅ ဇာတ်ကား {total_files} ကား လက်ခံရရှိသည်။ Channel သို့ ပို့နေပါပြီ...\n(ခဏစောင့်ပါ၊ ပြီးရင် Report ပြန်ပို့ပေးပါမည်)")
        
        success_count = 0
        failed_messages = []

        for msg in messages:
            try:
                original_caption = msg.caption if msg.caption else ""
                custom_txt = current_config.get('custom_caption') if current_config.get('custom_caption') else ""
                
                # ၁၀၂၄ limit အတွက် တွက်ချက်ခြင်း
                if custom_txt:
                    max_original_len = 1024 - len(custom_txt) - 2
                    safe_original = original_caption[:max_original_len]
                    final_caption = f"{safe_original}\n\n{custom_txt}"
                else:
                    final_caption = original_caption[:1024]

                bot.copy_message(
                    chat_id=target_channel,
                    from_chat_id=chat_id,
                    message_id=msg.message_id,
                    caption=final_caption
                )
                success_count += 1 # အောင်မြင်မှုအရေအတွက်ပေါင်းရန်
                time.sleep(3) # Telegram Flood limit မမိအောင် ၃ စက္ကန့်ခြားသည်
            except Exception as e:
                print(f"Error sending msg {msg.message_id}: {e}")
                failed_messages.append(msg)
        
        report_text = (
            f"📊 **Batch Report**\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📥 Total: {total_files}\n"
            f"✅ Success: {success_count}\n"
            f"❌ Failed: {len(failed_messages)}"
        )
        bot.send_message(chat_id, report_text, parse_mode="Markdown")

        if failed_messages:
            bot.send_message(chat_id, "⚠️ **အောက်ပါဖိုင်များသည် Error တက်ပြီး Channel သို့ မရောက်ပါ:**")
            for fail_msg in failed_messages:
                try:
                    bot.reply_to(fail_msg, "❌ ဒီဖိုင် Error တက်သွားလို့ Channel ကို မရောက်ပါဘူး။")
                    time.sleep(1)
                except:
                    pass
    
    elif len(messages) == 1:
        msg = messages[0]
        pending_files[chat_id] = {
            'message_id': msg.message_id,
            'from_chat_id': chat_id
        }
        bot.reply_to(msg, "✏️ **ဒီကားအတွက် Caption ရေးပို့ပေးပါ...**")

    if chat_id in batch_data:
        del batch_data[chat_id]
# ==========================================
# HANDLERS
# ==========================================

@bot.message_handler(content_types=['video', 'document', 'photo'])
def receive_video(message):
    # Check Permission
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔️ You are not authorized. Bot ကိုအသုံးပြုနိုင်ရန် admin- @moviestoreadmin ထံ ဆက်သွယ်ဝယ်ယူပါ။ ")
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
        custom_txt = current_config.get('custom_caption') # ပုံသေစာသားကို ယူသည်
        
        # Telegram ရဲ့ limit က 1024 characters ဖြစ်သည်
        if custom_txt:
            # Custom caption အတွက် နေရာဖယ်ပြီး ကျန်တာကိုပဲ original caption ထဲက ယူမည်
            # '\n\n' (၂ လုံး) အတွက်ပါ ထည့်တွက်ထားသည်
            max_input_len = 1024 - len(custom_txt) - 2
            safe_input = user_input[:max_input_len]
            final_caption = f"{safe_input}\n\n{custom_txt}"
        else:
            # Custom caption မရှိရင် စာသား ၁၀၂၄ လုံးအထိပဲ ယူမည်
            final_caption = user_input[:1024]

        bot.copy_message(
            chat_id=target_channel,
            from_chat_id=file_info['from_chat_id'],
            message_id=file_info['message_id'],
            caption=final_caption
        )
        bot.reply_to(message, "✅ Channel သို့ ပို့ပြီးပါပြီ။")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")
    
    del pending_files[chat_id]

# ==========================================
# LINK HANDLING
# ==========================================
@bot.message_handler(func=lambda m: m.text and "t.me/" in m.text)
def handle_post_link(message):
    if not is_authorized(message.from_user.id): return
    if message.chat.id in pending_files: return
    
    link = message.text.strip()
    match = re.search(r"t\.me/([^/]+)/(\d+)", link)
    target_channel = current_config['channel_id']
    
    if match:
        source_username = match.group(1)
        message_id = int(match.group(2))
        source_chat = f"@{source_username}"
        
        bot.reply_to(message, "🔄 Link processing...")

        try:
            bot.copy_message(
                chat_id=target_channel,
                from_chat_id=source_chat,
                message_id=message_id,
                caption=message.text,
                parse_mode="Markdown"
            )
            bot.reply_to(message, "✅ Sent.")
        except Exception as e:
            bot.reply_to(message, f"❌ Error: {e}")

# ==========================================
# START
# ==========================================
if __name__ == "__main__":
    keep_alive()
    print("🤖 Bot Started with MongoDB Support...")
    bot.infinity_polling()







