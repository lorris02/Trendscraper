"""
Trend Scraper Bot v2
- Scrapes: Google Trends, TikTok Trending, YouTube Trending
- Delivers report twice daily (8am + 8pm UTC)
- No Reddit API needed
"""

import os
import sqlite3
import logging
import requests
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from pytrends.request import TrendReq
from googleapiclient.discovery import build

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── CONFIG ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
DB_PATH = "trends.db"

# ── DATABASE ──────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS keywords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT UNIQUE NOT NULL,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def get_keywords():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT keyword FROM keywords ORDER BY added_at ASC")
    rows = [r[0] for r in c.fetchall()]
    conn.close()
    return rows

def add_keyword(kw):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO keywords (keyword) VALUES (?)", (kw,))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def remove_keyword(kw):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM keywords WHERE LOWER(keyword) = LOWER(?)", (kw,))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0

# ── GOOGLE TRENDS ─────────────────────────────────────────────────────────────
def get_google_trends(keywords):
    try:
        pytrends = TrendReq(hl="en-US", tz=0)
        results = []

        # Top trending in US right now
        trending = pytrends.trending_searches(pn="united_states")
        top_trending = trending[0].tolist()[:10]
        results.append(("🔥 Trending Now (US)", top_trending, "list"))

        # Your keyword interest scores
        if keywords:
            kw_batch = keywords[:5]
            pytrends.build_payload(kw_batch, timeframe="now 1-d", geo="US")
            interest = pytrends.interest_over_time()
            if not interest.empty:
                latest = interest.iloc[-1]
                kw_scores = [(kw, int(latest.get(kw, 0))) for kw in kw_batch]
                kw_scores.sort(key=lambda x: x[1], reverse=True)
                results.append(("📊 Your Keywords (Score /100)", kw_scores, "scores"))

        return results
    except Exception as e:
        logger.error(f"Google Trends error: {e}")
        return [("Google Trends", ["Unavailable — try again later"], "list")]

# ── TIKTOK TRENDING ───────────────────────────────────────────────────────────
def get_tiktok_trending():
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        r = requests.get("https://tokboard.com/", headers=headers, timeout=10)
        if r.status_code == 200:
            import re
            tags = re.findall(r'#([A-Za-z0-9]+)', r.text)
            unique = list(dict.fromkeys(tags))[:10]
            if unique:
                return [f"#{t}" for t in unique]

        # Fallback
        r2 = requests.get("https://www.tiktok.com/explore", headers=headers, timeout=10)
        if r2.status_code == 200:
            import re
            tags = re.findall(r'#([A-Za-z0-9]+)', r2.text)
            unique = list(dict.fromkeys(tags))[:10]
            if unique:
                return [f"#{t}" for t in unique]

        return ["TikTok trends temporarily unavailable"]
    except Exception as e:
        logger.error(f"TikTok error: {e}")
        return ["TikTok data unavailable"]

# ── YOUTUBE TRENDING ──────────────────────────────────────────────────────────
def get_youtube_trending():
    try:
        youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
        resp = youtube.videos().list(
            part="snippet,statistics",
            chart="mostPopular",
            regionCode="US",
            maxResults=10
        ).execute()
        videos = []
        for item in resp.get("items", []):
            videos.append({
                "title": item["snippet"]["title"],
                "channel": item["snippet"]["channelTitle"],
                "views": int(item["statistics"].get("viewCount", 0)),
                "video_id": item["id"]
            })
        return videos
    except Exception as e:
        logger.error(f"YouTube trending error: {e}")
        return []

