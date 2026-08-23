import os, re, io, json, time, logging, asyncio, html as html_mod, signal, subprocess, tempfile
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

import requests, aiohttp
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from mutagen.mp4 import MP4, MP4Cover
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import textwrap

# ─── CONFIG ───────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]
PORT = int(os.environ.get("PORT", 8080))
API_BASE = "http://songscrap.vercel.app/api"
GENIUS_TOKEN = "w-XTArszGpAQaaLu-JlViwy1e-0rxx4dvwqQzOEtcmmpYndHm_nkFTvAB5BsY-ww"
PER_PAGE = 5
MAX_SIZE = 45 * 1024 * 1024
LYRICS_CACHE_FILE = "/tmp/lyrics_cache.json"

# Video generation settings
MAX_VIDEO_DURATION = 600  # Skip video for songs > 10 minutes (600 seconds)
VIDEO_TIMEOUT = 150      # 2.5 minutes timeout for video creation
VIDEO_RESOLUTION = (854, 480)  # Width x Height (optimized for speed)

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

# ─── LYRICS CACHE ─────────────────────────────
def load_lyrics_cache():
    try:
        if os.path.exists(LYRICS_CACHE_FILE):
            with open(LYRICS_CACHE_FILE, 'r') as f:
                return json.load(f)
    except: pass
    return {}

def save_lyrics_cache(cache):
    try:
        with open(LYRICS_CACHE_FILE, 'w') as f:
            json.dump(cache, f)
    except: pass

def get_cached_lyrics(song_title, artist):
    cache = load_lyrics_cache()
    key = f"{song_title}|{artist}".lower()
    return cache.get(key)

def cache_lyrics(song_title, artist, lyrics_text):
    cache = load_lyrics_cache()
    key = f"{song_title}|{artist}".lower()
    cache[key] = lyrics_text
    save_lyrics_cache(cache)

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

