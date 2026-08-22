import os, re, io, json, time, logging, asyncio, html as html_mod, subprocess, tempfile
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
MAX_SIZE = 50 * 1024 * 1024  # 50MB
LRCLIB_API = "https://lrclib.net/api"

# ─── SETUP ─────────────────────────────────────
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
app = Flask(__name__)

bot_app = None
bot_loop = None
executor = ThreadPoolExecutor(max_workers=2)

# Check if ffmpeg is available
FFMPEG_AVAILABLE = False
try:
    subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
    FFMPEG_AVAILABLE = True
    logger.info("✅ FFmpeg available — lyric videos enabled!")
except:
    logger.warning("⚠️ FFmpeg not found — audio-only mode")

# Font for Hindi lyrics
FONT_PATH = None
for path in [
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]:
    if os.path.exists(path):
        FONT_PATH = path
        break
if not FONT_PATH:
    FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"  # fallback
    if not os.path.exists(FONT_PATH):
        FONT_PATH = None

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

async def fetch_synced_lyrics(track_name, artist_name, duration):
    """Fetch synced lyrics from LRCLIB"""
    try:
        # Search by track + artist
        async with aiohttp.ClientSession() as s:
            params = {"q": f"{track_name} {artist_name}"}
            async with s.get(f"{LRCLIB_API}/search", params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    data = await r.json()
                    if data and len(data) > 0:
                        # Try to find best match
                        best = data[0]
                        for item in data:
                            if item.get("syncedLyrics"):
                                best = item
                                break
                        # Check if synced lyrics exist
                        if best.get("syncedLyrics"):
                            synced = best["syncedLyrics"]
                            plain = best.get("plainLyrics", "")
                            return parse_lrc(synced), plain, True

        # Try direct get by details
        safe_track = requests.utils.quote(track_name)
        safe_artist = requests.utils.quote(artist_name)
        async with s.get(
            f"{LRCLIB_API}/get?track_name={safe_track}&artist_name={safe_artist}",
            timeout=aiohttp.ClientTimeout(total=15)
        ) as r:
            if r.status == 200:
                data = await r.json()
                if data.get("syncedLyrics"):
                    return parse_lrc(data["syncedLyrics"]), data.get("plainLyrics", ""), True
                elif data.get("plainLyrics"):
                    return None, data["plainLyrics"], False
    except:
        pass
    return None, None, False

def parse_lrc(lrc_text):
    """Convert LRC text to list of {time, text} dicts"""
    pattern = re.compile(r'\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)')
    lyrics = []
    for line in lrc_text.strip().split('\n'):
        m = pattern.match(line.strip())
        if m:
            mins, secs, millis, text = m.groups()
            if len(millis) == 2:
                millis = millis + "0"
            total_secs = int(mins) * 60 + int(secs) + int(millis) / 1000
            text = text.strip()
            if text:
                lyrics.append({"time": total_secs, "text": text})
    return lyrics

def lrc_to_srt(lyrics, duration):
    """Convert LRC lyrics to SRT format string"""
    if not lyrics:
        return ""
    lines = []
    for i, l in enumerate(lyrics):
        start = l["time"]
        end = lyrics[i+1]["time"] if i+1 < len(lyrics) else min(start + 4, duration)
        if start >= duration:
            break
        srt_start = f"{int(start//3600):02d}:{int((start%3600)//60):02d}:{int(start%60):02d},{int((start%1)*1000):03d}"
        srt_end = f"{int(end//3600):02d}:{int((end%3600)//60):02d}:{int(end%60):02d},{int((end%1)*1000):03d}"
        lines.append(f"{i+1}\n{srt_start} --> {srt_end}\n{l['text']}\n")
    return "\n".join(lines)

async def create_lyric_video(audio_bytes, cover_bytes, srt_content, song_title, artists, duration):
    """Create MP4 video with blurred album art + synced lyrics"""
    if not FFMPEG_AVAILABLE or not FONT_PATH:
        return None

    tmp_files = []
    try:
        # Save temp files
        af = tempfile.NamedTemporaryFile(suffix='.m4a', delete=False); af.write(audio_bytes); af.close()
        cf = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False); cf.write(cover_bytes); cf.close()
        sf = tempfile.NamedTemporaryFile(suffix='.srt', delete=False, mode='w'); sf.write(srt_content); sf.close()
        out = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False); out.close()
        tmp_files = [af.name, cf.name, sf.name, out.name]

        # Escape SRT path for FFmpeg filter (colons and backslashes)
        srt_esc = sf.name.replace('\\', '\\\\').replace(':', '\\:').replace("'", "'\\''")

        # FFmpeg command
        cmd = [
            'ffmpeg', '-y',
            '-i', af.name,
            '-loop', '1',
            '-i', cf.name,
            '-filter_complex',
            f'[1:v]scale=1920:1080,boxblur=50:10[bg];'
            f'[1:v]scale=700:700[fg];'
            f'[bg][fg]overlay=(W-w)/2:(H-h)/2[base];'
            f'[base]subtitles={srt_esc}:fontsdir={os.path.dirname(FONT_PATH)}[out]',
            '-map', '[out]',
            '-map', '0:a',
            '-c:v', 'libx264',
            '-preset', 'ultrafast',
            '-crf', '30',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-pix_fmt', 'yuv420p',
            '-r', '1',
            '-shortest',
            '-movflags', '+faststart',
            out.name
        ]

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(executor, lambda: subprocess.run(cmd, capture_output=True, timeout=300))

        with open(out.name, 'rb') as f:
            video_bytes = f.read()

        return video_bytes if len(video_bytes) <= MAX_SIZE else None

    except Exception as e:
        logger.error(f"Video creation error: {e}")
        return None
    finally:
        for f in tmp_files:
            try: os.unlink(f)
            except: pass

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

