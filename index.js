import http from "node:http";
import { Telegraf } from "telegraf";
import { tagM4a } from "./mp4tag.js";
import {
  artistsOf,
  decodeEntities,
  fetchBuffer,
  formatDuration,
  getSongs,
  pickAudio,
  pickImage,
  safeFileName,
  search,
} from "./saavn.js";

const BOT_TOKEN = process.env.BOT_TOKEN;
if (!BOT_TOKEN) {
  console.error("Missing BOT_TOKEN env var");
  process.exit(1);
}

const bot = new Telegraf(BOT_TOKEN, { handlerTimeout: 300_000 });

/** Short-lived cache so callback_data stays under Telegram's 64-byte limit. */
const albumCache = new Map();
const cacheKey = () => Math.random().toString(36).slice(2, 10);

function rememberAlbum(ids) {
  const key = cacheKey();
  albumCache.set(key, ids);
  setTimeout(() => albumCache.delete(key), 30 * 60 * 1000).unref?.();
  return key;
}

const TELEGRAM_UPLOAD_LIMIT = 49 * 1024 * 1024;

bot.start((ctx) =>
  ctx.reply(
    "🎧 *SongScrap*\n\nKisi bhi gaane ka naam bhejo — main search karke options dunga, aur chuni hui song ko cover art ke saath bhej dunga.\n\nExample: `Awarapan 2`",
    { parse_mode: "Markdown" },
  ),
);

bot.help((ctx) => ctx.reply("Bas song / album / artist ka naam type karo. Baaki main dekh lunga."));

bot.on("text", async (ctx) => {
  const query = ctx.message.text.trim();
  if (!query || query.startsWith("/")) return;

  const status = await ctx.reply(`🔎 Searching “${query}”…`);
  try {
    const { songs, albums } = await search(query);
    if (!songs.length && !albums.length) {
      return ctx.telegram.editMessageText(
        ctx.chat.id,
        status.message_id,
        undefined,
        "😕 Kuch nahi mila. Dusre naam se try karo.",
      );
    }

    const rows = [];
    for (const s of songs.slice(0, 8)) {
      const label = s.singers ? `🎵 ${s.title} — ${s.singers}` : `🎵 ${s.title}`;
      rows.push([{ text: label.slice(0, 60), callback_data: `s:${s.id}` }]);
    }
    for (const a of albums.slice(0, 5)) {
      if (!a.songIds.length) continue;
      if (a.songIds.length === 1) {
        rows.push([
          {
            text: `🎵 ${a.title}${a.artist ? ` — ${a.artist}` : ""}`.slice(0, 60),
            callback_data: `s:${a.songIds[0]}`,
          },
        ]);
      } else {
        rows.push([
          {
            text: `💿 ${a.title} (${a.songIds.length} tracks)`.slice(0, 60),
            callback_data: `a:${rememberAlbum(a.songIds)}`,
          },
        ]);
      }
    }

    await ctx.telegram.editMessageText(
      ctx.chat.id,
      status.message_id,
      undefined,
      `✨ Results for *${query}*\n_Neeche se select karo_`,
      { parse_mode: "Markdown", reply_markup: { inline_keyboard: rows.slice(0, 12) } },
    );
  } catch (err) {
    console.error(err);
    await ctx.telegram.editMessageText(
      ctx.chat.id,
      status.message_id,
      undefined,
      `⚠️ Search fail hua: ${err.message}`,
    );
  }
});

bot.action(/^a:(.+)$/, async (ctx) => {
  await ctx.answerCbQuery("Album khol raha hu…");
  const ids = albumCache.get(ctx.match[1]);
  if (!ids) return ctx.reply("⌛ Ye list expire ho gayi. Dobara search karo.");

  try {
    const songs = await getSongs(ids.slice(0, 15));
    const rows = songs.map((s) => {
      const artists = artistsOf(s);
      const label = `🎵 ${decodeEntities(s.name)}${artists ? ` — ${artists}` : ""}`;
      return [{ text: label.slice(0, 60), callback_data: `s:${s.id}` }];
    });
    await ctx.reply("💿 *Album tracks*", {
      parse_mode: "Markdown",
      reply_markup: { inline_keyboard: rows },
    });
  } catch (err) {
    console.error(err);
    await ctx.reply(`⚠️ Album load nahi hua: ${err.message}`);
  }
});