async def fetch_lyrics_text(lyrics_url):
    """Scrape lyrics from Genius URL"""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(lyrics_url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    from bs4 import BeautifulSoup
                    html = await r.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    lyrics_divs = soup.find_all('div', {'data-lyrics-container': 'true'})
                    if lyrics_divs:
                        lyrics = "\n".join([div.get_text() for div in lyrics_divs])
                        return lyrics[:2000]  # Limit to 2000 chars
    except Exception as e:
        logger.warning(f"Lyrics fetch fail: {e}")
    return None

async def fetch_lyrics(song_title, artist):
    """Fetch lyrics URL from Genius API"""
    try:
        url = "https://api.genius.com/search"
        headers = {"Authorization": f"Bearer {GENIUS_TOKEN}"}
        params = {"q": f"{song_title} {artist}"}
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    data = await r.json()
                    hits = data.get("response", {}).get("hits", [])
                    if hits: 
                        return hits[0]["result"]["url"]
    except: 
        pass
    return None

# ─── VIDEO GENERATION ─────────────────────────
def create_lyric_video(cover_bytes, audio_file, lyrics_text, duration, output_file):
    """
    Create video with blurred cover background + lyrics overlay
    OPTIMIZED for fast generation
    """
    try:
        logger.info(f"🎬 Starting video creation (duration: {duration}s)")
        
        # Load and process cover image
        cover_img = Image.open(io.BytesIO(cover_bytes)).convert("RGB")
        
        # OPTIMIZATION: Use smaller resolution for faster processing
        # Portrait mode for mobile
        video_width, video_height = 854, 480  # Smaller = faster, still good quality
        cover_img = cover_img.resize((video_width, video_height), Image.Resampling.LANCZOS)
        
        # Apply blur (reduced from 30 to 20 for faster processing)
        logger.info("🎨 Applying blur effect...")
        blurred = cover_img.filter(ImageFilter.GaussianBlur(radius=20))
        
        # Create frame
        frame = Image.new("RGB", (video_width, video_height), "black")
        frame.paste(blurred, (0, 0))
        
        # Add lyrics overlay
        if lyrics_text and len(lyrics_text.strip()) > 0:
            logger.info("📝 Adding lyrics overlay...")
            draw = ImageDraw.Draw(frame)
            
            # Try to use a nice font, fallback to default
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
            except:
                font = ImageFont.load_default()
            
            # Wrap lyrics (shorter width for faster render)
            wrapped_lyrics = textwrap.fill(lyrics_text[:400], width=18)
            
            y_text = video_height // 4
            lines = wrapped_lyrics.split('\n')
            
            for i, line in enumerate(lines[:8]):  # Max 8 lines (reduced from 12)
                y_pos = y_text + (i * 45)
                if y_pos > video_height - 100:
                    break
                
                try:
                    bbox = draw.textbbox((0, 0), line, font=font)
                    text_width = bbox[2] - bbox[0]
                    
                    # Draw semi-transparent background
                    bg_x1 = (video_width - text_width) // 2 - 15
                    bg_y1 = y_pos - 8
                    bg_x2 = (video_width + text_width) // 2 + 15
                    bg_y2 = y_pos + 35
                    
                    # Draw background
                    overlay = Image.new('RGBA', frame.size, (0, 0, 0, 0))
                    overlay_draw = ImageDraw.Draw(overlay)
                    overlay_draw.rectangle([bg_x1, bg_y1, bg_x2, bg_y2], fill=(0, 0, 0, 140))
                    frame = Image.alpha_composite(frame.convert('RGBA'), overlay).convert('RGB')
                    
                    # Draw text
                    draw = ImageDraw.Draw(frame)
                    draw.text(
                        ((video_width - text_width) // 2, y_pos),
                        line,
                        font=font,
                        fill=(255, 255, 255)
                    )
                except Exception as e:
                    logger.warning(f"Lyrics line error: {e}")
                    continue
        
        # Save frame as image
        frame_path = f"{tempfile.gettempdir()}/frame_{int(time.time())}.png"
        logger.info(f"💾 Saving frame to {frame_path}")
        frame.save(frame_path, quality=85)  # Lower quality for smaller file
        
        # Create video using ffmpeg with OPTIMIZATIONS
        logger.info("⚙️ Running FFmpeg...")
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", frame_path,
            "-i", audio_file,
            "-c:v", "libx264",
            "-preset", "ultrafast",        # Much faster encoding
            "-crf", "30",                  # Lower quality (but still good) = faster
            "-c:a", "aac",
            "-b:a", "128k",                # Lower audio bitrate
            "-shortest",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            output_file
        ]
        
        logger.info(f"Running: {' '.join(cmd)}")
        
        # CRITICAL: Add timeout to prevent hanging
        try:
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                timeout=120,  # 2 minutes max
                text=True
            )
            
            if result.returncode != 0:
                logger.error(f"❌ FFmpeg error: {result.stderr[:500]}")
                return False
            
            logger.info(f"✅ Video created successfully")
        except subprocess.TimeoutExpired:
            logger.error("❌ FFmpeg timeout after 120 seconds")
            return False
        except Exception as e:
            logger.error(f"❌ FFmpeg execution error: {e}")
            return False
        
        # Cleanup
        try:
            if os.path.exists(frame_path):
                os.remove(frame_path)
                logger.info("🧹 Frame file cleaned up")
        except:
            pass
        
        return os.path.exists(output_file)
        
    except Exception as e:
        logger.error(f"❌ Video creation error: {e}")
        return False

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

