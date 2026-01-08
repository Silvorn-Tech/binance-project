import hashlib
import os
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest, TimedOut, NetworkError
from loguru import logger

from hermes.utils.trading_mode import TradingMode

QUIET_ACTIONS = {"WAIT_SIGNAL", "ARM_INIT", "WAIT_CONFIRMATION", "WAITING_CONFIRMATION"}
MIN_EDIT_INTERVAL_SECONDS = 30


class TelegramNotifier:
    def __init__(self, bot, chat_id: int):
        self.bot = bot
        self.chat_id = chat_id
        self._editing = set()

    # =========================
    # Dashboard renderer
    # =========================
    async def render_bot_dashboard(self, state, force: bool = False):
        if os.getenv("TELEGRAM_DEV_MODE") == "true":
            return

        text = self._build_text(state)
        keyboard = self._build_keyboard(state)

        payload = text + repr(keyboard)
        payload_hash = hashlib.sha256(payload.encode()).hexdigest()
        now = time.time()

        # First time → create message
        if state.telegram_message_id is None:
            msg = await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            state.telegram_message_id = msg.message_id
            state.last_dashboard_hash = payload_hash
            state.last_dashboard_update = now
            logger.info("📊 Dashboard created | symbol=%s", state.symbol)
            return

        if not force:
            if state.last_action in QUIET_ACTIONS:
                return

            if (
                state.last_dashboard_hash == payload_hash
                and now - state.last_dashboard_update < MIN_EDIT_INTERVAL_SECONDS
            ):
                return

        msg_id = state.telegram_message_id
        if msg_id in self._editing:
            return

        self._editing.add(msg_id)
        try:
            # Update existing dashboard
            try:
                await self.bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=msg_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except BadRequest as e:
                if "Message is not modified" in str(e):
                    return
                raise
            except (TimedOut, NetworkError):
                return
            state.last_dashboard_hash = payload_hash
            state.last_dashboard_update = now
        finally:
            self._editing.discard(msg_id)

    # =========================
    # Dashboard text
    # =========================
    def _build_text(self, state) -> str:
        def fmt(value, decimals=2):
            if value is None:
                return "—"
            return f"{value:.{decimals}f}"

        def pct(value, decimals=2):
            if value is None:
                return "—"
            return f"{value * 100:.{decimals}f}"

        if state.trading_mode == TradingMode.LIVE and state.awaiting_fresh_entry:
            mode_label = "💰 LIVE (WAITING ENTRY)"
        else:
            mode_label = {
                TradingMode.SIMULATION: "🧪 SIMULATION",
                TradingMode.ARMED: "🟡 ARMED",
                TradingMode.LIVE: "💰 LIVE",
            }.get(state.trading_mode, str(state.trading_mode))

        trend = "—"
        if state.sma_fast is not None and state.sma_slow is not None:
            if state.sma_fast > state.sma_slow:
                trend = "📈 BULLISH"
            elif state.sma_fast < state.sma_slow:
                trend = "📉 BEARISH"
            else:
                trend = "➖ NEUTRAL"

        sma_diff = None
        if state.sma_fast is not None and state.sma_slow is not None and state.sma_slow != 0:
            sma_diff = (state.sma_fast - state.sma_slow) / state.sma_slow * 100

        stop_distance = None
        if state.last_price and state.stop_price:
            stop_distance = (state.last_price - state.stop_price) / state.last_price * 100

        lines = [
            "📊 <b>BOT DASHBOARD</b>",
            "",
            f"<b>Symbol:</b> <code>{state.symbol}</code>",
            f"<b>Profile:</b> <code>{state.profile}</code>",
            f"<b>Status:</b> {'🟢 RUNNING' if state.running else '🔴 STOPPED'}",
            "",
            f"<b>Mode:</b> {mode_label}",
            f"<b>Authorized:</b> {'✅' if state.live_authorized else '❌'}",
            f"<b>Waiting fresh entry:</b> {'✅' if state.awaiting_fresh_entry else '❌'}",
            "",
            "────────── <b>MARKET</b> ──────────",
            f"<b>Price now:</b> {fmt(state.last_price)}",
            f"<b>SMA fast:</b> {fmt(state.sma_fast)}",
            f"<b>SMA slow:</b> {fmt(state.sma_slow)}",
            f"<b>Trend:</b> {trend}",
            f"<b>SMA diff:</b> {fmt(sma_diff)} %",
            "",
            "───────── <b>ENTRY LOGIC</b> ───────",
            f"<b>Entry price:</b> {fmt(state.entry_price)}",
            "",
            "────────── <b>RISK</b> ────────────",
            f"<b>Trailing stop:</b> {pct(state.trailing_pct)} %",
            f"<b>Arm price:</b> {fmt(state.arm_price)}",
            f"<b>Stop price:</b> {fmt(state.stop_price)}",
            f"<b>Distance to stop:</b> {fmt(stop_distance)} %",
            "",
        ]

        if state.profile == "vortex":
            lines.extend(
                [
                    "────────── <b>SIMULATION</b> ───────────",
                    f"<b>Score:</b> {fmt(state.vortex_score, 4)}",
                    f"<b>Virtual entry:</b> {fmt(state.virtual_entry_price)}",
                    f"<b>Virtual PnL:</b> {fmt(state.virtual_pnl, 4)} USDT",
                    f"<b>Trades:</b> {state.trades_count}",
                    "",
                ]
            )

        lines.extend(
            [
                "───────── <b>STATS</b> ────────────",
                f"<b>Buys today:</b> {state.buys_today}",
                f"<b>Spent today:</b> {fmt(state.spent_today)} USDT",
                f"<b>Total PnL:</b> {fmt(state.total_pnl_usdt, 4)} USDT",
                "",
                f"<b>Last action:</b> {state.last_action}",
            ]
        )

        return "\n".join(lines)

    # =========================
    # Dashboard keyboard  ✅ FIX
    # =========================
    def _build_keyboard(self, state) -> InlineKeyboardMarkup:
        buttons = [
            [
                InlineKeyboardButton(
                    "🛑 Stop bot",
                    callback_data=f"stop_confirm:{state.symbol}",
                ),
                InlineKeyboardButton(
                    "🔄 Refresh",
                    callback_data=f"dash_refresh:{state.symbol}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "ℹ️ Dashboard help",
                    callback_data=f"dash_help:{state.symbol}",
                )
            ],
        ]

        if state.profile == "vortex" and state.trading_mode == TradingMode.ARMED:
            buttons.append(
                [
                    InlineKeyboardButton(
                        "💰 Enable real trading",
                        callback_data=f"vortex_live_prompt:{state.symbol}",
                    )
                ]
            )

        return InlineKeyboardMarkup(buttons)

    # =========================
    # Send file helper
    # =========================
    async def send_file(self, file_path: str, caption: str = ""):
        with open(file_path, "rb") as f:
            await self.bot.send_document(
                chat_id=self.chat_id,
                document=f,
                caption=caption,
            )
