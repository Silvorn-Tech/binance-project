from __future__ import annotations

from dataclasses import replace
from datetime import time as dt_time
from pathlib import Path
import html
import time
from zoneinfo import ZoneInfo

from loguru import logger
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from telegram.error import BadRequest, RetryAfter, TimedOut, NetworkError
import asyncio

from hermes.service.bot_builder import BotBuilder
from hermes.service.bot_service import BotService
from hermes.service.bot_state import BotRuntimeState
from hermes.utils.trading_mode import TradingMode
from hermes.utils.report_writer import write_bot_report


def escape_html(text: str) -> str:
    return html.escape(text, quote=False)

EDIT_HELP_TEXT = (
    "🆘 <b>Parameter help</b>\n\n"
    "💰 <b>capital_pct</b>\n"
    "Percentage of your total wallet balance assigned to this bot.\n"
    "Example:\n"
    "• Wallet = 1000 USDT\n"
    "• capital_pct = 20%\n"
    "→ Bot can use up to 200 USDT\n\n"
    "📊 <b>trade_pct</b>\n"
    "Percentage of the bot capital used per trade.\n"
    "Example:\n"
    "• Bot capital = 200 USDT\n"
    "• trade_pct = 25%\n"
    "→ Each trade uses ~50 USDT\n\n"
    "📦 <b>min_trade_usdt</b>\n"
    "Minimum amount per trade (Binance constraint).\n"
    "Used to avoid orders rejected by the exchange.\n"
    "⚠️ Recommended: ≥ 7 USDT\n\n"
    "🔁 <b>max_buys_per_day</b>\n"
    "Maximum number of buy operations per day.\n"
    "Helps control overtrading.\n\n"
    "• Can be disabled\n"
    "• When disabled → unlimited trades\n\n"
    "📉 <b>daily_budget_usdt</b>\n"
    "Maximum total USDT spent per day.\n"
    "Protects against bad market days.\n\n"
    "• Can be disabled\n"
    "• When disabled → no daily spending limit\n\n"
    "📐 <b>trailing_pct</b>\n"
    "Trailing stop percentage to protect profits.\n\n"
    "• Lower = safer, faster exits\n"
    "• Higher = more room for price movement\n\n"
    "Examples:\n"
    "• Sentinel → 1.0%\n"
    "• Equilibrium → 1.5%\n"
    "• Vortex → 3.0%\n\n"
    "📈 <b>new_high_epsilon_pct</b>\n"
    "Minimum price increase required to count as a new high.\n"
    "Helps reduce noise in trailing updates.\n\n"
    "🚫 <b>Disable limits</b>\n"
    "Disables:\n"
    "• max_buys_per_day\n"
    "• daily_budget_usdt\n\n"
    "⚠️ Bot will trade without limits.\n\n"
    "🔄 <b>symbol / base_asset</b>\n"
    "Trading pair and base asset.\n"
    "Changing this will restart the bot.\n"
)


