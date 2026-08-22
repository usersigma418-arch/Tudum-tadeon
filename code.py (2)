import os, re, io, json, time, logging, asyncio, html as html_mod, signal
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

import requests, aiohttp
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from mutagen.mp4 import MP4, MP4Cover

# ─── CONFIG ───────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]
PORT = int(os.environ.get("PORT", 8080))
API_BASE = "http://songscrap.vercel.app/api"
GENIUS_TOKEN = "w-XTArszGpAQaaLu-JlViwy1e-0rxx4dvwqQzOEtcmmpYndHm_nkFTvAB5BsY-ww"
PER_PAGE = 5
MAX_SIZE = 45 * 1024 * 1024

# ─── SETUP ─────────────────────────────────────
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
app = Flask(__name__)

# ─── THESE WILL BE SET AFTER INIT ──────────────
bot_app = None
bot_loop = None
executor = ThreadPoolExecutor(max_workers=2)

# ─── HELPERS ─────────────────────────────────────
def clean_url(url):
    return re.sub(r'[\x00-\x1f\x7f-\x9f]', '', url)

def fmt_dur(s):
    return str(timedelta(seconds=int(s))).lstrip("0:").lstrip("0") or "0"

def best_img(images):
    for q in ["500x500", "150x150", "50x50"]:
        for img in images:
            if img.get("quality") == q:
                return img["url"]
    return None

def best_audio(urls):
    for q in ["320kbps", "160kbps", "96kbps", "48kbps", "12kbps"]:
        for item in urls:
            if item["quality"] == q:
                return item
    return urls[0] if urls else None

def clean(s):
    return html_mod.unescape(s.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))

def fmt_count(n):
    if n >= 10000000: return f"{n/10000000:.1f}Cr"
    if n >= 100000: return f"{n/100000:.1f}L"
    if n >= 1000: return f"{n/1000:.1f}K"
    return str(n)

async def dl_file(url):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=120)) as r:
                if r.status == 200:
                    data = await r.read()
                    return data if len(data) <= MAX_SIZE else None
    except Exception as e:
        logger.error(f"DL fail: {e}")
    return None

async def fetch_lyrics(song_title, artist):
    try:
        url = "https://api.genius.com/search"
        headers = {"Authorization": f"Bearer {GENIUS_TOKEN}"}
        params = {"q": f"{song_title} {artist}"}
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=headers, params=params) as r:
                if r.status == 200:
                    data = await r.json()
                    hits = data.get("response", {}).get("hits", [])
                    if hits: return hits[0]["result"]["url"]
    except: pass
    return None

# ─── UI ──────────────────────────────────────────
def search_text(songs, page=0):
    start = page * PER_PAGE
    end = min(start + PER_PAGE, len(songs))
    lines = [f"🎵 **── SEARCH RESULTS ──**\n━━━━━━━━━━━━━━━\n"]
    for i in range(start, end):
        s = songs[i]
        t = clean(s.get("title", "?"))
        a = clean(s.get("primaryArtists", s.get("singers", "?")))
        ab = clean(s.get("album", ""))
        lang = s.get("language", "").upper()
        lines.append(f"**{i+1}.** `{t}`\n   🎤 _{a}_  💿 {ab}  🌐 {lang}\n")
    lines.append(f"━━━━━━━━━━━━━━━\n📄 Page {page+1}/{(len(songs)+PER_PAGE-1)//PER_PAGE}  •  🎶 {len(songs)} songs")
    return "\n".join(lines)

def search_kb(songs, page=0):
    start = page * PER_PAGE
    end = min(start + PER_PAGE, len(songs))
    tp = (len(songs) + PER_PAGE - 1) // PER_PAGE
    kb = []
    for i in range(start, end):
        t = clean(songs[i].get("title", "?")[:30])
        kb.append([InlineKeyboardButton(f"🎵 {t}", callback_data=f"s_{songs[i]['id']}_{i}")])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"p_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{tp}", callback_data="x"))
    if page < tp-1: nav.append(InlineKeyboardButton("➡️", callback_data=f"p_{page+1}"))
    kb.append(nav)
    kb.append([InlineKeyboardButton("❌ Close", callback_data="c"), InlineKeyboardButton("🔄 New", callback_data="n")])
    return InlineKeyboardMarkup(kb)