bot.action(/^s:(.+)$/, async (ctx) => {
  const id = ctx.match[1];
  await ctx.answerCbQuery("La raha hu…");
  const status = await ctx.reply("⬇️ Song fetch ho raha hai…");
  const edit = (text) =>
    ctx.telegram.editMessageText(ctx.chat.id, status.message_id, undefined, text).catch(() => {});

  try {
    const [song] = await getSongs(id);
    if (!song) return edit("😕 Ye song nahi mila.");

    const audio = pickAudio(song.downloadUrl ?? []);
    if (!audio) return edit("😕 Is song ka audio link nahi mila.");

    const title = decodeEntities(song.name);
    const artist = artistsOf(song);
    const album = decodeEntities(song.album?.name || "");

    await edit(`⬇️ *${title}* — ${audio.quality} download ho raha hai…`).catch(() => {});

    const coverUrl = pickImage(song.image, "500x500");
    const thumbUrl = pickImage(song.image, "150x150");
    const [audioFile, cover, thumb] = await Promise.all([
      fetchBuffer(audio.url),
      coverUrl ? fetchBuffer(coverUrl).catch(() => null) : null,
      thumbUrl ? fetchBuffer(thumbUrl).catch(() => null) : null,
    ]);

    // Embed cover art + metadata straight into the audio file.
    const tagged = tagM4a(
      audioFile.buffer,
      {
        title,
        artist,
        album,
        year: song.year,
        comment: `${audio.quality} • ${song.label || "SongScrap"}`,
      },
      cover ? { buffer: cover.buffer, mime: cover.mime || "image/jpeg" } : null,
    );

    if (tagged.length > TELEGRAM_UPLOAD_LIMIT) {
      return edit(
        `⚠️ File bahut badi hai (${(tagged.length / 1024 / 1024).toFixed(1)} MB). Telegram bot 50 MB tak hi bhej sakta hai.`,
      );
    }

    const fileName = `${safeFileName(title)}${artist ? ` - ${safeFileName(artist)}` : ""} (${audio.quality}).m4a`;

    await ctx.replyWithAudio(
      { source: tagged, filename: fileName },
      {
        title,
        performer: artist || undefined,
        duration: Number(song.duration) || undefined,
        thumbnail: thumb ? { source: thumb.buffer } : undefined,
        caption:
          `🎵 *${title}*\n` +
          (artist ? `👤 ${artist}\n` : "") +
          (album ? `💿 ${album}\n` : "") +
          `🎚 ${audio.quality} • ⏱ ${formatDuration(song.duration || 0)}` +
          (song.year ? ` • 📅 ${song.year}` : ""),
        parse_mode: "Markdown",
      },
    );

    // Cover art as a separate image too, so it's easy to save.
    if (cover) {
      await ctx.replyWithPhoto(
        { source: cover.buffer, filename: `${safeFileName(title)} - cover.jpg` },
        { caption: `🖼 Cover art — ${title}` },
      );
    }

    await ctx.telegram.deleteMessage(ctx.chat.id, status.message_id).catch(() => {});
  } catch (err) {
    console.error(err);
    await edit(`⚠️ Kuch galat ho gaya: ${err.message}`);
  }
});

bot.catch((err) => console.error("Bot error:", err));

// Render free tier expects an open HTTP port — also doubles as a health check.
const port = Number(process.env.PORT) || 3000;
http
  .createServer((_req, res) => {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ ok: true, service: "songscrap-telegram-bot" }));
  })
  .listen(port, () => console.log(`Health server on :${port}`));

bot.launch({ dropPendingUpdates: true }).then(() => console.log("Bot started (long polling)"));

process.once("SIGINT", () => bot.stop("SIGINT"));
process.once("SIGTERM", () => bot.stop("SIGTERM"));
