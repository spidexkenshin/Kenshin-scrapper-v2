"""
bot.py  –  @KENSHIN_ANIME Anime Search Bot  v2.0
Features:
  ✅ Dual site: desidubanime.me + animehindidubbed.in
  ✅ All Episodes at once (concurrent fetch)
  ✅ Quality-sorted download links (4K/1080p/720p/480p/360p)
  ✅ Site selector (both / desi only / hindi only)
  ✅ Fast async with connection pooling
  ✅ Railway / GitHub ready
"""

import asyncio
import logging
import os
import re

import aiohttp
from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter, TimedOut

from scraper import (
    search_anime, get_anime_detail, get_episode_links,
    get_all_episodes_links, make_connector,
    AnimeResult, AnimeDetail, Episode, DownloadLink,
    SITES, SITE_LABELS,
)

load_dotenv()

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("KenshinBot")

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN    = os.getenv("BOT_TOKEN", "")
CHANNEL_TAG  = "@KENSHIN_ANIME"
CHANNEL_URL  = "https://t.me/KENSHIN_ANIME"
MAX_MSG_LEN  = 4096

# Global http session (created at startup)
http_session: aiohttp.ClientSession = None

QUALITY_EMOJI = {
    "4K / 2160p": "🔵",
    "1080p FHD":  "🟣",
    "720p HD":    "🟢",
    "480p":       "🟡",
    "360p":       "🔴",
    "Unknown":    "⚪",
}


# ── Text helpers ──────────────────────────────────────────────────────────────

def esc(t: str) -> str:
    """Escape MarkdownV2."""
    for c in r"\_*[]()~`>#+-=|{}.!":
        t = t.replace(c, f"\\{c}")
    return t

def chunk_text(text: str, size: int = MAX_MSG_LEN) -> list[str]:
    """Split long text into Telegram-safe chunks."""
    return [text[i:i+size] for i in range(0, len(text), size)]

async def safe_edit(msg, text: str, **kwargs):
    try:
        await msg.edit_text(text, **kwargs)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            logger.warning(f"edit_text failed: {e}")
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        await msg.edit_text(text, **kwargs)


# ── Keyboards ─────────────────────────────────────────────────────────────────

def kb_search_results(results: list[AnimeResult]) -> InlineKeyboardMarkup:
    rows = []
    for i, r in enumerate(results):
        icon = SITE_LABELS.get(r.site_key, "")
        rows.append([InlineKeyboardButton(
            f"{icon}  {r.title[:42]}",
            callback_data=f"anime:{i}",
        )])
    rows.append([
        InlineKeyboardButton("🔄 Switch Site", callback_data="switch_site"),
        InlineKeyboardButton("❌ Close",        callback_data="close"),
    ])
    return InlineKeyboardMarkup(rows)


def kb_site_selector(current: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            ("✅ " if current=="both"  else "") + "🌐 Both Sites",
            callback_data="site:both"
        )],
        [InlineKeyboardButton(
            ("✅ " if current=="desi"  else "") + "🟠 DesiDubAnime",
            callback_data="site:desi"
        )],
        [InlineKeyboardButton(
            ("✅ " if current=="hindi" else "") + "🔵 AnimeHindiDubbed",
            callback_data="site:hindi"
        )],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_results")],
    ]
    return InlineKeyboardMarkup(buttons)