class Controller:
    """
    Telegram controller.
    Entry point to application logic via Telegram commands.
    """

    _PROFILE_TTL_SECONDS = {
        "sentinel": 5,
        "equilibrium": 4,
        "vortex": 3,
    }
    _VORTEX_MIN_TRADES = 30
    _VORTEX_CONFIDENCE_THRESHOLD = 0.55
    _LIVE_DRAWDOWN_LIMIT = 0.05

    def __init__(self, bot_service: BotService, telegram_token: str):
        self.bot_service = bot_service
        self.telegram_token = telegram_token
        self._pending_configs: dict[int, dict] = {}
        self._menu_message_id: dict[int, int] = {}
        self._last_query: dict[int, object] = {}




    # =========================
    # Bootstrap
    # =========================
    def start(self) -> None:
        app = ApplicationBuilder().token(self.telegram_token).build()

        # JobQueue may be None depending on PTB extras installation
        if app.job_queue is not None:
            app.job_queue.run_repeating(
                self._auto_refresh_dashboards,
                interval=15,
                first=10,
            )
            app.job_queue.run_daily(
                self._send_daily_summary,
                time=dt_time(hour=18, minute=0, tzinfo=ZoneInfo("America/Bogota")),
            )
            app.job_queue.run_daily(
                self._send_daily_summary,
                time=dt_time(hour=6, minute=0, tzinfo=ZoneInfo("America/Bogota")),
            )

        else:
            logger.warning("⚠️ JobQueue not available. Auto-refresh dashboards disabled.")

        app.add_handler(CommandHandler("start", self.start_bot))
        app.add_handler(CommandHandler("stop", self.stop_bot))
        app.add_handler(CommandHandler("restart", self.restart_bot))
        app.add_handler(CommandHandler("status", self.status))
        app.add_handler(CommandHandler("help", self.help))
        app.add_handler(CommandHandler("confirm", self.confirm))
        app.add_handler(CommandHandler("cancel", self.cancel))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        app.add_handler(CallbackQueryHandler(self.on_button))
        app.add_error_handler(self._on_error)

        logger.info("📡 Telegram controller started")
        app.run_polling()

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        err = context.error
        if err is None:
            logger.warning(
                "Telegram error handler invoked without exception | update=%s",
                update,
            )
            return
        if isinstance(err, (TimedOut, NetworkError)):
            logger.warning("Telegram network error: %s", err)
            return
        logger.opt(exception=err).error("Unhandled Telegram error")

    # =========================
    # Render helper (HTML)
    # =========================
    async def _render(self, *, query, text: str, keyboard):
        try:
            await query.edit_message_text(
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

        except RetryAfter as e:
            logger.warning(
                f"⏳ Flood control hit (retry_after={e.retry_after}s). Skipping render."
            )
            return

        except BadRequest as e:
            if "Message is not modified" in str(e):
                return
            logger.warning(f"BadRequest on render: {e}")



    # =========================
    # MAIN MENU
    # =========================
    def _main_menu_payload(self):
        text = (
            "🤖 <b>HERMES is online</b>\n\n"
            "Select an option:"
        )
        keyboard = [
            [InlineKeyboardButton("🚀 Start new bot", callback_data="start_new_bot")],
            [InlineKeyboardButton("📊 Running bots", callback_data="status")],
            [InlineKeyboardButton("🤖 Manage bots", callback_data="manage_menu")],
            [InlineKeyboardButton("📈 Reports", callback_data="reports_menu")],
            [InlineKeyboardButton("🛑 Stop a bot", callback_data="stop_menu")],
            [InlineKeyboardButton("ℹ️ Help", callback_data="help")],
        ]
        return text, keyboard

    def _build_running_bots_text(self) -> str:
        states = self.bot_service.get_all_states()

        if not states:
            return "🤷 <b>No bots running</b>"

        def limit_value(value, disabled: bool, suffix: str = "") -> str:
            if disabled:
                return "∞"
            return f"{value}{suffix}"

        lines = ["📊 <b>Running bots</b>\n"]
        for state in states:
            cfg = state.config
            if cfg is None:
                lines.append(
                    f"🟢 <b>{state.symbol}</b> ({state.profile})\n"
                    f"• Status: <code>{escape_html(state.last_action)}</code>\n"
                )
                continue

            lines.append(
                f"🟢 <b>{state.symbol}</b> ({state.profile})\n"
                f"• capital_pct: {cfg.capital_pct}\n"
                f"• trade_pct: {cfg.trade_pct}\n"
                f"• min_trade_usdt: {cfg.min_trade_usdt}\n"
                f"• max_buys/day: {limit_value(cfg.max_buys_per_day, cfg.disable_max_buys_per_day)}\n"
                f"• daily_budget: {limit_value(cfg.daily_budget_usdt, cfg.disable_daily_budget, ' USDT')}\n"
                f"• trailing: {cfg.trailing_pct * 100:.2f} %\n"
                f"• SMA: {cfg.sma_fast} / {cfg.sma_slow}\n"
                f"• Status: <code>{escape_html(state.last_action)}</code>\n"
            )

        return "\n".join(lines)

    def _build_running_bots_keyboard(self) -> list[list[InlineKeyboardButton]]:
        rows: list[list[InlineKeyboardButton]] = []
        for state in self.bot_service.get_all_states():
            rows.append(
                [InlineKeyboardButton(f"📊 {state.symbol}", callback_data=f"dash_open:{state.symbol}")]
            )

        rows.append([InlineKeyboardButton("➕ Start new bot", callback_data="start_new_bot")])
        rows.append([InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")])
        return rows

    async def _send_main_menu(self, *, chat_id, context):
        text, keyboard = self._main_menu_payload()
        await self._safe_edit_menu(
            chat_id=chat_id,
            context=context,
            text=text,
            keyboard=keyboard,
        )

    async def _safe_edit_menu(self, *, chat_id, context, text: str, keyboard):
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=self._menu_message_id[chat_id],
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                return
            raise

    def _profile_ttl(self, profile: str | None, default: int) -> int:
        if not profile:
            return default
        return self._PROFILE_TTL_SECONDS.get(profile, default)

    def _stop_wait_text(self, state: BotRuntimeState | None) -> str:
        if state is None:
            return "⏳ <b>Please wait…</b>\nStopping bot safely."
        return (
            "⏳ <b>Please wait…</b>\n"
            f"Stopping bot safely.\nLast action: <code>{escape_html(state.last_action)}</code>"
        )

    def _compute_vortex_confidence(self, state: BotRuntimeState) -> tuple[float, float, float, float]:
        trades = state.trades_count or 0
        win_rate = (state.wins / trades) if trades else 0.0

        avg_win = (state.total_win / state.wins) if state.wins else 0.0
        avg_loss = (state.total_loss / state.losses) if state.losses else 0.0

        expectancy = (avg_win * win_rate) - (avg_loss * (1 - win_rate))

        if avg_loss > 0:
            normalized_expectancy = min(max(expectancy / avg_loss, 0.0), 1.0)
        else:
            normalized_expectancy = 1.0 if expectancy > 0 else 0.0

        recent_pnls = state.recent_pnls[-10:]
        recent_sum = sum(recent_pnls) if recent_pnls else 0.0
        denom = (avg_loss if avg_loss > 0 else 1.0) * max(len(recent_pnls), 1)
        ratio = max(min(recent_sum / denom, 1.0), -1.0)
        recent_consistency = (ratio + 1.0) / 2.0

        confidence = (
            0.5 * win_rate +
            0.3 * normalized_expectancy +
            0.2 * recent_consistency
        )
        confidence = min(max(confidence, 0.0), 1.0)

        return confidence, win_rate, expectancy, state.max_drawdown

    async def _send_vortex_confirmation(self, *, context, chat_id: int, state: BotRuntimeState):
        confidence, win_rate, expectancy, max_drawdown = self._compute_vortex_confidence(state)
        expectancy_pct = 0.0
        if state.virtual_capital > 0:
            expectancy_pct = (expectancy / state.virtual_capital) * 100

        text = (
            "🧠 <b>VORTEX READY — REAL CAPITAL CONFIRMATION</b>\n\n"
            "<b>Simulation summary:</b>\n"
            f"• Trades executed: {state.trades_count}\n"
            f"• Win rate: {win_rate * 100:.1f} %\n"
            f"• Expectancy: {expectancy_pct:+.2f} %\n"
            f"• Max drawdown: {max_drawdown * 100:.1f} %\n"
            f"• Virtual PnL: {state.virtual_pnl:+.2f} USDT\n\n"
            f"📊 <b>Confidence score:</b> {confidence * 100:.0f} %\n\n"
            "⚠️ <i>This is NOT a guarantee.</i>\n"
            "This score is based on historical simulated performance.\n\n"
            "Do you want to enable REAL trading with limited capital?"
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("✅ Yes, invest real capital", callback_data=f"vortex_live_yes:{state.symbol}")],
                    [InlineKeyboardButton("❌ No, keep simulating", callback_data=f"vortex_live_no:{state.symbol}")],
                ]
            ),
        )

    async def _send_temp_message(
        self,
        *,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        text: str,
        seconds: int = 3,
    ):
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

        async def _auto_delete():
            await asyncio.sleep(seconds)
            try:
                await msg.delete()
            except Exception:
                return

        asyncio.create_task(_auto_delete())

    async def _send_deletable_message(
        self,
        *,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        text: str,
        delete_after: int | None = None,
    ):
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🗑️ Delete", callback_data="delete_self")]]
            ),
        )

        if delete_after is None:
            return

        async def _auto_delete():
            await asyncio.sleep(delete_after)
            try:
                await msg.delete()
            except Exception:
                return

        asyncio.create_task(_auto_delete())


    # =========================
    # /start
    # =========================
    async def start_bot(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        args = context.args

        text, keyboard = self._main_menu_payload()

        # 👇 Crear mensaje base SOLO UNA VEZ
        if chat_id not in self._menu_message_id:
            msg = await update.message.reply_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            self._menu_message_id[chat_id] = msg.message_id
        else:
            await self._safe_edit_menu(
                chat_id=chat_id,
                context=context,
                text=text,
                keyboard=keyboard,
            )

        return


    # =========================
    # Profile selector
    # =========================
    async def _start_profile_selector(self, *, query=None):
        text = (
            "⚡ <b>HERMES — Select a bot profile</b>\n\n"
            "Choose the risk profile you want to use:"
        )
        keyboard = [
            [InlineKeyboardButton("🛡️ Sentinel (Conservative)", callback_data="profile:sentinel")],
            [InlineKeyboardButton("⚖️ Equilibrium (Balanced)", callback_data="profile:equilibrium")],
            [InlineKeyboardButton("🌪️ Vortex (Aggressive)", callback_data="profile:vortex")],
            [InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")],
        ]
        await self._render(query=query, text=text, keyboard=keyboard)

    # =========================
    # Show config
    # =========================
    async def _show_config(self, *, query=None, pending: dict):
        config = pending["config"]

        text = (
            "⚡ <b>HERMES — Bot Configuration</b>\n\n"
            f"<b>Profile:</b> {escape_html(pending['profile'])}\n"
            f"<b>Symbol:</b> {escape_html(pending['symbol'])}\n\n"
            "<b>Current configuration:</b>\n"
            f"• capital_pct: {config.capital_pct}\n"
            f"• trade_pct: {config.trade_pct}\n"
            f"• min_trade_usdt: {config.min_trade_usdt}\n"
            f"• max_buys_per_day: {'∞' if config.disable_max_buys_per_day else config.max_buys_per_day}\n"
            f"• daily_budget_usdt: {'∞' if config.disable_daily_budget else f'{config.daily_budget_usdt} USDT'}\n"
            f"• sma_fast: {config.sma_fast}\n"
            f"• sma_slow: {config.sma_slow}\n"
            f"• trailing_pct: {config.trailing_pct}\n\n"
            f"• new_high_epsilon_pct: {config.new_high_epsilon_pct}\n\n"
            "What do you want to do?"
        )

        if pending.get("mode") == "manage":
            bot_id = pending.get("bot_id", "")
            keyboard = [
                [InlineKeyboardButton("✅ Apply & restart", callback_data=f"manage_apply:{bot_id}")],
                [InlineKeyboardButton("✏️ Edit parameters", callback_data="edit")],
                [InlineKeyboardButton("⬅️ Back", callback_data=f"manage_bot:{bot_id}")],
            ]
        else:
            keyboard = [
                [InlineKeyboardButton("✅ Start (default)", callback_data="confirm")],
                [InlineKeyboardButton("✏️ Edit parameters", callback_data="edit")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
            ]

        await self._render(query=query, text=text, keyboard=keyboard)

    # =========================
    # Reports menu
    # =========================
    async def _show_reports_menu(self, *, query):
        text = (
            "📈 <b>Reports</b>\n\n"
            "Select the report you want to generate:"
        )
        keyboard = [
            [InlineKeyboardButton("📈 Global performance (CSV)", callback_data="report_global")],
            [InlineKeyboardButton("🌍 General report (CSV)", callback_data="report_general")],
            [InlineKeyboardButton("🧾 Trade Report (CSV)", callback_data="report_trades")],
            [InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")],
        ]
        await self._render(query=query, text=text, keyboard=keyboard)

    # =========================
    # Button callbacks
    # =========================
    async def on_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat.id

        self._last_query[chat_id] = query

        action = query.data

        if action.startswith("vortex_signal_yes:") or action.startswith("vortex_signal_no:"):
            symbol = action.split(":", 1)[1]
            state = self.bot_service.get_bot_state(symbol)
            if not state:
                await query.answer("No state found", show_alert=True)
                return
            if state.profile != "vortex":
                await query.answer("Only Vortex supports confirmations", show_alert=True)
                return
            if not state.awaiting_user_confirmation:
                await query.edit_message_text("⌛ Señal expirada", parse_mode=ParseMode.HTML)
                return

            if action.startswith("vortex_signal_yes:"):
                state.user_confirmed_buy = True
                state.waiting_for_confirmation = False
                state.waiting_for_signal = False
                state.vortex_signal_ignored = False
                state.last_action = "USER_CONFIRMED_BUY"
                await query.edit_message_text(
                    "🟢 <b>VORTEX CONFIRMADO</b>\nEjecutando compra…",
                    parse_mode=ParseMode.HTML,
                )
            else:
                state.awaiting_user_confirmation = False
                state.user_confirmed_buy = False
                state.waiting_for_confirmation = False
                state.waiting_for_signal = True
                state.vortex_signal_ignored = True
                state.last_action = "USER_REJECTED"
                await query.edit_message_text(
                    "❌ <b>VORTEX CANCELADO</b>",
                    parse_mode=ParseMode.HTML,
                )
            return

        if action == "main_menu":
            text, keyboard = self._main_menu_payload()
            await self._render(query=query, text=text, keyboard=keyboard)
            return

        if action == "reports_menu":
            await self._show_reports_menu(query=query)
            return

        if action == "help":
            text = (
                "🤖 <b>HERMES — Trading Bot Assistant</b>\n\n"
                "Welcome! 👋\n\n"
                "<b>How to start (recommended)</b>\n"
                "1) Type /start\n"
                "2) Press Start new bot\n"
                "3) Choose a risk profile\n"
                "4) Select the crypto pair\n"
                "5) Review the configuration and press Start\n\n"
                "<b>Commands</b>\n"
                "/start → Open main menu\n"
                "/status → See running bots\n"
                "/stop &lt;SYMBOL&gt; → Stop a bot\n"
            )
            keyboard = [[InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")]]
            await self._render(query=query, text=text, keyboard=keyboard)
            return

        if action == "status":
            text = self._build_running_bots_text()
            keyboard = self._build_running_bots_keyboard()
            await self._render(query=query, text=text, keyboard=keyboard)
            return

        if action == "manage_menu":
            states = self.bot_service.get_all_states()
            if not states:
                await self._render(
                    query=query,
                    text="🤷 <b>No bots running</b>",
                    keyboard=[[InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")]],
                )
                return

            text = "🤖 <b>Manage bots</b>\n\nSelect a bot to manage:"
            keyboard = [
                [
                    InlineKeyboardButton(
                        f"🟢 {state.profile} | {state.symbol}",
                        callback_data=f"manage_bot:{state.bot_id}",
                    )
                ]
                for state in states
            ]
            keyboard.append([InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")])
            await self._render(query=query, text=text, keyboard=keyboard)
            return

        if action.startswith("manage_bot:"):
            bot_id = action.split(":", 1)[1]
            state = self.bot_service.get_bot_state_by_id(bot_id)
            if not state:
                await query.answer("No state found", show_alert=True)
                return

            text = (
                "🤖 <b>Managing bot</b>\n\n"
                f"<b>Bot:</b> <code>{escape_html(bot_id)}</code>\n"
                f"<b>Profile:</b> <code>{escape_html(state.profile)}</code>\n"
                f"<b>Symbol:</b> <code>{escape_html(state.symbol)}</code>"
            )
            keyboard = [
                [InlineKeyboardButton("📊 View status", callback_data=f"dash_open:{state.symbol}")],
                [InlineKeyboardButton("✏️ Edit config", callback_data=f"manage_edit:{bot_id}")],
                [InlineKeyboardButton("🔁 Restart bot", callback_data=f"manage_restart:{bot_id}")],
                [InlineKeyboardButton("🛑 Stop bot", callback_data=f"stop_confirm:{state.symbol}")],
                [InlineKeyboardButton("⬅️ Back", callback_data="manage_menu")],
            ]
            await self._render(query=query, text=text, keyboard=keyboard)
            return

        if action.startswith("manage_edit:"):
            bot_id = action.split(":", 1)[1]
            state = self.bot_service.get_bot_state_by_id(bot_id)
            if not state or not state.config:
                await query.answer("No config found", show_alert=True)
                return

            config = (
                BotBuilder()
                .with_symbol(state.symbol, state.base_asset)
                .with_profile(state.profile)
                .with_defaults()
                .build()
            )
            config = replace(
                config,
                bot_id=state.config.bot_id,
                capital_pct=state.config.capital_pct,
                trade_pct=state.config.trade_pct,
                min_trade_usdt=state.config.min_trade_usdt,
                max_buys_per_day=state.config.max_buys_per_day,
                daily_budget_usdt=state.config.daily_budget_usdt,
                disable_max_buys_per_day=state.config.disable_max_buys_per_day,
                disable_daily_budget=state.config.disable_daily_budget,
                sma_fast=state.config.sma_fast,
                sma_slow=state.config.sma_slow,
                trailing_pct=state.config.trailing_pct,
                kline_interval=state.config.kline_interval,
                kline_limit=state.config.kline_limit,
                cooldown_after_sell_seconds=state.config.cooldown_after_sell_seconds,
                trend_exit_enabled=state.config.trend_exit_enabled,
                trend_sma_period=state.config.trend_sma_period,
                max_hold_seconds_without_new_high=state.config.max_hold_seconds_without_new_high,
            )

            pending = {
                "mode": "manage",
                "bot_id": bot_id,
                "profile": state.profile,
                "symbol": state.symbol,
                "base_asset": state.base_asset,
                "config": config,
            }
            self._pending_configs[chat_id] = pending
            await self._show_config(query=query, pending=pending)
            return

        if action.startswith("manage_restart:"):
            bot_id = action.split(":", 1)[1]
            state = self.bot_service.get_bot_state_by_id(bot_id)
            if not state:
                await query.answer("No state found", show_alert=True)
                return

            text = (
                "🔁 <b>Restart bot</b>\n\n"
                f"<b>Bot:</b> <code>{escape_html(bot_id)}</code>\n"
                f"<b>Symbol:</b> <code>{escape_html(state.symbol)}</code>\n\n"
                "This will stop and restart the bot."
            )
            keyboard = [
                [InlineKeyboardButton("✅ Restart", callback_data=f"manage_restart_apply:{bot_id}")],
                [InlineKeyboardButton("⬅️ Back", callback_data=f"manage_bot:{bot_id}")],
            ]
            await self._render(query=query, text=text, keyboard=keyboard)
            return

        if action.startswith("manage_restart_apply:"):
            bot_id = action.split(":", 1)[1]
            pending = self._pending_configs.get(chat_id)
            config = pending["config"] if pending and pending.get("bot_id") == bot_id else None
            if config is None:
                state = self.bot_service.get_bot_state_by_id(bot_id)
                config = state.config if state else None
            if config is None:
                await query.answer("No config found", show_alert=True)
                return

            await self._send_temp_message(
                context=context,
                chat_id=chat_id,
                text="⏳ <b>Please wait…</b>\nRestarting bot.",
                seconds=self._profile_ttl(config.profile, default=4),
            )
            try:
                self.bot_service.restart_bot_with_config(bot_id, config)
            except Exception as e:
                await query.answer(f"❌ {str(e)}", show_alert=True)
                return

            state = self.bot_service.get_bot_state_by_id(bot_id)
            if state:
                notifier = self.bot_service.get_notifier(state.symbol)
                await notifier.render_bot_dashboard(state, force=True)

            text, keyboard = self._main_menu_payload()
            await self._safe_edit_menu(
                chat_id=chat_id,
                context=context,
                text=text,
                keyboard=keyboard,
            )
            return

        if action.startswith("manage_apply:"):
            bot_id = action.split(":", 1)[1]
            pending = self._pending_configs.get(chat_id)
            if not pending or pending.get("bot_id") != bot_id:
                await query.answer("No pending config", show_alert=True)
                return

            await self._send_temp_message(
                context=context,
                chat_id=chat_id,
                text="⏳ <b>Please wait…</b>\nApplying changes.",
                seconds=self._profile_ttl(pending.get("profile"), default=4),
            )
            try:
                self.bot_service.restart_bot_with_config(bot_id, pending["config"])
            except Exception as e:
                await query.answer(f"❌ {str(e)}", show_alert=True)
                return

            state = self.bot_service.get_bot_state_by_id(bot_id)
            notifier = self.bot_service.get_notifier(state.symbol) if state else None
            if notifier and state:
                await notifier.render_bot_dashboard(state, force=True)

            self._pending_configs.pop(chat_id, None)
            text, keyboard = self._main_menu_payload()
            await self._safe_edit_menu(
                chat_id=chat_id,
                context=context,
                text=text,
                keyboard=keyboard,
            )
            return

        if action == "stop_menu":
            bots = self.bot_service.list_bots()
            if not bots:
                await self._render(
                    query=query,
                    text="🤷 No running bots.",
                    keyboard=[[InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")]],
                )
                return

            keyboard = [[InlineKeyboardButton(f"🛑 Stop {b}", callback_data=f"stop_confirm:{b}")] for b in bots]
            keyboard.append([InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")])
            await self._render(query=query, text="🛑 Select a bot to stop:", keyboard=keyboard)
            return

        if action.startswith("stop_confirm:"):
            symbol = action.split(":", 1)[1]
            text = (
                "⚠️ <b>Confirm stop bot</b>\n\n"
                f"<b>Symbol:</b> <code>{escape_html(symbol)}</code>\n\n"
                "This action will stop the bot immediately."
            )
            keyboard = [
                [InlineKeyboardButton("🛑 Yes, stop", callback_data=f"stop_execute:{symbol}")],
                [InlineKeyboardButton("🔥 Stop & Sell", callback_data=f"stop_sell_execute:{symbol}")],
                [InlineKeyboardButton("❌ Cancel", callback_data="main_menu")],
            ]
            await self._render(query=query, text=text, keyboard=keyboard)
            return

        if action.startswith("stop_execute:"):
            symbol = action.split(":", 1)[1]
            state = self.bot_service.get_bot_state(symbol)
            wait_seconds = self._profile_ttl(state.profile if state else None, default=3)

            try:
                await query.message.delete()
            except Exception:
                pass

            await self._send_temp_message(
                context=context,
                chat_id=chat_id,
                text=self._stop_wait_text(state),
                seconds=wait_seconds,
            )

            try:
                self.bot_service.stop_bot(symbol)
            except Exception as e:
                await query.answer(f"❌ {str(e)}", show_alert=True)
                return

            await self._send_deletable_message(
                context=context,
                chat_id=chat_id,
                text=(
                    "🔴 <b>Bot stopped successfully</b>\n\n"
                    f"<b>Symbol:</b> <code>{escape_html(symbol)}</code>"
                ),
                delete_after=self._profile_ttl(state.profile if state else None, default=10),
            )

            text, keyboard = self._main_menu_payload()
            await self._safe_edit_menu(
                chat_id=chat_id,
                context=context,
                text=text,
                keyboard=keyboard,
            )
            return

        if action == "start_new_bot":
            await self._start_profile_selector(query=query)
            return

        if action.startswith("profile:"):
            profile = action.split(":", 1)[1]
            self._pending_configs[chat_id] = {"profile": profile, "step": "awaiting_symbol"}

            text = f"✅ Profile selected: <b>{escape_html(profile)}</b>\n\nSelect the crypto to trade:"
            keyboard = [
                [InlineKeyboardButton("🟣 ETH / USDT", callback_data="symbol:ETHUSDT:ETH")],
                [InlineKeyboardButton("🟢 SOL / USDT", callback_data="symbol:SOLUSDT:SOL")],
                [InlineKeyboardButton("🔺 AVAX / USDT", callback_data="symbol:AVAXUSDT:AVAX")],
                [InlineKeyboardButton("🔵 MATIC / USDT", callback_data="symbol:MATICUSDT:MATIC")],
                [InlineKeyboardButton("🟡 LINK / USDT", callback_data="symbol:LINKUSDT:LINK")],
                [InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")],
            ]
            await self._render(query=query, text=text, keyboard=keyboard)
            return

        if action.startswith("symbol:"):
            _, symbol, base_asset = action.split(":")
            pending = self._pending_configs.get(chat_id)
            if not pending:
                await query.answer("No pending config", show_alert=True)
                return

            config = (
                BotBuilder()
                .with_symbol(symbol, base_asset)
                .with_profile(pending["profile"])
                .with_defaults()
                .build()
            )

            pending.update({"symbol": symbol, "base_asset": base_asset, "config": config, "step": "ready"})
            await self._show_config(query=query, pending=pending)
            return

        if action == "edit":
            pending = self._pending_configs.get(chat_id)
            if pending:
                pending.pop("edit_step", None)
                pending.pop("edit_param", None)
            text = "✏️ <b>Edit parameters</b>\n\nSelect the parameter you want to change:"
            keyboard = [
                [InlineKeyboardButton("🆘 Help", callback_data="edit_help")],
                [InlineKeyboardButton("💰 capital_pct", callback_data="edit_param:capital_pct")],
                [InlineKeyboardButton("🧮 trade_pct", callback_data="edit_param:trade_pct")],
                [InlineKeyboardButton("🧱 min_trade_usdt", callback_data="edit_param:min_trade_usdt")],
                [InlineKeyboardButton("🔁 max_buys_per_day", callback_data="edit_param:max_buys_per_day")],
                [InlineKeyboardButton("📊 daily_budget_usdt", callback_data="edit_param:daily_budget_usdt")],
                [InlineKeyboardButton("📉 trailing_pct", callback_data="edit_param:trailing_pct")],
                [InlineKeyboardButton("📈 new_high_epsilon_pct", callback_data="edit_param:new_high_epsilon_pct")],
                [InlineKeyboardButton("🚫 Disable limits", callback_data="disable_limits_menu")],
                [InlineKeyboardButton("🔄 symbol / base_asset", callback_data="edit_param:symbol")],
                [InlineKeyboardButton("⬅️ Back", callback_data="edit_back_config")],
            ]
            await self._render(query=query, text=text, keyboard=keyboard)
            return

        if action == "edit_help":
            await self._render(
                query=query,
                text=EDIT_HELP_TEXT,
                keyboard=[[InlineKeyboardButton("⬅️ Back", callback_data="edit")]],
            )
            return

        if action == "disable_limits_menu":
            pending = self._pending_configs.get(chat_id)
            if not pending:
                await self._render(
                    query=query,
                    text="❌ No pending bot configuration.",
                    keyboard=[[InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")]],
                )
                return

            config = pending["config"]
            text = "🚫 <b>Disable limits</b>\n\nSelect the limits you want to disable:"
            keyboard = [
                [
                    InlineKeyboardButton(
                        f"{'✅' if config.disable_max_buys_per_day else '☑️'} Max trades/day",
                        callback_data="toggle_disable_max_buys",
                    )
                ],
                [
                    InlineKeyboardButton(
                        f"{'✅' if config.disable_daily_budget else '☑️'} Daily budget",
                        callback_data="toggle_disable_daily_budget",
                    )
                ],
                [InlineKeyboardButton("⬅️ Back", callback_data="edit")],
            ]
            await self._render(query=query, text=text, keyboard=keyboard)
            return

        if action in {"toggle_disable_max_buys", "toggle_disable_daily_budget"}:
            pending = self._pending_configs.get(chat_id)
            if not pending:
                await self._render(
                    query=query,
                    text="❌ No pending bot configuration.",
                    keyboard=[[InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")]],
                )
                return

            config = pending["config"]
            if action == "toggle_disable_max_buys":
                config = replace(
                    config,
                    disable_max_buys_per_day=not config.disable_max_buys_per_day,
                )
            else:
                config = replace(
                    config,
                    disable_daily_budget=not config.disable_daily_budget,
                )

            pending["config"] = config
            text = "🚫 <b>Disable limits</b>\n\nSelect the limits you want to disable:"
            keyboard = [
                [
                    InlineKeyboardButton(
                        f"{'✅' if config.disable_max_buys_per_day else '☑️'} Max trades/day",
                        callback_data="toggle_disable_max_buys",
                    )
                ],
                [
                    InlineKeyboardButton(
                        f"{'✅' if config.disable_daily_budget else '☑️'} Daily budget",
                        callback_data="toggle_disable_daily_budget",
                    )
                ],
                [InlineKeyboardButton("⬅️ Back", callback_data="edit")],
            ]
            await self._render(query=query, text=text, keyboard=keyboard)
            return

        if action.startswith("edit_param:"):
            param = action.split(":", 1)[1]
            pending = self._pending_configs.get(chat_id)

            if not pending:
                await self._render(
                    query=query,
                    text="❌ No pending bot configuration.",
                    keyboard=[[InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")]],
                )
                return

            pending["edit_param"] = param
            pending["edit_step"] = "awaiting_value"

            if param == "symbol":
                pending["edit_step"] = "awaiting_symbol"
                await self._render(
                    query=query,
                    text=(
                        "✏️ <b>Edit SYMBOL / BASE_ASSET</b>\n\n"
                        "Send both values in one message:\n"
                        "<code>&lt;SYMBOL&gt; &lt;BASE_ASSET&gt;</code>\n\n"
                        "Example:\n"
                        "<code>SOLUSDT SOL</code>"
                    ),
                    keyboard=[[InlineKeyboardButton("⬅️ Back", callback_data="edit")]],
                )
                return

            current_value = getattr(pending["config"], param)
            await self._render(
                query=query,
                text=(
                    f"✏️ Editing parameter: <b>{escape_html(param)}</b>\n\n"
                    f"Current value: <code>{escape_html(str(current_value))}</code>\n\n"
                    "Send the new value:"
                ),
                keyboard=[[InlineKeyboardButton("⬅️ Back", callback_data="edit")]],
            )
            return

        if action == "edit_back_config":
            pending = self._pending_configs.get(chat_id)
            if pending:
                await self._show_config(query=query, pending=pending)
                return

            text, keyboard = self._main_menu_payload()
            await self._render(query=query, text=text, keyboard=keyboard)
            return

        if action == "confirm":
            pending = self._pending_configs.pop(chat_id, None)
            if not pending:
                await query.answer("No pending configuration", show_alert=True)
                return

            await self._send_temp_message(
                context=context,
                chat_id=chat_id,
                text="⏳ <b>Please wait…</b>\nStarting bot.",
                seconds=self._profile_ttl(pending.get("profile"), default=3),
            )

            try:
                self.bot_service.start_bot_from_config(pending["config"])
            except Exception as e:
                await query.answer(f"❌ {str(e)}", show_alert=True)
                return

            state = self.bot_service.get_bot_state(pending["symbol"])
            notifier = self.bot_service.get_notifier(pending["symbol"])
            if state:
                await notifier.render_bot_dashboard(state)

            text, keyboard = self._main_menu_payload()

            menu_id = self._menu_message_id.get(chat_id)

            if menu_id:
                await self._safe_edit_menu(
                    chat_id=chat_id,
                    context=context,
                    text=text,
                    keyboard=keyboard,
                )
            else:
                msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                self._menu_message_id[chat_id] = msg.message_id

            await self._send_deletable_message(
                context=context,
                chat_id=chat_id,
                text=(
                    "✅ <b>Bot started successfully</b>\n\n"
                    f"<b>Symbol:</b> <code>{escape_html(pending['symbol'])}</code>"
                ),
                delete_after=self._profile_ttl(pending.get("profile"), default=10),
            )

            await query.answer("✅ Bot started!")
            return

        if action.startswith("stop_sell_execute:"):
            symbol = action.split(":", 1)[1]
            state = self.bot_service.get_bot_state(symbol)
            wait_seconds = self._profile_ttl(state.profile if state else None, default=3)

            try:
                await query.message.delete()
            except Exception:
                pass

            await self._send_temp_message(
                context=context,
                chat_id=chat_id,
                text="⏳ <b>Please wait…</b>\nStopping and selling.",
                seconds=wait_seconds,
            )

            order = None
            if state and state.trading_mode == TradingMode.LIVE:
                try:
                    order = self.bot_service.binance.safe_sell_all(symbol)
                except Exception as e:
                    logger.warning("Stop & Sell failed: %s", e)

            try:
                self.bot_service.stop_bot(symbol)
            except Exception as e:
                await query.answer(f"❌ {str(e)}", show_alert=True)
                return

            if order and state:
                received = float(order.get("cummulativeQuoteQty", 0.0))
                spent = state.open_position_spent or 0.0
                pnl = received - spent if spent > 0 else 0.0
                text = (
                    "🔥 <b>POSITION CLOSED</b>\n"
                    "Reason: Manual stop\n"
                    f"Received: {received:.2f} USDT\n"
                    f"PnL: {pnl:+.2f} USDT"
                )
            else:
                text = (
                    "🛑 <b>Bot stopped</b>\n"
                    "No position to sell."
                )

            await self._send_deletable_message(
                context=context,
                chat_id=chat_id,
                text=text,
                delete_after=self._profile_ttl(state.profile if state else None, default=10),
            )

            text, keyboard = self._main_menu_payload()
            await self._safe_edit_menu(
                chat_id=chat_id,
                context=context,
                text=text,
                keyboard=keyboard,
            )
            return

        if action == "cancel":
            self._pending_configs.pop(chat_id, None)
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ <b>Bot start cancelled.</b>",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🗑️ Delete", callback_data="delete_self")]]
                ),
            )
            await query.answer("Cancelled")
            return

        if action.startswith("report_menu:"):
            symbol = action.split(":", 1)[1]
            state = self.bot_service.get_bot_state(symbol)
            if not state:
                await query.answer("No state found", show_alert=True)
                return

            await query.answer("Generating report...")
            file_path = write_bot_report(state)
            notifier = self.bot_service.get_any_notifier()
            await notifier.send_file(file_path, caption=f"📊 Report for {symbol}")

            # After file -> send main menu as a new message to avoid edit conflicts
            await self._send_main_menu(chat_id=chat_id, context=context)

            return

        if action == "report_global":
            path = self.bot_service.generate_global_report_csv()
            if not path:
                await query.answer("No bots running", show_alert=True)
                return

            await query.answer("Generating report...")
            notifier = self.bot_service.get_any_notifier()
            await notifier.send_file(path, caption="📈 GLOBAL SERVER REPORT")
            await self._send_main_menu(chat_id=chat_id, context=context)

            return

        if action == "report_general":
            path = self.bot_service.generate_general_report_csv()
            if not path:
                await self._send_deletable_message(
                    context=context,
                    chat_id=chat_id,
                    text="🤷 No bots running. General report not available.",
                    delete_after=6,
                )
                return

            await query.answer("Generating report...")
            notifier = self.bot_service.get_any_notifier()
            await notifier.send_file(path, caption="🌍 General report (server-wide)")
            await self._send_main_menu(chat_id=chat_id, context=context)

            return

        if action == "report_trades":
            path = self.bot_service.get_trade_report_csv()
            if not path:
                await self._send_deletable_message(
                    context=context,
                    chat_id=chat_id,
                    text="🤷 No trades recorded yet.",
                    delete_after=6,
                )
                return

            await query.answer("Generating report...")
            notifier = self.bot_service.get_any_notifier()
            await notifier.send_file(path, caption="🧾 Trade Report (all trades)")
            await self._send_main_menu(chat_id=chat_id, context=context)

            return

        if action.startswith("dash_refresh:"):
            symbol = action.split(":", 1)[1]
            state = self.bot_service.get_bot_state(symbol)
            if not state:
                await query.answer("No state found", show_alert=True)
                return

            notifier = self.bot_service.get_notifier(symbol)
            await notifier.render_bot_dashboard(state, force=True)
            await query.answer("🔄 Refreshed")
            return

        if action.startswith("dash_open:"):
            symbol = action.split(":", 1)[1]
            state = self.bot_service.get_bot_state(symbol)
            if not state:
                await query.answer("No state found", show_alert=True)
                return

            notifier = self.bot_service.get_notifier(symbol)
            await notifier.render_bot_dashboard(state, force=True)
            await query.answer("📊 Dashboard opened")
            return

        if action.startswith("vortex_live_prompt:"):
            symbol = action.split(":", 1)[1]
            state = self.bot_service.get_bot_state(symbol)
            if not state:
                await query.answer("No state found", show_alert=True)
                return
            if state.profile != "vortex":
                await query.answer("Only Vortex supports simulation mode", show_alert=True)
                return
            await self._send_vortex_confirmation(
                context=context,
                chat_id=chat_id,
                state=state,
            )
            await query.answer("🧠 Vortex confirmation sent")
            return

        if action.startswith("vortex_live_yes:"):
            symbol = action.split(":", 1)[1]
            state = self.bot_service.get_bot_state(symbol)
            if not state:
                await query.answer("No state found", show_alert=True)
                return
            try:
                await query.message.delete()
            except Exception:
                pass

            state.trading_mode = TradingMode.LIVE
            state.real_capital_enabled = True
            state.live_disabled_notified = False
            state.armed_notified = True
            state.live_authorized = True
            state.live_authorized_at = time.time()
            state.awaiting_fresh_entry = True
            self.bot_service.enable_live(symbol)

            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "💰 <b>REAL TRADING ENABLED</b>\n\n"
                    f"Initial capital: {state.real_capital_limit:.2f} USDT\n"
                    "Mode: LIVE (LIMITED)\n"
                    "Waiting for fresh entry signal…\n\n"
                    "Monitoring closely…"
                ),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            await query.answer("💰 Live enabled")
            return

        if action.startswith("vortex_live_no:"):
            symbol = action.split(":", 1)[1]
            state = self.bot_service.get_bot_state(symbol)
            if not state:
                await query.answer("No state found", show_alert=True)
                return
            try:
                await query.message.delete()
            except Exception:
                pass

            state.trading_mode = TradingMode.SIMULATION
            state.real_capital_enabled = False
            state.armed_notified = False
            state.live_disabled_notified = False
            state.live_authorized = False
            state.live_authorized_at = None
            state.awaiting_fresh_entry = False
            self.bot_service.disable_live(symbol)

            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    "🧪 <b>Simulation continued.</b>\n\n"
                    "Vortex will keep monitoring the market and notify again "
                    "if conditions improve."
                ),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            await query.answer("🧪 Simulation continued")
            return

        if action.startswith("dash_help:"):
            help_text = (
                "ℹ️ <b>BOT DASHBOARD — Help</b>\n\n"
                "<b>MARKET</b>\n"
                "• <b>Price now</b>: Último precio conocido del mercado.\n"
                "• <b>SMA fast</b>: Media móvil rápida (reacciona más rápido).\n"
                "• <b>SMA slow</b>: Media móvil lenta (define la tendencia).\n"
                "• <b>Trend</b>: Dirección actual del mercado según SMAs.\n"
                "• <b>SMA diff</b>: Diferencia porcentual entre SMA fast y SMA slow.\n\n"
                "<b>ENTRY LOGIC</b>\n"
                "• <b>Entry price</b>: Precio estimado donde el bot considera entrar.\n\n"
                "<b>RISK</b>\n"
                "• <b>Trailing stop</b>: Porcentaje de protección contra caídas.\n"
                "• <b>Arm price</b>: Precio desde el cual el trailing stop se activa.\n"
                "• <b>Stop price</b>: Precio actual de salida si el mercado cae.\n\n"
                "<b>STATS</b>\n"
                "• <b>Buys today</b>: Compras realizadas hoy.\n"
                "• <b>Spent today</b>: Capital usado hoy.\n"
                "• <b>Total PnL</b>: Ganancia o pérdida acumulada.\n\n"
                "<b>Last action</b>\n"
                "Estado actual interno del bot (esperando señal, armado, en trade, etc.).\n\n"
                "📌 <i>Tip:</i> El dashboard se actualiza automáticamente solo cuando hay cambios "
                "relevantes para evitar bloqueos de Telegram."
            )

            await context.bot.send_message(
                chat_id=chat_id,
                text=help_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🗑️ Delete", callback_data="delete_self")]]
                ),
            )
            await query.answer("ℹ️ Dashboard help")
            return

        if action == "delete_self":
            try:
                await query.message.delete()
            except Exception:
                return
            return

        await query.answer("Unknown action", show_alert=True)

    # =========================
    # Text handler
    # =========================
    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        text = update.message.text.strip()
        pending = self._pending_configs.get(chat_id)

        if not pending:
            return

        # Necesitamos un query real para re-render
        query = self._last_query.get(chat_id)
        if not query:
            await update.message.reply_text("✋ Please use the buttons to continue.")
            return

        # =========================
        # Editing SYMBOL / BASE_ASSET
        # =========================
        if pending.get("edit_step") == "awaiting_symbol":
            parts = text.split()
            if len(parts) != 2:
                await update.message.reply_text(
                    "❌ Invalid format.\n\n"
                    "Use:\n<code>&lt;SYMBOL&gt; &lt;BASE_ASSET&gt;</code>\n"
                    "Example:\n<code>SOLUSDT SOL</code>",
                    parse_mode=ParseMode.HTML,
                )
                return

            symbol, base_asset = parts

            config = (
                BotBuilder()
                .with_symbol(symbol, base_asset)
                .with_profile(pending["profile"])
                .with_defaults()
                .build()
            )

            pending.update(
                {
                    "symbol": symbol.upper(),
                    "base_asset": base_asset.upper(),
                    "config": config,
                }
            )
            pending.pop("edit_step", None)
            pending.pop("edit_param", None)

            await self._show_config(query=query, pending=pending)
            return

        # =========================
        # Editing numeric / float params
        # =========================
        if pending.get("edit_step") == "awaiting_value":
            param = pending["edit_param"]
            try:
                current_value = getattr(pending["config"], param)

                if isinstance(current_value, bool):
                    normalized = text.strip().lower()
                    if normalized in {"true", "yes", "1", "on"}:
                        new_value = True
                    elif normalized in {"false", "no", "0", "off"}:
                        new_value = False
                    else:
                        raise ValueError("Invalid boolean")
                elif isinstance(current_value, int):
                    new_value = int(text)
                elif isinstance(current_value, float):
                    new_value = float(text)
                else:
                    new_value = text

                pending["config"] = replace(pending["config"], **{param: new_value})
                pending.pop("edit_step", None)
                pending.pop("edit_param", None)

                await self._show_config(query=query, pending=pending)
                return

            except ValueError:
                await update.message.reply_text(
                    f"❌ Invalid value for <b>{escape_html(param)}</b>. Try again:",
                    parse_mode=ParseMode.HTML,
                )
                return


    async def _auto_refresh_dashboards(self, context):
        for state in self.bot_service.get_all_states():
            if not state.running:
                continue

            # 🔒 NO refrescar si el bot aún no tiene dashboard
            if state.telegram_message_id is None:
                continue

            notifier = self.bot_service.get_notifier(state.symbol)
            try:
                await notifier.render_bot_dashboard(state)
            except Exception as e:
                logger.warning("Dashboard refresh skipped: %s", e)
                continue

    async def _send_daily_summary(self, context):
        states = self.bot_service.get_all_states()
        if not states:
            return

        lines = [
            "📊 <b>Daily Summary</b>",
            "",
        ]
        for state in states:
            lines.append(
                f"• <b>{state.symbol}</b> ({state.profile}) | "
                f"Trades: {state.trades_count} | "
                f"PnL: {state.total_pnl_usdt:+.2f} USDT | "
                f"Buys today: {state.buys_today} | "
                f"Spent: {state.spent_today:.2f} USDT"
            )

        text = "\n".join(lines)
        await context.bot.send_message(
            chat_id=self.bot_service.get_any_notifier().chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

        if (
            state.trading_mode == TradingMode.ARMED
            and not state.armed_notified
            and state.trades_count >= self._VORTEX_MIN_TRADES
        ):
            await self._send_vortex_confirmation(
                context=context,
                chat_id=self.bot_service.notifier.chat_id,
                state=state,
            )
            state.armed_notified = True

        if (
            state.trading_mode == TradingMode.LIVE
            and state.real_capital_enabled
            and state.real_drawdown_pct >= self._LIVE_DRAWDOWN_LIMIT
            and not state.live_disabled_notified
        ):
            state.trading_mode = TradingMode.SIMULATION
            state.real_capital_enabled = False
            state.armed_notified = False
            state.live_disabled_notified = True
            self.bot_service.disable_live(state.symbol)
            await context.bot.send_message(
                chat_id=self.bot_service.notifier.chat_id,
                    text=(
                        "🛑 <b>REAL TRADING DISABLED</b>\n\n"
                        f"Reason: drawdown exceeded {self._LIVE_DRAWDOWN_LIMIT * 100:.0f} %\n\n"
                        "Vortex returned to SIMULATION mode."
                    ),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )


    # =========================
    # Commands (kept for power users)
    # =========================
    async def confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        pending = self._pending_configs.pop(chat_id, None)

        if not pending:
            await update.message.reply_text("❌ No pending bot.")
            return

        try:
            self.bot_service.start_bot_from_config(pending["config"])
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {escape_html(str(e))}")
            return

        await update.message.reply_text("✅ Bot started successfully.")

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self._pending_configs.pop(update.effective_chat.id, None)
        await update.message.reply_text(
            "❌ <b>Bot start cancelled.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🗑️ Delete", callback_data="delete_self")]]
            ),
        )

    async def stop_bot(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /stop <SYMBOL>")
            return

        symbol = context.args[0]
        try:
            self.bot_service.stop_bot(symbol)
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {escape_html(str(e))}")
            return

        await update.message.reply_text(f"🛑 Bot stopped\nSymbol: {escape_html(symbol)}")

    async def restart_bot(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if len(context.args) != 3:
            await update.message.reply_text("Usage: /restart <profile> <SYMBOL> <BASE_ASSET>")
            return

        profile, symbol, base_asset = context.args
        try:
            self.bot_service.restart_bot(symbol, base_asset, profile)
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {escape_html(str(e))}")
            return

        await update.message.reply_text(
            f"♻️ Bot restarted\nProfile: {escape_html(profile)}\nSymbol: {escape_html(symbol)}"
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = self._build_running_bots_text()
        keyboard = self._build_running_bots_keyboard()
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (
            "🤖 <b>HERMES — Trading Bot Assistant</b>\n\n"
            "Use /start to open the menu.\n"
            "Use buttons for guided setup.\n"
        )
        keyboard = [[InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")]]

        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )
