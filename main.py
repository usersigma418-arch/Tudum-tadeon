import logging
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.error import TelegramError
import os
import json
from pathlib import Path
import urllib.parse

# Bot token (BotFather se lena)
TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# API endpoints
SEARCH_API = "http://songscrap.vercel.app/api/search"
SONG_API = "http://Songscrap.vercel.app/api/songs"

# Create temp folder for audio files
AUDIO_DIR = Path("./audio_files")
AUDIO_DIR.mkdir(exist_ok=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    await update.message.reply_text(
        "🎵 *Namaste!*\n\n"
        "Koi bhi song ka naam likho aur main tujhe high-quality audio bhej dunga\n\n"
        "📝 Examples: Awarapan, Tum Hi Ho, Tera Mera Rishta\n\n"
        "🎧 Milega: Album Art + High Quality Audio (320kbps)",
        parse_mode="Markdown"
    )

async def search_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search for songs"""
    query = update.message.text
    chat_id = update.message.chat_id
    
    print(f"📝 Search Query: '{query}' from chat_id: {chat_id}")
    
    # Loading message
    loading_msg = await update.message.reply_text("🔍 *Searching for songs...* 🎵\nPlease wait...", parse_mode="Markdown")
    
    try:
        # Search API call
        print(f"🔗 Calling API: {SEARCH_API}?query={query}")
        response = requests.get(SEARCH_API, params={"query": query}, timeout=10)
        print(f"📊 API Status: {response.status_code}")
        
        data = response.json()
        print(f"✅ API Response received, success: {data.get('success')}")
        
        if not data.get("success"):
            print("❌ API returned success=false")
            await loading_msg.edit_text("❌ Search failed, please try again")
            return
        
        # Get songs from response
        songs = data.get("data", {}).get("songs", {}).get("results", [])
        albums = data.get("data", {}).get("albums", {}).get("results", [])
        
        print(f"📀 Found {len(songs)} songs, {len(albums)} albums")
        
        all_results = []
        
        # Add songs with singer names
        for idx, song in enumerate(songs[:8]):  # Max 8 songs
            all_results.append({
                "type": "song",
                "id": song.get("id"),
                "title": song.get("title"),
                "artist": song.get("primaryArtists", "Unknown"),
                "image": song.get("image", [{}])[-1].get("url"),
                "album": song.get("album")
            })
            print(f"  🎵 Song {idx+1}: {song.get('title')} | Singer: {song.get('primaryArtists', 'Unknown')} (ID: {song.get('id')})")
        
        # Add albums with their songIds
        for idx, album in enumerate(albums[:4]):  # Max 4 albums
            song_ids = album.get("songIds", "").split(", ")
            all_results.append({
                "type": "album",
                "id": song_ids[0] if song_ids else None,
                "title": album.get("title"),
                "artist": album.get("artist"),
                "image": album.get("image", [{}])[-1].get("url"),
                "all_song_ids": song_ids
            })
            print(f"  💿 Album {idx+1}: {album.get('title')} | Artists: {album.get('artist')} (IDs: {song_ids})")
        
        if not all_results:
            print("❌ No results found")
            await loading_msg.edit_text("❌ Koi song nahi mila bhai 😕\n\nDusra naam try kar!")
            return
        
        print(f"✅ Total results: {len(all_results)}")
        
        # Create fancy inline buttons with emojis and singer names
        keyboard = []
        for idx, result in enumerate(all_results[:12]):
            # Truncate title and artist for button
            title = result['title'][:35]
            artist = result['artist'][:25]
            
            # Fancy button text with singer name
            btn_text = f"🎵 {title}\n👤 {artist}"
            callback_data = f"select_{idx}"
            
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=callback_data)])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Store results in context for callback
        context.user_data['search_results'] = all_results
        
        # Edit loading message with fancy results
        result_count = len(all_results)
        await loading_msg.edit_text(
            f"✅ *{result_count} Results Found!*\n\n"
            f"🎧 Which song do you want?\n"
            f"(Click on any button below)",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Search Error: {e}")
        try:
            await loading_msg.edit_text(f"❌ Error: {str(e)[:100]}")
        except:
            await update.message.reply_text(f"❌ Error: {str(e)[:100]}")

def sanitize_filename(filename):
    """Clean filename for file system"""
    # Remove special characters
    filename = "".join(c for c in filename if c.isalnum() or c in (' ', '-', '_', '.')).rstrip()
    # Replace spaces with underscores
    filename = filename.replace(' ', '_')
    return filename[:100]  # Limit length

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button selection"""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    
    print(f"🎬 Button clicked: {query.data} from chat_id: {chat_id}")
    
    try:
        # Parse callback data
        idx = int(query.data.split("_")[1])
        
        results = context.user_data.get('search_results', [])
        print(f"📋 Available results: {len(results)}, Selected index: {idx}")
        
        if idx >= len(results):
            print(f"❌ Index out of range: {idx} >= {len(results)}")
            await query.edit_message_text("❌ Selection expired, search again")
            return
        
        selected = results[idx]
        song_id = selected.get("id")
        
        print(f"✅ Selected: {selected.get('title')} by {selected.get('artist')} (ID: {song_id})")
        
        if not song_id:
            print("❌ No song ID in selected result")
            await query.edit_message_text("❌ Song ID not found")
            return
        
        # Update message to show loading
        await query.edit_message_text(
            f"⏳ *Loading...* \n\n"
            f"🎵 {selected['title'][:50]}\n"
            f"👤 {selected['artist'][:40]}\n\n"
            f"Please wait..."
        )
        
        # Fetch song details
        try:
            song_url = f"{SONG_API}/{song_id}"
            print(f"🔗 Calling Song API: {song_url}")
            song_response = requests.get(song_url, timeout=15)
            print(f"📊 Song API Status: {song_response.status_code}")
            song_data = song_response.json()
            print(f"✅ Song API Response: success={song_data.get('success')}")
        except requests.Timeout:
            print("❌ Request timeout")
            await query.edit_message_text("❌ Request timeout, try again")
            return
        except Exception as e:
            print(f"❌ Song API Error: {e}")
            logger.error(f"Song API Error: {e}")
            await query.edit_message_text(f"❌ API Error: {str(e)[:80]}")
            return
        
        if not song_data.get("success"):
            print("❌ Song API returned success=false")
            await query.edit_message_text("❌ Song details not found")
            return
        
        # Parse song data (it's a list)
        song_list = song_data.get("data", [])
        if not song_list or len(song_list) == 0:
            print("❌ Song data list is empty")
            await query.edit_message_text("❌ Song data not found")
            return
        
        song_info = song_list[0]
        print(f"✅ Got song info: {song_info.get('name')}")
        
        # Get image from song object (highest quality)
        image_list = song_info.get("image", [])
        image_url = None
        if image_list and len(image_list) > 0:
            image_url = image_list[-1].get("url")
            print(f"📸 Image URL: {image_url[:60]}...")
        
        # Get best quality audio URL (320kbps is best)
        download_urls = song_info.get("downloadUrl", [])
        print(f"🎧 Available qualities: {len(download_urls)}")
        
        audio_url = None
        
        # Try to get 320kbps first (best quality)
        for url_obj in download_urls:
            if url_obj.get("quality") == "320kbps":
                raw_url = url_obj.get("url", "")
                audio_url = raw_url.rstrip('\u0004').rstrip('\x04')
                print(f"✅ Found 320kbps: {audio_url[:80]}...")
                break
        
        # If no 320kbps, get highest available
        if not audio_url and download_urls:
            for url_obj in reversed(download_urls):
                raw_url = url_obj.get("url", "")
                if raw_url:
                    audio_url = raw_url.rstrip('\u0004').rstrip('\x04')
                    print(f"✅ Using {url_obj.get('quality')}: {audio_url[:80]}...")
                    break
        
        if not audio_url:
            print("❌ No audio URL found in downloadUrl")
            await query.edit_message_text("❌ Audio URL not found")
            return
        
        print(f"🎵 Final audio URL ready: {audio_url[:80]}...")
        
        # Step 1: Send album art (photo) first
        if image_url:
            try:
                print(f"📸 Sending album art...")
                caption = f"🎵 *{selected['title']}*\n👤 {selected['artist']}\n⏱️ Duration: {song_info.get('duration', 'N/A')} sec"
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=image_url,
                    caption=caption,
                    parse_mode="Markdown"
                )
                print("✅ Album art sent successfully")
            except Exception as e:
                print(f"⚠️ Photo send error: {e}")
                logger.warning(f"Photo send error: {e}")
        
        # Step 2: Download and send audio with thumbnail
        await query.edit_message_text(f"🎵 *Sending audio...* \n\n{selected['title'][:50]}", parse_mode="Markdown")
        
        try:
            print(f"🎧 Audio URL: {audio_url[:80]}...")
            
            # Create proper filename
            song_name = song_info.get("name", selected['title'])
            artist_name = selected['artist'].split(',')[0]  # Get first artist
            filename = sanitize_filename(f"{song_name} - {artist_name}.mp4")
            filepath = AUDIO_DIR / filename
            
            print(f"💾 Downloading audio: {filename}")
            
            # Download audio file with longer timeout
            audio_response = requests.get(audio_url, timeout=60, stream=True)
            with open(filepath, 'wb') as f:
                for chunk in audio_response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            file_size_mb = filepath.stat().st_size / (1024 * 1024)
            print(f"✅ Downloaded: {filename} ({file_size_mb:.1f} MB)")
            
            # Retry logic for sending audio (with backoff)
            import asyncio
            
            max_retries = 3
            retry_count = 0
            audio_sent = False
            last_error = None
            
            while retry_count < max_retries and not audio_sent:
                try:
                    print(f"📤 Sending audio (attempt {retry_count + 1}/{max_retries})...")
                    
                    # Send audio with thumbnail image
                    with open(filepath, 'rb') as audio_file:
                        await context.bot.send_audio(
                            chat_id=chat_id,
                            audio=audio_file,
                            title=song_name[:200],
                            performer=artist_name[:200],
                            duration=song_info.get("duration"),
                            thumbnail=image_url if image_url else None
                        )
                    
                    print("✅ Audio sent successfully with thumbnail")
                    audio_sent = True
                    
                except (TimeoutError, ConnectionError, Exception) as send_error:
                    last_error = send_error
                    retry_count += 1
                    error_msg = str(send_error)[:80]
                    print(f"⚠️ Send attempt {retry_count} failed: {error_msg}")
                    
                    if retry_count < max_retries:
                        wait_time = 3 * retry_count  # Progressive backoff: 3s, 6s, 9s
                        print(f"🔄 Retrying in {wait_time} seconds...")
                        await asyncio.sleep(wait_time)
                    else:
                        print("❌ All retry attempts failed")
                        raise last_error
            
            # Clean up file after sending
            filepath.unlink()
            print(f"🗑️ Cleaned up: {filename}")
            
        except Exception as e:
            print(f"❌ Audio send error: {e}")
            logger.error(f"Audio send error: {e}")
            await query.edit_message_text(f"❌ Audio send failed: {str(e)[:80]}")
            return
        
        # Final success message
        await query.edit_message_text(
            f"✅ *Download Complete!*\n\n"
            f"🎵 {selected['title']}\n"
            f"👤 {selected['artist']}\n"
            f"⏱️ {song_info.get('duration', 'N/A')} sec\n"
            f"📊 320 kbps (High Quality)\n\n"
            f"Enjoy! 🎧",
            parse_mode="Markdown"
        )
        
    except ValueError as e:
        logger.error(f"Parse Error: {e}")
        await query.edit_message_text("❌ Error parsing selection")
    except Exception as e:
        logger.error(f"Button click error: {e}")
        try:
            await query.edit_message_text(f"❌ Error: {str(e)[:100]}")
        except:
            pass

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    await update.message.reply_text(
        "📖 *How to Use:*\n\n"
        "1️⃣ Type any song name\n"
        "2️⃣ Select from the list\n"
        "3️⃣ Get album art + audio\n\n"
        "🎧 *Quality:* 320 kbps (High)\n"
        "📸 *Includes:* Album Art as Thumbnail\n"
        "🏷️ *Filename:* Song Name - Artist\n\n"
        "/start - Start bot\n"
        "/help - Show this",
        parse_mode="Markdown"
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Error: {context.error}")
    if update and update.message:
        try:
            await update.message.reply_text("❌ Bot mein error ayi, dubara try karo")
        except:
            pass

def main():
    """Main function to run the bot"""
    
    # Check token
    if TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        print("❌ ERROR: Bot token set nahi hai!")
        print("telegram_song_bot.py line 16 mein apna token paste karo")
        print("Bot token @BotFather se lena (Telegram mein)")
        return
    
    # Create application
    application = Application.builder().token(TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(button_click))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_song))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Start bot
    print("✅ Bot started successfully!")
    print("🔗 BotFather se apna bot search karo or /start de")
    print("❌ Stop karne ke liye: Ctrl+C press karo\n")
    
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except KeyboardInterrupt:
        print("\n✅ Bot stopped")
    except Exception as e:
        print(f"❌ Fatal error: {e}")

if __name__ == "__main__":
    main()