# ─── HANDLERS ─────────────────────────────
async def start(upd, ctx):
    u = upd.effective_user
    await upd.message.reply_text(
        f"🌟 **Namaste {u.first_name}!** 🌟\n\n"
        f"🎵 JioSaavn Music Bot — koi bhi gana search karo!\n\n"
        f"🔍 `/search <name>` — Search\n"
        f"💡 `/search Awarapan`\n\n"
        f"✨ Blurred background + Lyrics overlay!\n"
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

    # Fetch lyrics
    lyrics_text = None
    artist_first = artists_names.split(",")[0].strip()
    
    # Check cache first
    cached_lyrics = get_cached_lyrics(song_title, artist_first)
    if cached_lyrics:
        lyrics_text = cached_lyrics
        logger.info(f"Using cached lyrics for {song_title}")
    else:
        # Fetch from Genius
        lyrics_url = await fetch_lyrics(song_title, artist_first)
        if lyrics_url:
            await msg.edit_text(card + "\n\n📜 Fetching lyrics...", parse_mode="Markdown")
            lyrics_text = await fetch_lyrics_text(lyrics_url)
            if lyrics_text:
                cache_lyrics(song_title, artist_first, lyrics_text)

    # Save audio temporarily for ffmpeg
    temp_audio_path = f"{tempfile.gettempdir()}/audio_{int(time.time())}.mp4"
    with open(temp_audio_path, 'wb') as f:
        f.write(audio_bytes)

    # Create video with lyrics
    temp_video_path = f"{tempfile.gettempdir()}/video_{int(time.time())}.mp4"
    
    # Skip video for songs > 10 minutes (prevents timeout)
    if duration > MAX_VIDEO_DURATION:
        logger.info(f"⏭️ Skipping video for long song ({duration}s > {MAX_VIDEO_DURATION}s)")
        use_video = False
    elif cover_bytes:
        await msg.edit_text(card + "\n\n🎬 Creating video with lyrics...\n⏳ This may take 30-60 seconds", parse_mode="Markdown")
        loop = asyncio.get_event_loop()
        
        try:
            # Add timeout to prevent hanging indefinitely
            video_created = await asyncio.wait_for(
                loop.run_in_executor(
                    executor,
                    create_lyric_video,
                    cover_bytes,
                    temp_audio_path,
                    lyrics_text or "🎵 Lyrics not available",
                    duration,
                    temp_video_path
                ),
                timeout=150  # 2.5 minutes max
            )
            
            if not video_created:
                logger.warning("Video creation failed, falling back to audio")
                await msg.edit_text(card + "\n\n⚠️ Video creation failed, sending audio instead...", parse_mode="Markdown")
                use_video = False
            else:
                use_video = True
                logger.info("✅ Video created successfully")
        except asyncio.TimeoutError:
            logger.error("❌ Video creation timeout")
            await msg.edit_text(card + "\n\n⏱️ Video creation taking too long, sending audio instead...", parse_mode="Markdown")
            use_video = False
        except Exception as e:
            logger.error(f"❌ Video creation exception: {e}")
            await msg.edit_text(card + "\n\n❌ Video error, sending audio instead...", parse_mode="Markdown")
            use_video = False
    else:
        use_video = False
        logger.info("No cover image, sending audio only")

    safe_title = re.sub(r'[^\w\s\-\'\(\)]', '', song_title).strip()[:50] or "song"
    safe_artist = re.sub(r'[^\w\s\-\'\(\)]', '', artists_names).strip()[:30] or "artist"
    fname = f"{safe_title} - {safe_artist}.mp4"

    caption = (
        f"🎵 **{song_title}**\n"
        f"👤 `{artists_names}`\n"
        f"💿 `{album_name}`\n"
        f"⏱ {fmt_dur(duration)}  🎚 {quality}  📅 {year}\n"
        f"👁 {fmt_count(play_count)} plays\n"
        f"{'📜 Lyrics included!' if lyrics_text else '📜 Lyrics not available'}\n"
        f"⚡ JioSaavn Bot"
    ).strip()

    await msg.edit_text(card + "\n\n📤 Uploading...", parse_mode="Markdown")
    try:
        if use_video:
            # Send as video
            with open(temp_video_path, 'rb') as f:
                await ctx.bot.send_video(
                    chat_id=upd.effective_chat.id,
                    video=f,
                    caption=caption,
                    parse_mode="Markdown",
                    duration=duration,
                    supports_streaming=True,
                )
        else:
            # Fallback to audio with thumbnail
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
    finally:
        # Cleanup temp files
        try:
            os.remove(temp_audio_path)
        except:
            pass
        try:
            os.remove(temp_video_path)
        except:
            pass

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

    while True:
        await asyncio.sleep(3600)
        logger.info("❤️ Heartbeat OK")

if __name__ == "__main__":
    from threading import Thread

    t = Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False), daemon=True)
    t.start()

    asyncio.run(init())
