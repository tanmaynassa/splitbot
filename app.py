"""
SplitBot — Multi-user grocery expense splitter.
Telegram bot + Splitwise OAuth + aiohttp server.
"""

import os
import re
import logging
import asyncio
import tempfile
from aiohttp import web
from telegram import Bot, Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
import db
from invoice_parser import parse_invoice, format_item_list, compute_split, format_split_summary
from splitwise_client import (
    SplitwiseClient, get_auth_url, exchange_code_for_token, build_expense_details,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Env vars
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "")
PORT = int(os.environ.get("PORT", 10000))

# Conversation states
SETUP_FLATMATES, CONFIRM_FLATMATES, AWAITING_TAGS, CONFIRMING = range(4)

# Global bot app
bot_app = Application.builder().token(TOKEN).build()


def get_callback_url():
    url = RENDER_URL
    if not url.startswith("http"):
        url = f"https://{url}"
    return f"{url}/auth/callback"


# ── Commands ──

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start — check user status and guide them."""
    tid = update.effective_user.id
    user = db.get_user(tid)

    if not user:
        db.create_user(tid)
        user = db.get_user(tid)

    if not user.get("splitwise_token"):
        # Need OAuth
        auth_url = get_auth_url(tid, get_callback_url())
        await update.message.reply_text(
            "👋 Welcome to SplitBot!\n\n"
            "I help you split grocery bills with your flatmates "
            "and log them directly to Splitwise.\n\n"
            f"First, connect your Splitwise account:\n"
            f"👉 [Click here to connect]({auth_url})\n\n"
            "After connecting, come back here.",
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        return ConversationHandler.END

    if not user.get("setup_complete"):
        await update.message.reply_text(
            "Your Splitwise is connected! ✅\n\n"
            "Now tell me who you split groceries with. "
            "Send their names (as they appear on Splitwise), separated by commas.\n\n"
            "Example: `Rahul, Priya`",
            parse_mode="Markdown",
        )
        return SETUP_FLATMATES

    # Fully set up — ready to go
    flatmates = db.get_flatmates(tid)
    names = ", ".join(f["name"] for f in flatmates)
    await update.message.reply_text(
        f"✅ You're all set! Splitting with: {names}\n\n"
        f"Send me a grocery invoice PDF anytime.\n\n"
        f"Commands:\n"
        f"/setup — change your flatmates\n"
        f"/reset — disconnect and start over",
    )
    return ConversationHandler.END


async def setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /setup — reconfigure flatmates."""
    tid = update.effective_user.id
    user = db.get_user(tid)

    if not user or not user.get("splitwise_token"):
        await update.message.reply_text("You need to connect Splitwise first. Send /start")
        return ConversationHandler.END

    await update.message.reply_text(
        "Who do you split groceries with? "
        "Send their names separated by commas.\n\n"
        "Example: `Rahul, Priya`",
        parse_mode="Markdown",
    )
    return SETUP_FLATMATES


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /reset — disconnect everything."""
    tid = update.effective_user.id
    db.clear_flatmates(tid)
    db.update_user(tid, splitwise_token=None, splitwise_user_id=None, setup_complete=False)
    context.user_data.clear()
    await update.message.reply_text("🔄 Reset complete. Send /start to set up again.")
    return ConversationHandler.END


# ── Flatmate Setup ──

async def handle_flatmate_names(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User sent flatmate names — search on Splitwise."""
    tid = update.effective_user.id
    user = db.get_user(tid)
    text = update.message.text.strip()

    names = [n.strip() for n in text.split(",") if n.strip()]
    if not names:
        await update.message.reply_text("Send at least one name. Example: `Rahul, Priya`", parse_mode="Markdown")
        return SETUP_FLATMATES

    sw = SplitwiseClient(user["splitwise_token"])
    found = []
    not_found = []

    for name in names:
        matches = sw.find_friends_by_name(name)
        if matches:
            best = matches[0]
            full_name = f"{best.get('first_name', '')} {best.get('last_name', '')}".strip()
            found.append({"name": full_name, "id": best["id"], "search": name})
        else:
            not_found.append(name)

    if not_found:
        await update.message.reply_text(
            f"❌ Couldn't find: {', '.join(not_found)} in your Splitwise friends.\n\n"
            f"Make sure they're added as friends on Splitwise, "
            f"then try again with their names.",
        )
        return SETUP_FLATMATES

    # Store found flatmates for confirmation
    context.user_data["pending_flatmates"] = found

    lines = ["Found these people on your Splitwise:\n"]
    for f in found:
        lines.append(f"  ✅ {f['name']}")
    lines.append(f"\nLook right? Type `yes` to confirm or send different names.")

    await update.message.reply_text("\n".join(lines))
    return CONFIRM_FLATMATES


async def handle_confirm_flatmates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm flatmate selection."""
    tid = update.effective_user.id
    text = update.message.text.strip().lower()

    if text not in ("yes", "y", "ok"):
        await update.message.reply_text(
            "Send the correct names separated by commas, or type `yes` to confirm.",
        )
        return CONFIRM_FLATMATES

    pending = context.user_data.get("pending_flatmates", [])
    if not pending:
        await update.message.reply_text("Something went wrong. Send /setup to try again.")
        return ConversationHandler.END

    # Save flatmates to DB
    db.clear_flatmates(tid)
    for f in pending:
        db.add_flatmate(tid, f["name"], f["id"])

    db.update_user(tid, setup_complete=True)
    context.user_data.pop("pending_flatmates", None)

    names = ", ".join(f["name"] for f in pending)
    await update.message.reply_text(
        f"✅ All set! Splitting with: {names}\n\n"
        f"Now just send me a grocery invoice PDF anytime.",
    )
    return ConversationHandler.END


# ── Invoice Processing ──

async def handle_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming PDF invoice."""
    tid = update.effective_user.id
    user = db.get_user(tid)

    if not user or not user.get("setup_complete"):
        await update.message.reply_text("You need to set up first. Send /start")
        return ConversationHandler.END

    document = update.message.document
    if not document.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("Send a PDF invoice file.")
        return ConversationHandler.END

    await update.message.reply_text("📄 Parsing invoice...")

    # Download PDF
    file = await document.get_file()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        tmp_path = tmp.name

    try:
        parsed = parse_invoice(tmp_path)
    except Exception as e:
        logger.error(f"Parse error: {e}")
        await update.message.reply_text("❌ Couldn't parse this invoice. Is it a grocery order PDF?")
        return ConversationHandler.END
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    if not parsed["items"]:
        await update.message.reply_text("No items found in the invoice.")
        return ConversationHandler.END

    # Get flatmate names for display
    flatmates = db.get_flatmates(tid)
    flatmate_names = [f["name"].split()[0] for f in flatmates]  # first names only

    context.user_data["parsed"] = parsed
    context.user_data["flatmates"] = flatmates

    msg = format_item_list(parsed, flatmate_names)
    await update.message.reply_text(msg, parse_mode="Markdown")

    return AWAITING_TAGS


async def handle_tags(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle item tagging."""
    text = update.message.text.strip().lower()
    parsed = context.user_data.get("parsed")
    flatmates = context.user_data.get("flatmates", [])

    if not parsed:
        await update.message.reply_text("No pending order. Send a new invoice PDF.")
        return ConversationHandler.END

    valid_srs = {item["sr"] for item in parsed["items"]}

    # Build flatmate lookup: {lowercase_first_name: flatmate_record}
    fm_lookup = {}
    for f in flatmates:
        first_name = f["name"].split()[0].lower()
        fm_lookup[first_name] = f

    # Handle "all" shortcut
    if text in ("all", "shared", "all shared", "split all"):
        personal_indices = []
        flatmate_tagged = {}
    else:
        # Parse tags
        personal_indices = []
        flatmate_tagged = {}  # {flatmate_name: [sr_numbers]}

        mine_match = re.search(r"mine\s*:\s*([\d,\s]+)", text)
        if mine_match:
            personal_indices = [int(x.strip()) for x in mine_match.group(1).split(",") if x.strip().isdigit()]

        for fm_key, fm_record in fm_lookup.items():
            fm_match = re.search(rf"{fm_key}\s*:\s*([\d,\s]+)", text)
            if fm_match:
                srs = [int(x.strip()) for x in fm_match.group(1).split(",") if x.strip().isdigit()]
                flatmate_tagged[fm_key] = srs

        if not mine_match and not flatmate_tagged:
            fm_examples = "\n".join(f"`{name}: 3`" for name in fm_lookup.keys())
            await update.message.reply_text(
                f"Couldn't understand. Reply like:\n"
                f"`mine: 1,2`\n{fm_examples}\n\n"
                f"Or type `all` if everything splits equally.",
                parse_mode="Markdown",
            )
            return AWAITING_TAGS

        # Validate item numbers
        all_tagged = personal_indices + [sr for srs in flatmate_tagged.values() for sr in srs]
        invalid = [x for x in all_tagged if x not in valid_srs]
        if invalid:
            await update.message.reply_text(f"Invalid item numbers: {invalid}. Valid: {sorted(valid_srs)}")
            return AWAITING_TAGS

        # Check for overlapping tags
        all_tagged_set = set()
        for sr in personal_indices:
            if sr in all_tagged_set:
                await update.message.reply_text(f"Item {sr} is tagged twice. Fix and resend.")
                return AWAITING_TAGS
            all_tagged_set.add(sr)
        for fm_name, srs in flatmate_tagged.items():
            for sr in srs:
                if sr in all_tagged_set:
                    await update.message.reply_text(f"Item {sr} is tagged twice. Fix and resend.")
                    return AWAITING_TAGS
                all_tagged_set.add(sr)

    # Parse "split among" / "rest between" — optional line
    split_among_match = re.search(
        r"(?:rest split among|rest among|rest between|rest split between|rest)\s*:\s*(.+)",
        text,
    )

    # Determine who shares the remaining (untagged) items
    if split_among_match:
        split_names_raw = [n.strip().lower() for n in split_among_match.group(1).split(",")]
        splitter_ids = []
        include_self = False

        for name in split_names_raw:
            if name in ("me", "mine", "myself"):
                include_self = True
            elif name in fm_lookup:
                splitter_ids.append(fm_lookup[name]["splitwise_user_id"])
            else:
                # Unknown name
                known = ", ".join(fm_lookup.keys())
                await update.message.reply_text(
                    f"❌ Don't know who `{name}` is.\n"
                    f"Known names: me, {known}",
                    parse_mode="Markdown",
                )
                return AWAITING_TAGS

        if not include_self and not splitter_ids:
            await update.message.reply_text("Split among needs at least one person.")
            return AWAITING_TAGS

        # Build splitter list for compute_split
        split_among = {"include_self": include_self, "flatmate_ids": splitter_ids}
    else:
        # Default: split among everyone
        split_among = None

    # Build flatmate_ids mapping
    flatmate_ids = {}
    for fm_key, fm_record in fm_lookup.items():
        flatmate_ids[fm_key] = fm_record["splitwise_user_id"]

    split = compute_split(
        parsed["items"],
        personal_indices,
        flatmate_tagged,
        flatmate_ids,
        1 + len(flatmates),  # default num_splitters (everyone)
        split_among=split_among,
    )
    context.user_data["split"] = split

    # Build display names
    fm_display = {f["splitwise_user_id"]: f["name"].split()[0] for f in flatmates}
    user = db.get_user(update.effective_user.id)
    user_name = user.get("splitwise_name", "You")

    msg = format_split_summary(split, parsed.get("order_date", ""), user_name, fm_display)
    await update.message.reply_text(msg, parse_mode="Markdown")

    return CONFIRMING


async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle ok/cancel confirmation."""
    text = update.message.text.strip().lower()
    tid = update.effective_user.id

    if text == "cancel":
        context.user_data.clear()
        await update.message.reply_text("❌ Discarded. Send another invoice whenever.")
        return ConversationHandler.END

    if text != "ok":
        await update.message.reply_text("Type `ok` to confirm or `cancel` to discard.", parse_mode="Markdown")
        return CONFIRMING

    parsed = context.user_data.get("parsed")
    split = context.user_data.get("split")
    flatmates = context.user_data.get("flatmates", [])
    user = db.get_user(tid)

    # Check if anyone owes money
    has_debt = any(v > 0 for k, v in split["shares"].items() if k != "user")

    if has_debt:
        await update.message.reply_text("⏳ Logging to Splitwise...")

        try:
            sw = SplitwiseClient(user["splitwise_token"])
            my_sw_id = user["splitwise_user_id"]

            # Build shares dict with Splitwise user IDs
            sw_shares = {my_sw_id: split["shares"]["user"]}
            for fm_id, amount in split["shares"].items():
                if fm_id != "user":
                    sw_shares[fm_id] = amount

            fm_display = {f["splitwise_user_id"]: f["name"].split()[0] for f in flatmates}
            details = build_expense_details(split, user.get("splitwise_name", "You"), fm_display)

            platform = parsed.get("platform", "Grocery")
            description = f"{platform} — {parsed.get('order_date', 'order')}"

            sw.create_expense(
                description=description,
                total_cost=split["order_total"],
                payer_id=my_sw_id,
                shares=sw_shares,
                details=details,
            )

            lines = ["✅ *Logged to Splitwise!*\n"]
            for fm_id, amount in split["shares"].items():
                if fm_id != "user" and amount > 0:
                    fm_name = fm_display.get(fm_id, "Flatmate")
                    lines.append(f"{fm_name} owes you *₹{amount:.2f}*")

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Splitwise error: {e}")
            await update.message.reply_text(f"❌ Splitwise error: {e}")
    else:
        await update.message.reply_text(
            f"✅ *All yours — ₹{split['order_total']:.2f}*\nNo split needed.",
            parse_mode="Markdown",
        )

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled. Send a new invoice whenever.")
    return ConversationHandler.END


# ── Web Server (aiohttp) ──

async def oauth_callback(request):
    """Handle Splitwise OAuth callback."""
    code = request.query.get("code")
    state = request.query.get("state")  # telegram_id

    if not code or not state:
        return web.Response(
            text="<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
                 "<h2>SplitBot</h2><p>Use the Telegram bot to get started.</p></body></html>",
            content_type="text/html",
        )

    try:
        tid = int(state)
        token_data = exchange_code_for_token(code, get_callback_url())
        access_token = token_data["access_token"]

        # Get user info from Splitwise
        sw = SplitwiseClient(access_token)
        sw_user = sw.get_current_user()
        sw_name = f"{sw_user.get('first_name', '')} {sw_user.get('last_name', '')}".strip()

        # Store in DB
        db.update_user(
            tid,
            splitwise_token=access_token,
            splitwise_user_id=sw_user["id"],
            splitwise_name=sw_name,
        )

        # Send message to user on Telegram
        bot = Bot(TOKEN)
        await bot.send_message(
            chat_id=tid,
            text=(
                f"✅ Connected to Splitwise as *{sw_name}*!\n\n"
                f"Now tell me who you split groceries with. "
                f"Send their names separated by commas.\n\n"
                f"Example: `Rahul, Priya`"
            ),
            parse_mode="Markdown",
        )

        return web.Response(
            text="<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
                 "<h2>✅ Connected!</h2><p>Go back to Telegram.</p></body></html>",
            content_type="text/html",
        )

    except Exception as e:
        logger.error(f"OAuth error: {e}")
        return web.Response(text=f"Error: {e}", status=500)


async def telegram_webhook(request):
    """Handle incoming Telegram webhook updates."""
    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)
    return web.Response(text="ok")