def kb_episode_list(detail: AnimeDetail, page: int = 0) -> InlineKeyboardMarkup:
    eps      = detail.episodes
    per_page = 10
    start    = page * per_page
    chunk    = eps[start : start + per_page]

    rows = []
    # 5 buttons per row
    row = []
    for i, ep in enumerate(chunk):
        row.append(InlineKeyboardButton(
            f"▶ {ep.number}",
            callback_data=f"ep:{start+i}",
        ))
        if len(row) == 5:
            rows.append(row); row = []
    if row:
        rows.append(row)

    # Pagination
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"pg:{page-1}"))
    nav.append(InlineKeyboardButton(f"📄 {page+1}/{(len(eps)-1)//per_page+1}", callback_data="noop"))
    if start + per_page < len(eps):
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"pg:{page+1}"))
    if nav:
        rows.append(nav)

    # All Episodes + Back
    rows.append([InlineKeyboardButton(
        f"📦 All {len(eps)} Episodes (send all links)",
        callback_data="all_eps",
    )])
    rows.append([
        InlineKeyboardButton("🔙 Search", callback_data="back_search"),
        InlineKeyboardButton("❌ Close",  callback_data="close"),
    ])
    return InlineKeyboardMarkup(rows)


def kb_episode_links(
    links: list[DownloadLink],
    ep_idx: int,
    page: int,
) -> InlineKeyboardMarkup:
    rows = []
    # Group by quality
    by_quality: dict[str, list[DownloadLink]] = {}
    for lnk in links:
        by_quality.setdefault(lnk.quality, []).append(lnk)

    for quality, qlnks in by_quality.items():
        for lnk in qlnks:
            emoji = QUALITY_EMOJI.get(quality, "⚪")
            rows.append([InlineKeyboardButton(
                f"{emoji} {quality} – {lnk.host[:20]}",
                url=lnk.url,
            )])

    rows.append([
        InlineKeyboardButton("🔙 Episodes", callback_data=f"pg:{page}"),
        InlineKeyboardButton("❌ Close",    callback_data="close"),
    ])
    return InlineKeyboardMarkup(rows)


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🎌 *Kenshin Anime Bot* — v2\\.0\n\n"
        f"Anime ka naam bhejo ya `/search Naruto` likho\\!\n\n"
        f"🟢 *Kya milega:*\n"
        f"• Dual site search \\(DesiDub \\+ HindiDub\\)\n"
        f"• Saare episodes ek baar mein\n"
        f"• Quality\\-wise links: 360p / 720p / 1080p\n\n"
        f"📢 Channel: [{esc(CHANNEL_TAG)}]({CHANNEL_URL})",
        parse_mode=ParseMode.MARKDOWN_V2,
        disable_web_page_preview=True,
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Help*\n\n"
        "*Commands:*\n"
        "`/search <naam>` — Anime search\n"
        "`/site` — Source site change karo\n"
        "`/start` — Welcome message\n\n"
        "*Ya seedha* anime ka naam type karo\\!\n\n"
        "*Episode buttons:*\n"
        "• `▶ 1` `▶ 2` … — Single episode ke links\n"
        "• `📦 All X Episodes` — Saare episodes ke links ek saath\n\n"
        "*Quality:*\n"
        "🔵 4K  🟣 1080p  🟢 720p  🟡 480p  🔴 360p",
        parse_mode=ParseMode.MARKDOWN_V2,
    )