def song_card(sd):
    t = clean(sd.get("name", "?"))
    al = sd.get("artists", {}).get("primary", [])
    ar = ", ".join(a.get("name","") for a in al) or "?"
    ab = clean(sd.get("album",{}).get("name","?"))
    lb = sd.get("label","?")
    du = fmt_dur(sd.get("duration",0))
    yr = sd.get("year","")
    lang = sd.get("language","").upper()
    pc = fmt_count(sd.get("playCount",0))
    ex = "🔞" if sd.get("explicitContent") else "✅"
    return (
        f"🎧 **── NOW PLAYING ──**\n━━━━━━━━━━━━━━\n\n"
        f"🎵 **{t}**\n👤 _{ar}_\n💿 {ab}\n🏷 {lb}\n\n"
        f"⏱ {du}  📅 {yr}  🌐 {lang}\n👁 {pc} plays  {ex}\n"
        f"━━━━━━━━━━━━━━\n📥 Processing..."
    )

# ─── HANDLERS ─────────────────────────────────
async def start(upd, ctx):
    u = upd.effective_user
    await upd.message.reply_text(
        f"🌟 **Namaste {u.first_name}!** 🌟\n\n"
        f"🎵 JioSaavn Music Bot — koi bhi gana search karo!\n\n"
        f"🔍 `/search <name>` — Search\n"
        f"💡 `/search Awarapan`\n\n"
        f"⚡ Free • No ads • Direct download",
        parse_mode="Markdown"
    )

async def search(upd, ctx):
    q = " ".join(ctx.args)
    if not q:
        return await upd.message.reply_text("❌ `/search <song name>` likh bhai!", parse_mode="Markdown")

    await ctx.bot.send_chat_action(upd.effective_chat.id, "typing")
    msg = await upd.message.reply_text(f"🔍 Searching `{q}`...", parse_mode="Markdown")

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{API_BASE}/search?query={requests.utils.quote(q)}", timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200: return await msg.edit_text("❌ API down!")
                data = await r.json()

        songs = data.get("data",{}).get("songs",{}).get("results",[])
        if not songs:
            return await msg.edit_text(f"❌ `{q}` ka kuch nahi mila!", parse_mode="Markdown")

        ctx.user_data["results"] = songs
        await msg.edit_text(search_text(songs, 0), parse_mode="Markdown", reply_markup=search_kb(songs, 0))
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{str(e)[:60]}`")

async def btn(upd, ctx):
    q = upd.callback_query
    await q.answer()
    d = q.data

    if d == "c":
        ctx.user_data.pop("results", None)
        return await q.message.delete()
    if d == "x": return
    if d == "n":
        await q.message.reply_text("🔍 `/search <name>` use karo", parse_mode="Markdown")
        return await q.message.delete()

    if d.startswith("p_"):
        pg = int(d.split("_")[1])
        songs = ctx.user_data.get("results", [])
        if not songs: return await q.message.edit_text("❌ Expired! /search karo.")
        await q.message.edit_text(search_text(songs, pg), parse_mode="Markdown", reply_markup=search_kb(songs, pg))
        return

    if d.startswith("s_"):
        parts = d.split("_", 2)
        sid, idx = parts[1], int(parts[2])
        songs = ctx.user_data.get("results", [])
        if idx >= len(songs): return await q.message.edit_text("❌ Error! /search karo.")
        sinfo = songs[idx]
        await send_song(upd, ctx, q.message, sid, sinfo)

async def send_song(upd, ctx, msg, sid, sinfo):
    title = clean(sinfo.get("title","?"))
    artists = clean(sinfo.get("primaryArtists", sinfo.get("singers","?")))

    await ctx.bot.send_chat_action(upd.effective_chat.id, "typing")

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{API_BASE}/songs/{sid}", timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200: return await msg.edit_text("❌ Song fetch fail!")
                data = await r.json()
        sd = data["data"][0]
    except:
        return await msg.edit_text("❌ Song data error!")

    card = song_card(sd)
    await msg.edit_text(card, parse_mode="Markdown")

    ba = best_audio(sd.get("downloadUrl", []))
    if not ba: return await msg.edit_text(card + "\n\n❌ No link!", parse_mode="Markdown")
    dl_url = clean_url(ba["url"])
    quality = ba["quality"]

    song_title = clean(sd.get("name","?"))
    artists_list = sd.get("artists",{}).get("primary",[])
    artists_names = ", ".join(a.get("name","") for a in artists_list) or artists
    album_name = clean(sd.get("album",{}).get("name","?"))
    duration = sd.get("duration",0)
    year = str(sd.get("year",""))
    play_count = sd.get("playCount",0)
    cover_url = best_img(sd.get("image",[]))

    await msg.edit_text(card + "\n\n📥 Downloading audio...", parse_mode="Markdown")
    audio_bytes = await dl_file(dl_url)
    if not audio_bytes:
        return await msg.edit_text(card + "\n\n❌ Download fail!", parse_mode="Markdown")

    cover_bytes = None
    if cover_url:
        await msg.edit_text(card + "\n\n🖼 Downloading cover...", parse_mode="Markdown")
        cover_bytes = await dl_file(cover_url)

    lyrics_url = await fetch_lyrics(song_title, artists_names.split(",")[0].strip())

    if cover_bytes:
        try:
            await msg.edit_text(card + "\n\n🎨 Embedding cover...", parse_mode="Markdown")
            def embed():
                audio = MP4(io.BytesIO(audio_bytes))
                audio["\xa9nam"] = song_title
                audio["\xa9ART"] = artists_names
                audio["\xa9alb"] = album_name
                audio["\xa9day"] = year
                fmt = MP4Cover.FORMAT_JPEG if (cover_url.endswith('.jpg') or cover_url.endswith('.jpeg')) else MP4Cover.FORMAT_PNG
                audio["covr"] = [MP4Cover(cover_bytes, fmt)]
                buf = io.BytesIO()
                audio.save(buf)
                return buf.getvalue()
            loop = asyncio.get_event_loop()
            audio_bytes = await loop.run_in_executor(executor, embed)
        except:
            logger.warning("Cover embed fail, sending raw")

    safe_title = re.sub(r'[^\w\s\-\'\(\)]', '', song_title).strip()[:50] or "song"
    safe_artist = re.sub(r'[^\w\s\-\'\(\)]', '', artists_names).strip()[:30] or "artist"
    fname = f"{safe_title} - {safe_artist}.mp4"

    caption = (
        f"🎵 **{song_title}**\n"
        f"👤 `{artists_names}`\n"
        f"💿 `{album_name}`\n"
        f"⏱ {fmt_dur(duration)}  🎚 {quality}  📅 {year}\n"
        f"👁 {fmt_count(play_count)} plays\n"
        f"{'📜 [Lyrics]('+lyrics_url+')' if lyrics_url else ''}\n"
        f"⚡ JioSaavn Bot"
    ).strip()

    await msg.edit_text(card + "\n\n📤 Uploading...", parse_mode="Markdown")
    try:
        await ctx.bot.send_audio(
            chat_id=upd.effective_chat.id,
            audio=io.BytesIO(audio_bytes),
            filename=fname,
            title=song_title,
            performer=artists_names,
            duration=duration,
            caption=caption,
            parse_mode="Markdown",
            thumbnail=io.BytesIO(cover_bytes) if cover_bytes else None,
        )
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"❌ Upload fail: `{str(e)[:80]}`\n/search karo dobara.", parse_mode="Markdown")

# ─── FLASK ROUTES ─────────────────────────────
@app.route('/')
def index():
    return jsonify({"status": "ok", "bot": "JioSaavn Music Bot"}), 200

@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    global bot_app, bot_loop
    if not bot_app or not bot_loop:
        return "Init pending", 503
    try:
        upd = Update.de_json(request.get_json(force=True), bot_app.bot)
        # ⚡ Use stored loop instead of bot_app.loop
        asyncio.run_coroutine_threadsafe(bot_app.process_update(upd), bot_loop)
        return "OK", 200
    except Exception as e:
        logger.error(f"WH err: {e}")
        return "ERR", 500

# ─── MAIN ──────────────────────────────────────
async def init():
    global bot_app, bot_loop

    # ✅ Store the running event loop
    bot_loop = asyncio.get_running_loop()

    bot_app = Application.builder().token(BOT_TOKEN).updater(None).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("search", search))
    bot_app.add_handler(CallbackQueryHandler(btn))

    await bot_app.initialize()
    await bot_app.start()
    await bot_app.bot.set_webhook(f"{WEBHOOK_URL}/{BOT_TOKEN}", drop_pending_updates=True)
    logger.info(f"✅ Webhook set: {WEBHOOK_URL}/{BOT_TOKEN[:10]}...")

    # Heartbeat — keep alive
    while True:
        await asyncio.sleep(3600)
        logger.info("❤️ Heartbeat OK")

if __name__ == "__main__":
    from threading import Thread

    # Flask in separate thread
    t = Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False), daemon=True)
    t.start()

    # Bot in main thread's event loop
    asyncio.run(init())
