from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest
from loguru import logger
import hashlib


def escape_md(text: str) -> str:
    """
    Escapa texto para Telegram MarkdownV2
    """
    return (
        text
        .replace("_", "\\_")
        .replace("*", "\\*")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("~", "\\~")
        .replace("`", "\\`")
        .replace(">", "\\>")
        .replace("#", "\\#")
        .replace("+", "\\+")
        .replace("-", "\\-")
        .replace("=", "\\=")
        .replace("|", "\\|")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace(".", "\\.")
        .replace("!", "\\!")
    )


class TelegramNotifier:
    def __init__(self, bot, chat_id: int):
        self.bot = bot
        self.chat_id = chat_id
        self._last_render_hash = None


    # =========================
    # DASHBOARD
    # =========================
    async def render_bot_dashboard(self, state):
        text = self._build_text(state)
        keyboard = self._build_keyboard(state)

        try:
            if state.telegram_message_id is not None:
                await self.bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=state.telegram_message_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML,
                )
                return

            msg = await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )

            state.telegram_message_id = msg.message_id
            logger.info(
                "📡 Dashboard created | symbol=%s | message_id=%s",
                state.symbol,
                msg.message_id,
            )

        except Exception as e:
            logger.warning("Dashboard update failed: %s", e)



    # =========================
    # TEXT BUILDER
    # =========================
    def _build_text(self, s):
        status = "🟢 RUNNING" if s.running else "🔴 STOPPED"

        trailing = (
            f"✅ ON ({s.trailing_pct*100:.2f}%)"
            if s.trailing_enabled
            else "❌ OFF"
        )

        market = (
            "🛡️ Waiting confirmation"
            if s.waiting_for_confirmation
            else "📈 Waiting signal"
            if s.waiting_for_signal
            else "—"
        )

        return (
            f"<b>🤖 HERMES BOT</b>\n\n"
            f"<b>Symbol:</b> {s.symbol}\n"
            f"<b>Profile:</b> {s.profile}\n"
            f"<b>Status:</b> {status}\n\n"
            f"<b>Last action:</b> {s.last_action}\n"
            f"<b>Market state:</b> {market}\n\n"
            f"<b>Price:</b> {s.last_price}\n"
            f"<b>Entry:</b> {s.entry_price}\n"
            f"<b>Arm price:</b> {s.arm_price}\n\n"
            f"<b>Trailing:</b> {trailing}\n\n"
            f"<b>Buys today:</b> {s.buys_today}\n"
            f"<b>Spent today:</b> {s.spent_today:.2f} USDT\n\n"
            f"<b>PnL:</b> {s.total_pnl_usdt:+.4f} USDT"
        )

    # =========================
    # KEYBOARD
    # =========================
    def _build_keyboard(self, s):
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⛔ Stop bot",
                    callback_data=f"stop_confirm:{s.symbol}"
                )
            ],
            [
                InlineKeyboardButton(
                    "📊 Bot report",
                    callback_data=f"report_menu:{s.symbol}"
                ),
                InlineKeyboardButton(
                    "📈 General report",
                    callback_data="report_global"
                ),
            ],
        ])

    # =========================
    # FILES
    # =========================
    async def send_file(self, file_path: str, caption: str | None = None):
        try:
            with open(file_path, "rb") as f:
                await self.bot.send_document(
                    chat_id=self.chat_id,
                    document=f,
                    caption=escape_md(caption) if caption else None,
                    parse_mode=ParseMode.MARKDOWN_V2 if caption else None,
                )
        except Exception:
            logger.exception("Telegram send_file error")

    # =========================
    # SIMPLE MESSAGE
    # =========================
    async def send(self, text: str):
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=escape_md(text),
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception:
            logger.exception("Telegram send error")
