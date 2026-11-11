import asyncio
import logging
import os
import subprocess
import tempfile
from pathlib import Path

import edge_tts
import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

# Render -> Environment Variables: BOT_TOKEN = BotFather token
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
VOICE = "uz-UZ-SardorNeural"  # Ayol ovozi: uz-UZ-MadinaNeural
RENDER_LIMIT = asyncio.Semaphore(2)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("ai_video_bot")


def get_font(size, bold=False):
    paths = []
    if bold:
        paths.append("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    paths.append("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    for path in paths:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                pass
    return ImageFont.load_default()


def wrap_text(draw, text, font, max_width):
    lines, current = [], ""
    for word in text.split():
        candidate = word if not current else current + " " + word
        box = draw.textbbox((0, 0), candidate, font=font)
        if box[2] - box[0] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def create_poster(text, kind, path):
    w, h = 1080, 1920
    image = Image.new("RGB", (w, h))
    pixels = image.load()
    top, bottom = (12, 25, 55), (72, 31, 100)
    for y in range(h):
        t = y / (h - 1)
        color = tuple(int(top[i] * (1-t) + bottom[i] * t) for i in range(3))
        for x in range(w):
            pixels[x, y] = color

    draw = ImageDraw.Draw(image)
    title_font, body_font, footer_font = get_font(62, True), get_font(52), get_font(30)
    titles = {"motivation": "BUGUNGI MOTIVATSIYA", "advert": "SIZ UCHUN MAXSUS", "info": "MUHIM MA'LUMOT"}
    title = titles.get(kind, "AI VIDEO")
    box = draw.textbbox((0, 0), title, font=title_font)
    draw.text(((w-(box[2]-box[0]))/2, 260), title, font=title_font, fill=(255,220,120))

    lines = wrap_text(draw, text, body_font, 880)
    line_height = 76
    if len(lines) > 12:
        body_font = get_font(42)
        lines = wrap_text(draw, text, body_font, 900)
        line_height = 62
    y = max(620, (h-len(lines)*line_height)//2)
    for line in lines:
        box = draw.textbbox((0, 0), line, font=body_font)
        draw.text(((w-(box[2]-box[0]))/2, y), line, font=body_font, fill="white")
        y += line_height

    footer = "YANGI FIKR • YANGI IMKONIYAT"
    box = draw.textbbox((0, 0), footer, font=footer_font)
    draw.text(((w-(box[2]-box[0]))/2, 1660), footer, font=footer_font, fill=(220,225,245))
    image.save(path, "PNG")


async def create_video(text, kind, folder):
    image_path = os.path.join(folder, "poster.png")
    voice_path = os.path.join(folder, "voice.mp3")
    video_path = os.path.join(folder, "video.mp4")
    await asyncio.to_thread(create_poster, text, kind, image_path)

    # Uzbekcha AI ovoz uchun internet kerak
    await edge_tts.Communicate(text=text, voice=VOICE).save(voice_path)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    vf = ("zoompan=z='min(zoom+0.0005,1.08)':d=1:"
          "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=25")
    cmd = [ffmpeg, "-y", "-loop", "1", "-framerate", "25", "-i", image_path, "-i", voice_path]

    music = Path("music.mp3")
    if music.exists():
        cmd += ["-stream_loop", "-1", "-i", str(music),
                "-filter_complex", "[1:a]volume=1.0[v];[2:a]volume=0.12[m];[v][m]amix=inputs=2:duration=first[a]",
                "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]

    cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-r", "25", "-c:a", "aac",
            "-b:a", "128k", "-shortest", "-movflags", "+faststart", video_path]

    await asyncio.to_thread(subprocess.run, cmd, check=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300)
    return video_path


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keys = [[InlineKeyboardButton("🔥 Motivatsion", callback_data="motivation")],
            [InlineKeyboardButton("📢 Reklama", callback_data="advert")],
            [InlineKeyboardButton("📚 Ma'lumot slaydi", callback_data="info")]]
    await update.message.reply_text("Assalomu alaykum! 🎬\nQanday video tayyorlaymiz?",
                                    reply_markup=InlineKeyboardMarkup(keys))


async def choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["kind"] = q.data
    await q.edit_message_text("Video uchun 3–500 belgidan iborat matn yuboring.")


async def receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not context.user_data.get("kind"):
        await update.message.reply_text("Avval /start bosing va video turini tanlang.")
        return
    if not 3 <= len(text) <= 500:
        await update.message.reply_text("Matn 3–500 ta belgi orasida bo‘lsin.")
        return

    status = await update.message.reply_text("⏳ Video tayyorlanmoqda... Biroz kuting.")
    async with RENDER_LIMIT:
        try:
            with tempfile.TemporaryDirectory(prefix="ai_video_") as folder:
                path = await create_video(text, context.user_data["kind"], folder)
                with open(path, "rb") as f:
                    await update.message.reply_video(video=f, caption="✅ Videongiz tayyor!",
                                                     supports_streaming=True)
        except Exception as exc:
            logger.exception("Video yaratishda xatolik")
            await update.message.reply_text(
                "Video tayyorlashda xatolik yuz berdi. Keyinroq qayta urinib ko‘ring.\n"
                f"Xatolik: {str(exc)[:250]}"
            )
        finally:
            try:
                await status.delete()
            except Exception:
                pass


async def error_handler(update, context):
    if context.error:
        logger.error("Bot xatoligi", exc_info=(type(context.error), context.error, context.error.__traceback__))


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Render Environment Variables ichiga BOT_TOKEN kiriting.")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(choose_type))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_text))
    app.add_error_handler(error_handler)
    logger.info("Bot ishga tushdi.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
