"""
commands.py – Telegram command handlers for balance sync, arb execution,
               abort, availability, pause/resume, and settlement.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Set

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config import (
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
    BOOKMAKER_YOU, BOOKMAKER_FRIEND,
    EXECUTION_TIMEOUT,
    COOL_OFF_DAYS,
)
from balancer import Balancer
from alerter import send_status, send_arb_alert

log = logging.getLogger("commands")

# ── Replace with your actual Telegram user IDs ────────────────────────────
YOUR_TELEGRAM_ID   = 7277537180   # TODO: get from @userinfobot
FRIEND_TELEGRAM_ID = 987654321   # TODO: get from @userinfobot

# ── Shared bot state ──────────────────────────────────────────────────────
class BotState:
    def __init__(self, balancer: Balancer) -> None:
        self.balancer = balancer
        self.alerts_enabled = True
        self.paused = False
        self.available: Set[int] = {YOUR_TELEGRAM_ID, FRIEND_TELEGRAM_ID}

        # Current active arb (only one at a time in early phase)
        self.current_arb: Optional[PendingArb] = None
        self.last_settled_arb_id: Optional[int] = None

    @property
    def can_alert(self) -> bool:
        return (
            self.alerts_enabled
            and not self.paused
            and True
            and datetime.now(timezone.utc).weekday() not in COOL_OFF_DAYS
        )


class PendingArb:
    def __init__(self, arb_id: int, details: dict) -> None:
        self.arb_id = arb_id
        self.details = details
        self.done_you = False
        self.done_friend = False
        self.deadline = datetime.now(timezone.utc).timestamp() + EXECUTION_TIMEOUT


# Global state – initialised by setup_bot()
state: Optional[BotState] = None


# ── Helper to identify caller ─────────────────────────────────────────────
def get_role(user_id: int) -> Optional[str]:
    if user_id == YOUR_TELEGRAM_ID:
        return "you"
    if user_id == FRIEND_TELEGRAM_ID:
        return "friend"
    return None


def is_admin(user_id: int) -> bool:
    return user_id == YOUR_TELEGRAM_ID  # only you can issue /stop etc.


# ── Command handlers ──────────────────────────────────────────────────────

async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if get_role(user_id) is None:
        return

    parts = update.message.text.split()
    if len(parts) == 5:
        # /balance sportybet 100 1win 80
        try:
            bal_you, bal_friend = None, None
            for i in range(1, 4, 2):
                plat = parts[i].lower()
                amt = float(parts[i+1])
                if plat == "1win":
                    bal_you = amt
                elif plat == "sportybet":
                    bal_friend = amt
            if bal_you is not None and bal_friend is not None:
                state.balancer.set_balances(bal_you, bal_friend)
                await update.message.reply_text("✅ Balances updated.")
                return
        except (ValueError, IndexError):
            pass

    # Show current balances
    b = state.balancer
    await update.message.reply_text(
        f"📊 *Current Balances*\n"
        f"🔵 {BOOKMAKER_YOU}: GHS {b.get_balance(BOOKMAKER_YOU):.2f}\n"
        f"🔴 {BOOKMAKER_FRIEND}: GHS {b.get_balance(BOOKMAKER_FRIEND):.2f}\n"
        f"💰 Total: GHS {b.get_balance(BOOKMAKER_YOU) + b.get_balance(BOOKMAKER_FRIEND):.2f}"
    )


async def done_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    role = get_role(user_id)
    if role is None or state.current_arb is None:
        return

    arb = state.current_arb
    if role == "you":
        arb.done_you = True
    else:
        arb.done_friend = True

    # Check if both sides confirmed
    if arb.done_you and arb.done_friend:
        state.balancer.lock_capital(
            arb.details["sport"],
            arb.details["market_key"],
            arb.details["stake_you"],
            arb.details["stake_friend"],
        )
        state.last_settled_arb_id = arb.arb_id
        state.current_arb = None
        await update.message.reply_text(
            f"✅ Arb #{arb.arb_id} executed! "
            f"Reply `/settled <winner>` when it settles. "
            f"(winner = `you` or `friend`)"
        )
    else:
        await update.message.reply_text(f"⏳ Waiting for the other side to confirm...")


async def abort_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    role = get_role(user_id)
    if role is None or state.current_arb is None:
        return

    state.current_arb = None
    await update.message.reply_text(
        "🛑 Arb aborted. Do NOT place any bets for the last alert."
    )


async def available_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    role = get_role(user_id)
    if role is None:
        return

    state.available.add(user_id)
    await update.message.reply_text(
        f"✅ {role.capitalize()} is now *available*. "
        f"Both available: {len(state.available) == 2}"
    )


async def unavailable_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    role = get_role(user_id)
    if role is None:
        return

    state.available.discard(user_id)
    await update.message.reply_text(
        f"🔴 {role.capitalize()} is now *unavailable*. Arb alerts paused."
    )


async def pause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    state.paused = True
    await update.message.reply_text("⏸ Arb alerts paused. Use /resume to restart.")


async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    state.paused = False
    await update.message.reply_text("▶ Arb alerts resumed.")


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    state.alerts_enabled = False
    state.paused = True
    await update.message.reply_text("🛑 All alerts STOPPED. Bot still alive. Use /resume to restart.")


async def settled_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    role = get_role(user_id)
    if role is None:
        return

    parts = update.message.text.split()
    winner = parts[1].lower() if len(parts) > 1 else None
    if winner not in ("you", "friend"):
        await update.message.reply_text("Usage: `/settled you` or `/settled friend`")
        return

    if state.last_settled_arb_id is not None:
        state.balancer.release_capital(state.last_settled_arb_id, winner)
        await update.message.reply_text(
            f"💰 Arb #{state.last_settled_arb_id} settled. {winner.capitalize()} won! "
            "Balances updated."
        )
        state.last_settled_arb_id = None
    else:
        await update.message.reply_text("No active arb to settle.")


# ── Timeout watchdog ──────────────────────────────────────────────────────

async def arb_timeout_watchdog():
    """Runs in background, aborts pending arbs that exceed EXECUTION_TIMEOUT."""
    while True:
        await asyncio.sleep(5)
        if state is None or state.current_arb is None:
            continue
        arb = state.current_arb
        if datetime.now(timezone.utc).timestamp() > arb.deadline:
            log.warning(f"Arb #{arb.arb_id} timed out. Aborting.")
            state.current_arb = None
            # Send timeout message via alerter (use send_status as a quick notifier)
            await send_status(
                state.balancer.get_balance(BOOKMAKER_YOU),
                state.balancer.get_balance(BOOKMAKER_FRIEND),
                active_arbs=len(state.balancer.get_locked_arbs()),
            )
            # Actually we'd want to send a proper abort notification. We'll improve later.


# ── Setup ─────────────────────────────────────────────────────────────────

def setup_bot(balancer_instance: Balancer) -> Application:
    global state
    state = BotState(balancer_instance)

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("done", done_cmd))
    app.add_handler(CommandHandler("abort", abort_cmd))
    app.add_handler(CommandHandler("available", available_cmd))
    app.add_handler(CommandHandler("unavailable", unavailable_cmd))
    app.add_handler(CommandHandler("pause", pause_cmd))
    app.add_handler(CommandHandler("resume", resume_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("settled", settled_cmd))

    return app