async def cmd_site(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    current = ctx.user_data.get("site", "both")
    await update.message.reply_text(
        "🌐 *Source site choose karo:*",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=kb_site_selector(current),
    )

async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = " ".join(ctx.args).strip() if ctx.args else ""
    if not query:
        await update.message.reply_text(
            "❓ Naam bhi likho: `/search Naruto`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    await _do_search(update, ctx, query)

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if txt and not txt.startswith("/"):
        await _do_search(update, ctx, txt)


# ── Core search flow ──────────────────────────────────────────────────────────

async def _do_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE, query: str):
    site = ctx.user_data.get("site", "both")
    msg  = await update.message.reply_text(
        f"🔍 Searching *{esc(query)}* on {SITE_LABELS.get(site,'both sites')}\\.\\.\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    try:
        results = await search_anime(query, http_session, site)
    except Exception as e:
        logger.error(f"Search error: {e}")
        await safe_edit(msg, "⚠️ Search failed. Dobara try karo.")
        return

    if not results:
        await safe_edit(
            msg,
            f"😔 *{esc(query)}* — kuch nahi mila\\.\nDusra naam try karo ya `/site` se dusri site chuno\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    ctx.user_data["results"] = results
    ctx.user_data["query"]   = query

    site_label = SITE_LABELS.get(site, "🌐 Both")
    await safe_edit(
        msg,
        f"🎌 *{esc(query)}* — {len(results)} results \\| {esc(site_label)}\n\nSelect karo 👇",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=kb_search_results(results),
    )


# ── Callback router ───────────────────────────────────────────────────────────

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    data = q.data
    await q.answer()

    # ── close ────────────────────────────────────────────────
    if data == "close":
        try: await q.message.delete()
        except Exception: pass
        return

    if data == "noop":
        return

    # ── site selector ────────────────────────────────────────
    if data == "switch_site":
        cur = ctx.user_data.get("site", "both")
        await safe_edit(q.message, "🌐 *Source site choose karo:*",
                        parse_mode=ParseMode.MARKDOWN_V2,
                        reply_markup=kb_site_selector(cur))
        return

    if data.startswith("site:"):
        site = data.split(":")[1]
        ctx.user_data["site"] = site
        query = ctx.user_data.get("query","")
        if query:
            await safe_edit(q.message,
                f"✅ Site: *{esc(SITE_LABELS.get(site,'Both'))}*\n\n"
                f"🔄 *{esc(query)}* dobara search kar raha hoon\\.\\.\\.",
                parse_mode=ParseMode.MARKDOWN_V2)
            results = await search_anime(query, http_session, site)
            ctx.user_data["results"] = results
            if not results:
                await safe_edit(q.message, f"😔 *{esc(query)}* nahi mila is site pe\\.",
                                parse_mode=ParseMode.MARKDOWN_V2)
                return
            await safe_edit(q.message,
                f"🎌 *{esc(query)}* — {len(results)} results\n\nSelect karo 👇",
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=kb_search_results(results))
        else:
            await safe_edit(q.message, "✅ Site set\\! Ab search karo\\.",
                            parse_mode=ParseMode.MARKDOWN_V2)
        return

    # ── back to search results ───────────────────────────────
    if data in ("back_search", "back_to_results"):
        results = ctx.user_data.get("results", [])
        query   = ctx.user_data.get("query", "")
        if not results:
            try: await q.message.delete()
            except: pass
            return
        await safe_edit(q.message,
            f"🎌 *{esc(query)}* — {len(results)} results\n\nSelect karo 👇",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=kb_search_results(results))
        return

    # ── anime selected ───────────────────────────────────────
    if data.startswith("anime:"):
        idx     = int(data.split(":")[1])
        results = ctx.user_data.get("results", [])
        if idx >= len(results):
            await safe_edit(q.message, "❌ Expire hua. Dobara search karo."); return

        anime = results[idx]
        ctx.user_data["anime_url"]  = anime.url
        ctx.user_data["anime_site"] = anime.site_key

        await safe_edit(q.message,
            f"⏳ *{esc(anime.title)}* load ho raha hai\\.\\.\\.",
            parse_mode=ParseMode.MARKDOWN_V2)

        detail = await get_anime_detail(anime.url, http_session, anime.site_key)
        if not detail:
            await safe_edit(q.message, "⚠️ Page load nahi hua. Try again."); return

        ctx.user_data["detail"] = detail
        ctx.user_data["ep_page"] = 0
        await _send_detail(q.message, detail, page=0, edit=True)
        return

    # ── pagination ───────────────────────────────────────────
    if data.startswith("pg:"):
        page   = int(data.split(":")[1])
        detail = ctx.user_data.get("detail")
        if not detail:
            await safe_edit(q.message, "⚠️ Data expire hua. Dobara search karo."); return
        ctx.user_data["ep_page"] = page
        try:
            await q.message.edit_reply_markup(reply_markup=kb_episode_list(detail, page))
        except BadRequest: pass
        return

    # ── single episode ───────────────────────────────────────
    if data.startswith("ep:"):
        idx    = int(data.split(":")[1])
        detail = ctx.user_data.get("detail")
        if not detail or idx >= len(detail.episodes):
            await q.answer("Episode nahi mila!", show_alert=True); return

        ep   = detail.episodes[idx]
        page = ctx.user_data.get("ep_page", 0)

        await safe_edit(q.message,
            f"🔗 *{esc(ep.title[:60])}* ke links dhundh raha hoon\\.\\.\\.",
            parse_mode=ParseMode.MARKDOWN_V2)

        links = await get_episode_links(ep.url, http_session)
        if not links:
            links_text = f"🌐 [Episode Page Open Karo]({ep.url})"
            await safe_edit(q.message,
                f"📺 *{esc(detail.title)}*\n🎬 *Ep {esc(ep.number)}*\n\n"
                f"Direct links nahi mile\\. {links_text}",
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data=f"pg:{page}"),
                    InlineKeyboardButton("❌", callback_data="close"),
                ]]),
                disable_web_page_preview=False)
            return

        quality_lines = _format_quality_summary(links)
        text = (
            f"📺 *{esc(detail.title)}*\n"
            f"🎬 *Episode {esc(ep.number)}* — {esc(ep.title[:50])}\n\n"
            f"{quality_lines}\n\n"
            f"Neeche quality choose karo 👇"
        )
        await safe_edit(q.message, text,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=kb_episode_links(links, idx, page),
            disable_web_page_preview=True)
        return

    # ── ALL episodes ─────────────────────────────────────────
    if data == "all_eps":
        detail = ctx.user_data.get("detail")
        if not detail:
            await safe_edit(q.message, "⚠️ Data expire hua. Dobara search karo."); return

        eps   = detail.episodes
        total = len(eps)

        # Progress message
        prog_msg = await q.message.reply_text(
            f"📦 *{esc(detail.title)}*\n"
            f"⏳ Saare *{total}* episodes ke links fetch ho rahe hain\\.\\.\\.\n"
            f"Progress: 0/{total}",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

        done_count = [0]
        last_edit  = [0]

        async def progress(done: int, total: int):
            done_count[0] = done
            # Edit only every 5 episodes to avoid flood
            if done - last_edit[0] >= 5 or done == total:
                last_edit[0] = done
                bar = "█" * (done * 20 // total) + "░" * (20 - done * 20 // total)
                try:
                    await prog_msg.edit_text(
                        f"📦 *{esc(detail.title)}*\n"
                        f"⏳ Fetching links\\.\\.\\. {done}/{total}\n"
                        f"`{bar}`",
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )
                except Exception: pass

        # Fetch all concurrently
        filled_eps = await get_all_episodes_links(eps, http_session, progress)

        # Build the full message text
        await prog_msg.delete()
        await _send_all_episodes(q.message, detail, filled_eps)
        return


# ── Message builders ──────────────────────────────────────────────────────────

async def _send_detail(msg, detail: AnimeDetail, page: int, edit: bool):
    ep_count = len(detail.episodes)
    genres   = ", ".join(detail.genres[:5]) or "N/A"
    desc     = detail.description[:300] or "N/A"

    text = (
        f"🎌 *{esc(detail.title)}*\n\n"
        f"📝 {esc(desc)}\\.\\.\\.\n\n"
        f"🏷️ *Genre:* {esc(genres)}\n"
        f"📺 *Total Episodes:* {ep_count}\n"
        f"🌐 *Source:* {esc(SITE_LABELS.get(detail.site_key,'?'))}\n\n"
        f"Episode select karo 👇\n"
        f"_Ya `📦 All Episodes` se ek baar mein sab lo_"
    )
    kb = kb_episode_list(detail, page)

    # Try with thumbnail
    if detail.thumbnail and not edit:
        try:
            await msg.reply_photo(
                photo=detail.thumbnail, caption=text[:1024],
                parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb,
            )
            return
        except Exception: pass

    if edit:
        await safe_edit(msg, text, parse_mode=ParseMode.MARKDOWN_V2,
                        reply_markup=kb, disable_web_page_preview=True)
    else:
        await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2,
                             reply_markup=kb, disable_web_page_preview=True)


async def _send_all_episodes(orig_msg, detail: AnimeDetail, eps: list[Episode]):
    """
    Send all episode download links.
    Groups into chunks to stay under Telegram message limit.
    Sends as multiple messages if needed.
    """
    header = f"📦 *{esc(detail.title)}* — All Episodes\n\n"
    lines  = []

    for ep in eps:
        ep_line = f"*▶ Episode {esc(ep.number)}* — {esc(ep.title[:40])}\n"
        if ep.download_links:
            for lnk in ep.download_links:
                emoji = QUALITY_EMOJI.get(lnk.quality, "⚪")
                # Telegram inline link inside message
                ep_line += f"  {emoji} [{esc(lnk.quality)} \\| {esc(lnk.host[:15])}]({lnk.url})\n"
        else:
            ep_line += f"  🌐 [Open Page]({ep.url})\n"
        lines.append(ep_line)

    # Split into chunks
    current_chunk = header
    chunks_sent   = 0

    for line in lines:
        if len(current_chunk) + len(line) > MAX_MSG_LEN - 50:
            # Send current chunk
            await orig_msg.reply_text(
                current_chunk,
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_web_page_preview=True,
            )
            chunks_sent += 1
            current_chunk = f"📦 *{esc(detail.title)}* \\(cont\\.\\)\n\n" + line
            # Small delay to avoid flood
            await asyncio.sleep(0.5)
        else:
            current_chunk += line

    # Send last chunk
    if current_chunk.strip():
        await orig_msg.reply_text(
            current_chunk,
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True,
        )
        chunks_sent += 1

    # Summary message
    total_links = sum(len(e.download_links) for e in eps)
    await orig_msg.reply_text(
        f"✅ *Done\\!* {len(eps)} episodes ke {total_links} links send ho gaye\\!\n"
        f"_{chunks_sent} messages mein split kiya_\n\n"
        f"📢 [{esc(CHANNEL_TAG)}]({CHANNEL_URL})",
        parse_mode=ParseMode.MARKDOWN_V2,
        disable_web_page_preview=True,
    )


def _format_quality_summary(links: list[DownloadLink]) -> str:
    qualities = sorted(set(lnk.quality for lnk in links),
                       key=lambda q: ["4K / 2160p","1080p FHD","720p HD","480p","360p","Unknown"].index(q)
                       if q in ["4K / 2160p","1080p FHD","720p HD","480p","360p","Unknown"] else 99)
    parts = []
    for q in qualities:
        emoji = QUALITY_EMOJI.get(q,"⚪")
        count = sum(1 for l in links if l.quality == q)
        parts.append(f"{emoji} {esc(q)} \\({count}\\)")
    return "🎞️ *Available:* " + "  ".join(parts) if parts else ""


# ── Lifecycle ─────────────────────────────────────────────────────────────────

async def post_init(app: Application):
    global http_session
    connector  = make_connector()
    http_session = aiohttp.ClientSession(
        connector=connector,
        headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36",
        },
    )
    logger.info("✅ aiohttp session created with connection pooling")


async def post_shutdown(app: Application):
    if http_session and not http_session.closed:
        await http_session.close()
    logger.info("✅ aiohttp session closed")


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable not set!")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(15)
        .pool_timeout(10)
        .build()
    )

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("site",   cmd_site))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🤖 KenshinAnimeBot started — polling…")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
        poll_interval=0,       # no delay between polls
        timeout=30,
    )


if __name__ == "__main__":
    main()