async def health(request):
    return web.Response(text="ok")


# ── Main ──

async def main():
    # Set up bot handlers
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("setup", setup),
            MessageHandler(filters.Document.PDF, handle_pdf),
        ],
        states={
            SETUP_FLATMATES: [
                MessageHandler(filters.Document.PDF, handle_pdf),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_flatmate_names),
            ],
            CONFIRM_FLATMATES: [
                MessageHandler(filters.Document.PDF, handle_pdf),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_confirm_flatmates),
            ],
            AWAITING_TAGS: [
                MessageHandler(filters.Document.PDF, handle_pdf),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_tags),
            ],
            CONFIRMING: [
                MessageHandler(filters.Document.PDF, handle_pdf),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_confirm),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("reset", reset),
        ],
    )

    bot_app.add_handler(conv_handler)

    # Initialize bot
    await bot_app.initialize()
    await bot_app.start()

    # Set webhook
    webhook_url = RENDER_URL
    if not webhook_url.startswith("http"):
        webhook_url = f"https://{webhook_url}"
    webhook_url = f"{webhook_url}/webhook"

    await bot_app.bot.set_webhook(url=webhook_url)
    logger.info(f"Webhook set: {webhook_url}")

    # Start web server
    app = web.Application()
    app.router.add_post("/webhook", telegram_webhook)
    app.router.add_get("/auth/callback", oauth_callback)
    app.router.add_get("/", oauth_callback)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info(f"Server running on port {PORT}")

    # Keep running
    try:
        await asyncio.Event().wait()
    finally:
        await bot_app.stop()
        await bot_app.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