# ── REPORT BUILDER ────────────────────────────────────────────────────────────
async def build_report():
    keywords = get_keywords()
    session = "🌅 MORNING" if datetime.utcnow().hour < 12 else "🌆 EVENING"
    lines = []

    lines.append(f"📈 *TREND REPORT — {session}*")
    lines.append(f"🕐 {datetime.utcnow().strftime('%B %d, %Y — %H:%M UTC')}\n")

    # Google Trends
    lines.append("━━━━━━━━━━━━━━━━━")
    lines.append("🔍 *GOOGLE TRENDS*\n")
    for title, items, kind in get_google_trends(keywords):
        lines.append(f"*{title}:*")
        if kind == "scores":
            for kw, score in items:
                bar = "█" * min(int(score / 10), 10)
                lines.append(f"  • {kw}: {bar} {score}/100")
        else:
            for i, item in enumerate(items, 1):
                lines.append(f"  {i}. {item}")
        lines.append("")

    # TikTok
    lines.append("━━━━━━━━━━━━━━━━━")
    lines.append("🎵 *TIKTOK TRENDING*\n")
    for i, tag in enumerate(get_tiktok_trending(), 1):
        lines.append(f"  {i}. {tag}")
    lines.append("")

    # YouTube Trending
    lines.append("━━━━━━━━━━━━━━━━━")
    lines.append("▶️ *YOUTUBE TRENDING (US)*\n")
    yt_data = get_youtube_trending()
    if yt_data:
        for i, v in enumerate(yt_data, 1):
            url = f"https://youtube.com/watch?v={v['video_id']}"
            lines.append(
                f"{i}. [{v['title'][:45]}...]({url})\n"
                f"   👤 {v['channel']} | 👁 {v['views']:,}"
            )
    else:
        lines.append("YouTube data unavailable.")
    lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━")
    lines.append("✅ *Next report in 12 hours.*")

    return "\n".join(lines)

# ── SEND REPORT ───────────────────────────────────────────────────────────────
async def send_report(bot: Bot):
    logger.info("Building trend report...")
    report = await build_report()
    for i in range(0, len(report), 4000):
        await bot.send_message(
            chat_id=CHAT_ID,
            text=report[i:i+4000],
            parse_mode="Markdown",
            disable_web_page_preview=True
        )

# ── TELEGRAM COMMANDS ─────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📈 *Trend Scraper Bot*\n\n"
        "Scrapes Google Trends, TikTok + YouTube Trending twice daily.\n\n"
        "*Commands:*\n"
        "/addkeyword `<kw>` — Add keyword to monitor\n"
        "/removekeyword `<kw>` — Remove keyword\n"
        "/listkeywords — See all keywords\n"
        "/gettrends — Run report right now\n"
        "/help — Show this message"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_addkeyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /addkeyword wildlife")
        return
    kw = " ".join(context.args)
    added = add_keyword(kw)
    if added:
        await update.message.reply_text(f"✅ Added '{kw}' to keyword tracking.")
    else:
        await update.message.reply_text(f"'{kw}' is already tracked.")

async def cmd_removekeyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /removekeyword wildlife")
        return
    kw = " ".join(context.args)
    removed = remove_keyword(kw)
    if removed:
        await update.message.reply_text(f"🗑️ Removed '{kw}'.")
    else:
        await update.message.reply_text(f"'{kw}' not found.")

async def cmd_listkeywords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keywords = get_keywords()
    if not keywords:
        await update.message.reply_text("No keywords yet. Use /addkeyword to add one.")
        return
    msg = "*Tracked Keywords:*\n\n" + "\n".join(f"{i}. {k}" for i, k in enumerate(keywords, 1))
    await update.message.reply_text(msg, parse_mode="Markdown")

async def cmd_gettrends(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Fetching trends... give me a sec.")
    await send_report(context.bot)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("addkeyword", cmd_addkeyword))
    app.add_handler(CommandHandler("removekeyword", cmd_removekeyword))
    app.add_handler(CommandHandler("listkeywords", cmd_listkeywords))
    app.add_handler(CommandHandler("gettrends", cmd_gettrends))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_report, "cron", hour=8, minute=0, args=[app.bot])
    scheduler.add_job(send_report, "cron", hour=20, minute=0, args=[app.bot])
    scheduler.start()

    logger.info("Trend Scraper Bot running.")
    app.run_polling()

if __name__ == "__main__":
    main()