def song_card(sd, status="processing"):
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
    status_icons = {
        "processing": "⏳",
        "lyrics": "📜",
        "video": "🎬",
        "uploading": "📤",
        "done": "✅"
    }
    icon = status_icons.get(status, "⏳")
    return (
        f"🎧 **── NOW PLAYING ──**\n━━━━━━━━━━━━━━\n\n"
        f"🎵 **{t}**\n👤 _{ar}_\n💿 {ab}\n🏷 {lb}\n\n"
        f"⏱ {du}  📅 {yr}  🌐 {lang}\n👁 {pc} plays  {ex}\n"
        f"━━━━━━━━━━━━━━\n"
        f"{icon} {status.capitalize()}..."
    )

# ─── HANDLERS ─────────────────────────────────
async def start(upd, ctx):
    u = upd.effective_user
    v = "🎬" if FFMPEG_AVAILABLE else "🎵"
    await upd.message.reply_text(
        f"🌟 **Namaste {u.first_name}!** 🌟\n\n"
        f"🎵 JioSaavn Music Bot {v}\n\n"
        f"🔍 `/search <name>` — Search karo\n"
        f"💡 `/search Awarapan`\n\n"
        f"{'🎬 Lyric Video + 🎵 Audio' if FFMPEG_AVAILABLE else '🎵 Audio Only'}\n"
        f"📜 Synced lyrics included!\n"
        f"⚡ Free • Direct download",
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

# ─── AUDIO MODE KEYBOARD ─────────────────────
def audio_mode_kb(song_id):
    kb = [
        [
            InlineKeyboardButton("🎬 Lyric Video", callback_data=f"lyric_{song_id}"),
            InlineKeyboardButton("🎵 Audio Only", callback_data=f"audio_{song_id}")
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="c")]
    ]
    return InlineKeyboardMarkup(kb)

async def send_song(upd, ctx, msg, sid, sinfo):
    """Download song, fetch lyrics, generate video, send"""
    await ctx.bot.send_chat_action(upd.effective_chat.id, "typing")

    # Fetch full song data
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{API_BASE}/songs/{sid}", timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200: return await msg.edit_text("❌ Song fetch fail!")
                data = await r.json()
        sd = data["data"][0]
    except:
        return await msg.edit_text("❌ Song data error!")

    song_title = clean(sd.get("name", "?"))
    artists_list = sd.get("artists", {}).get("primary", [])
    artists_names = ", ".join(a.get("name", "") for a in artists_list) or "?"
    album_name = clean(sd.get("album", {}).get("name", "?"))
    duration = sd.get("duration", 0)
    year = str(sd.get("year", ""))
    play_count = sd.get("playCount", 0)
    cover_url = best_img(sd.get("image", []))
    
    # Get best audio
    ba = best_audio(sd.get("downloadUrl", []))
    if not ba: return await msg.edit_text("❌ No download link!")
    dl_url = clean_url(ba["url"])
    quality = ba["quality"]

    # Show card
    card = song_card(sd)
    await msg.edit_text(card, parse_mode="Markdown")

    # Download audio + cover
    await msg.edit_text(song_card(sd, "processing") + "\n\n📥 Downloading audio...", parse_mode="Markdown")
    audio_bytes = await dl_file(dl_url)
    if not audio_bytes:
        return await msg.edit_text("❌ Download failed!")

    await msg.edit_text(song_card(sd, "lyrics") + "\n\n📜 Fetching synced lyrics...", parse_mode="Markdown")
    
    # Get synced lyrics
    first_artist = artists_names.split(",")[0].strip()
    synced_lyrics, plain_lyrics, has_sync = await fetch_synced_lyrics(song_title, first_artist, duration)
    
    # Fallback to Genius if LRCLIB has nothing
    if not plain_lyrics:
        genius_url = await fetch_genius_lyrics(song_title, first_artist)
    else:
        genius_url = None

    # Download cover
    cover_bytes = None
    if cover_url:
        cover_bytes = await dl_file(cover_url)

    # Try to create lyric video if synced lyrics available + ffmpeg available
    video_bytes = None
    if has_sync and synced_lyrics and cover_bytes and FFMPEG_AVAILABLE:
        await msg.edit_text(song_card(sd, "video") + "\n\n🎬 Creating lyric video...\n⏳ This may take 30-60s...", parse_mode="Markdown")
        srt_content = lrc_to_srt(synced_lyrics, duration)
        if srt_content:
            video_bytes = await create_lyric_video(audio_bytes, cover_bytes, srt_content, song_title, artists_names, duration)

    # Send what we have
    await msg.edit_text(song_card(sd, "uploading") + "\n\n📤 Uploading...", parse_mode="Markdown")

    safe_title = re.sub(r'[^\w\s\-\'\(\)]', '', song_title).strip()[:50] or "song"
    safe_artist = re.sub(r'[^\w\s\-\'\(\)]', '', artists_names).strip()[:30] or "artist"
    
    # Build caption
    caption_parts = [
        f"🎵 **{song_title}**",
        f"👤 `{artists_names}`",
        f"💿 `{album_name}`",
        f"⏱ {fmt_dur(duration)}  🎚 {quality}  📅 {year}",
        f"👁 {fmt_count(play_count)} plays",
    ]
    if genius_url:
        caption_parts.append(f"📜 [Lyrics]({genius_url})")
    elif plain_lyrics:
        caption_parts.append("📜 Lyrics synced ✓")
    caption_parts.append("⚡ JioSaavn Bot")
    caption = "\n".join(caption_parts)

    try:
        if video_bytes:
            # Send as video — Telegram will show lyrics in frames!
            await ctx.bot.send_video(
                chat_id=upd.effective_chat.id,
                video=io.BytesIO(video_bytes),
                caption=caption,
                parse_mode="Markdown",
                width=1080,
                height=1080,
                supports_streaming=True,
                thumbnail=io.BytesIO(cover_bytes) if cover_bytes else None,
            )
            await msg.delete()
        else:
            # Fallback to audio-only
            fname = f"{safe_title} - {safe_artist}.mp4"
            
            # Embed cover in audio
            if cover_bytes:
                try:
                    def embed():
                        audio = MP4(io.BytesIO(audio_bytes))
                        audio["\xa9nam"] = song_title
                        audio["\xa9ART"] = artists_names
                        audio["\xa9alb"] = album_name
                        audio["\xa9day"] = year
                        fmt = MP4Cover.FORMAT_JPEG if (cover_url and (cover_url.endswith('.jpg') or cover_url.endswith('.jpeg'))) else MP4Cover.FORMAT_PNG
                        audio["covr"] = [MP4Cover(cover_bytes, fmt)]
                        buf = io.BytesIO()
                        audio.save(buf)
                        return buf.getvalue()
                    loop = asyncio.get_event_loop()
                    audio_bytes = await loop.run_in_executor(executor, embed)
                except:
                    pass

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
            # If synced lyrics available but couldn't make video, send LRC too
            if has_sync and synced_lyrics:
                lrc_text = "\n".join([f"[{int(l['time']//60):02d}:{int(l['time']%60):02d}.{int((l['time']%1)*100):02d}]{l['text']}" for l in synced_lyrics])
                lrc_filename = f"{safe_title} - {safe_artist}.lrc"
                await ctx.bot.send_document(
                    chat_id=upd.effective_chat.id,
                    document=io.BytesIO(lrc_text.encode('utf-8')),
                    filename=lrc_filename,
                    caption="📜 Synced lyrics file — use with VLC, foobar2000, etc."
                )
            
            await msg.delete()

    except Exception as e:
        logger.error(f"Upload error: {e}")
        await msg.edit_text(f"❌ Upload fail: `{str(e)[:80]}`\n/search karo dobara.", parse_mode="Markdown")

async def fetch_genius_lyrics(song_title, artist):
    """Get Genius URL for lyrics link"""
    try:
        url = "https://api.genius.com/search"
        headers = {"Authorization": f"Bearer {GENIUS_TOKEN}"}
        params = {"q": f"{song_title} {artist}"}
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    data = await r.json()
                    hits = data.get("response", {}).get("hits", [])
                    if hits:
                        return hits[0]["result"]["url"]
    except:
        pass
    return None

# ─── FLASK ROUTES ─────────────────────────────
@app.route('/')
def index():
    return jsonify({"status": "ok", "bot": "JioSaavn Music Bot", "lyric_video": FFMPEG_AVAILABLE}), 200

@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    global bot_app, bot_loop
    if not bot_app or not bot_loop:
        return "Init pending", 503
    try:
        upd = Update.de_json(request.get_json(force=True), bot_app.bot)
        asyncio.run_coroutine_threadsafe(bot_app.process_update(upd), bot_loop)
        return "OK", 200
    except Exception as e:
        logger.error(f"WH err: {e}")
        return "ERR", 500

# ─── MAIN ──────────────────────────────────────
async def init():
    global bot_app, bot_loop
    bot_loop = asyncio.get_running_loop()

    bot_app = Application.builder().token(BOT_TOKEN).updater(None).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("search", search))
    bot_app.add_handler(CallbackQueryHandler(btn))

    await bot_app.initialize()
    await bot_app.start()
    await bot_app.bot.set_webhook(f"{WEBHOOK_URL}/{BOT_TOKEN}", drop_pending_updates=True)
    logger.info(f"✅ Webhook set: {WEBHOOK_URL}/{BOT_TOKEN[:10]}...")
    logger.info(f"{'🎬 Lyric video enabled' if FFMPEG_AVAILABLE else '🎵 Audio only'}")

    while True:
        await asyncio.sleep(3600)
        logger.info("❤️ Alive")

if __name__ == "__main__":
    from threading import Thread
    t = Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False), daemon=True)
    t.start()
    asyncio.run(init())
