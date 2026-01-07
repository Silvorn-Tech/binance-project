from loguru import logger
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from hermes.service.bot_service import BotService
from hermes.service.bot_builder import BotBuilder
from telegram.error import BadRequest
from dataclasses import replace



class Controller:
    """
    Telegram controller.
    Entry point to application logic via Telegram commands.
    """

    def __init__(self, bot_service: BotService, telegram_token: str):
        self.bot_service = bot_service
        self.telegram_token = telegram_token
        self._pending_configs: dict[int, dict] = {}

    # =========================
    # Bootstrap
    # =========================
    def start(self) -> None:
        app = ApplicationBuilder().token(self.telegram_token).build()

        app.add_handler(CommandHandler("start", self.start_bot))
        app.add_handler(CommandHandler("stop", self.stop_bot))
        app.add_handler(CommandHandler("restart", self.restart_bot))
        app.add_handler(CommandHandler("status", self.status))
        app.add_handler(CommandHandler("help", self.help))
        app.add_handler(CommandHandler("confirm", self.confirm))
        app.add_handler(CommandHandler("cancel", self.cancel))
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text)
        )

        app.add_handler(CallbackQueryHandler(self.on_button))

        logger.info("📡 Telegram controller started")
        app.run_polling()

    # =========================
    # Render helper (CORE RULE)
    # =========================

    async def _render(self, *, message=None, query=None, text: str, keyboard):
        try:
            if query:
                await query.edit_message_text(
                    text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            elif message:
                await message.reply_text(
                    text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                # ⚠️ Expected behavior → ignore silently
                logger.debug("Render skipped: message not modified")
            else:
                raise


    # =========================
    # /start command
    # =========================
    async def start_bot(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        chat_id = update.effective_chat.id
        args = context.args

        if not args:
            text = (
                "🤖 HERMES is online\n\n"
                "Select an option:"
            )

            keyboard = [
                [InlineKeyboardButton("🚀 Start new bot", callback_data="start_new_bot")],
                [InlineKeyboardButton("📊 Running bots", callback_data="status")],
                [InlineKeyboardButton("🛑 Stop a bot", callback_data="stop_menu")],
                [InlineKeyboardButton("ℹ️ Help", callback_data="help")],
            ]

            await self._render(
                message=update.message,
                text=text,
                keyboard=keyboard,
            )
            return

        # Wizard start
        if len(args) == 1 and args[0].lower() == "bot":
            await self._start_profile_selector(message=update.message)
            return

        # Power user
        try:
            if len(args) != 3:
                raise ValueError(
                    "Usage:\n/start bot\nOR\n/start <profile> <SYMBOL> <BASE_ASSET>"
                )

            profile, symbol, base_asset = args

            config = (
                BotBuilder()
                .with_symbol(symbol, base_asset)
                .with_profile(profile)
                .with_defaults()
                .build()
            )

            self._pending_configs[chat_id] = {
                "profile": profile,
                "symbol": symbol,
                "base_asset": base_asset,
                "config": config,
                "step": "ready",
            }

            await self._show_config(
                message=update.message,
                pending=self._pending_configs[chat_id],
            )

        except Exception as e:
            logger.exception(e)
            await update.message.reply_text(f"❌ Error: {e}")

    # =========================
    # Profile selector
    # =========================
    async def _start_profile_selector(self, *, message=None, query=None):
        text = (
            "⚡ HERMES — Select a bot profile\n\n"
            "Choose the risk profile you want to use:"
        )

        keyboard = [
            [InlineKeyboardButton("🛡️ Sentinel (Conservative)", callback_data="profile:sentinel")],
            [InlineKeyboardButton("⚖️ Equilibrium (Balanced)", callback_data="profile:equilibrium")],
            [InlineKeyboardButton("🌪️ Vortex (Aggressive)", callback_data="profile:vortex")],
        ]

        await self._render(
            message=message,
            query=query,
            text=text,
            keyboard=keyboard,
        )

    # =========================
    # Show config
    # =========================
    async def _show_config(self, *, message=None, query=None, pending: dict):
        config = pending["config"]

        text = (
            "⚡ HERMES — Bot Configuration\n\n"
            f"Profile: {pending['profile']}\n"
            f"Symbol: {pending['symbol']}\n\n"
            "🧩 Current configuration:\n"
            f"• buy_usdt: {config.buy_usdt}\n"
            f"• max_buys_per_day: {config.max_buys_per_day}\n"
            f"• daily_budget_usdt: {config.daily_budget_usdt}\n"
            f"• sma_fast: {config.sma_fast}\n"
            f"• sma_slow: {config.sma_slow}\n"
            f"• trailing_pct: {config.trailing_pct}\n\n"
            "What do you want to do?"
        )

        keyboard = [
            [InlineKeyboardButton("✅ Start (default)", callback_data="confirm")],
            [InlineKeyboardButton("✏️ Edit parameters", callback_data="edit")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
        ]

        await self._render(
            message=message,
            query=query,
            text=text,
            keyboard=keyboard,
        )

    # =========================
    # Button callbacks
    # =========================
    async def on_button(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat.id
        action = query.data

        # =========================
        # MAIN MENU
        # =========================
        if action == "main_menu":
            text = "🤖 HERMES is online\n\nSelect an option:"
            keyboard = [
                [InlineKeyboardButton("🚀 Start new bot", callback_data="start_new_bot")],
                [InlineKeyboardButton("📊 Running bots", callback_data="status")],
                [InlineKeyboardButton("🛑 Stop a bot", callback_data="stop_menu")],
                [InlineKeyboardButton("ℹ️ Help", callback_data="help")],
            ]
            await self._render(query=query, text=text, keyboard=keyboard)
            return

        # =========================
        # HELP
        # =========================
        if action == "help":
            text = (
                "🤖 **HERMES — Trading Bot Assistant**\n\n"
                "Welcome! 👋\n"
                "HERMES is an automated trading bot that helps you trade cryptocurrencies "
                "on Binance using predefined strategies and risk profiles.\n\n"

                "━━━━━━━━━━━━━━━━━━━━\n"
                "🚀 **How to start (recommended)**\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "1️⃣ Type:\n"
                "`/start`\n"
                "2️⃣ Press **Start new bot**\n"
                "3️⃣ Choose a **risk profile**:\n"
                "• Sentinel → conservative (low risk)\n"
                "• Equilibrium → balanced\n"
                "• Vortex → aggressive (high risk)\n"
                "4️⃣ Select the crypto pair (example: ETH/USDT)\n"
                "5️⃣ Review the configuration and press **Start**\n\n"

                "👉 That’s it! The bot will start trading automatically.\n\n"

                "━━━━━━━━━━━━━━━━━━━━\n"
                "📊 **What can I do once a bot is running?**\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "• See running bots → **Running bots**\n"
                "• Stop a bot → **Stop a bot**\n"
                "• Start another bot → **Start new bot**\n\n"

                "━━━━━━━━━━━━━━━━━━━━\n"
                "🧠 **Commands (advanced users)**\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "`/start` → Open main menu\n"
                "`/start bot` → Start bot wizard directly\n"
                "`/status` → See running bots\n"
                "`/stop <SYMBOL>` → Stop a bot\n"
                "Example:\n"
                "`/stop ETHUSDT`\n\n"

                "━━━━━━━━━━━━━━━━━━━━\n"
                "⚠️ **Important notes**\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "• The bot trades with REAL money\n"
                "• Make sure your Binance API keys are correct\n"
                "• Conservative profiles reduce risk but also profits\n"
                "• You can stop the bot at any time\n\n"

                "If you are unsure about something:\n"
                "👉 Just use the buttons. They guide you step by step 😊"
            )
            keyboard = [[InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")]]
            await self._render(query=query, text=text, keyboard=keyboard)
            return

        # =========================
        # STATUS
        # =========================
        if action == "status":
            bots = self.bot_service.list_bots()
            text = (
                "🤷 No bots running"
                if not bots
                else "📊 Running bots:\n\n" + "\n".join(f"• {b}" for b in bots)
            )
            keyboard = [
                [InlineKeyboardButton("➕ Start new bot", callback_data="start_new_bot")],
                [InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")],
            ]
            await self._render(query=query, text=text, keyboard=keyboard)
            return

        # =========================
        # STOP MENU
        # =========================
        if action == "stop_menu":
            bots = self.bot_service.list_bots()
            if not bots:
                await self._render(
                    query=query,
                    text="🤷 No running bots.",
                    keyboard=[[InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")]],
                )
                return

            keyboard = [
                [InlineKeyboardButton(f"🛑 Stop {b}", callback_data=f"stop_confirm:{b}")]
                for b in bots
            ]
            keyboard.append([InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")])

            await self._render(
                query=query,
                text="🛑 Select a bot to stop:",
                keyboard=keyboard,
            )
            return

        # =========================
        # STOP CONFIRM
        # =========================
        if action.startswith("stop_confirm:"):
            symbol = action.split(":")[1]
            text = (
                "⚠️ Confirm stop bot\n\n"
                f"Symbol: {symbol}\n\n"
                "This action will stop the bot immediately."
            )
            keyboard = [
                [InlineKeyboardButton("🛑 Yes, stop", callback_data=f"stop_execute:{symbol}")],
                [InlineKeyboardButton("❌ Cancel", callback_data="main_menu")],
            ]
            await self._render(query=query, text=text, keyboard=keyboard)
            return

        if action.startswith("stop_execute:"):
            symbol = action.split(":")[1]
            self.bot_service.stop_bot(symbol)

            text = f"🛑 Bot stopped successfully\n\nSymbol: {symbol}"
            keyboard = [
                [InlineKeyboardButton("➕ Create another bot", callback_data="start_new_bot")],
                [InlineKeyboardButton("📊 View running bots", callback_data="status")],
                [InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")],
            ]
            await self._render(query=query, text=text, keyboard=keyboard)
            return

        # =========================
        # START FLOW
        # =========================
        if action == "start_new_bot":
            await self._start_profile_selector(query=query)
            return

        if action.startswith("profile:"):
            profile = action.split(":")[1]
            self._pending_configs[chat_id] = {
                "profile": profile,
                "step": "awaiting_symbol",
            }

            text = f"✅ Profile selected: {profile}\n\nSelect the crypto to trade:"
            keyboard = [
                [InlineKeyboardButton("🟣 ETH / USDT", callback_data="symbol:ETHUSDT:ETH")],
                [InlineKeyboardButton("🟢 SOL / USDT", callback_data="symbol:SOLUSDT:SOL")],
                [InlineKeyboardButton("🔺 AVAX / USDT", callback_data="symbol:AVAXUSDT:AVAX")],
                [InlineKeyboardButton("🔵 MATIC / USDT", callback_data="symbol:MATICUSDT:MATIC")],
                [InlineKeyboardButton("🟡 LINK / USDT", callback_data="symbol:LINKUSDT:LINK")],
            ]
            await self._render(query=query, text=text, keyboard=keyboard)
            return

        if action.startswith("symbol:"):
            _, symbol, base_asset = action.split(":")
            pending = self._pending_configs.get(chat_id)

            config = (
                BotBuilder()
                .with_symbol(symbol, base_asset)
                .with_profile(pending["profile"])
                .with_defaults()
                .build()
            )

            pending.update({
                "symbol": symbol,
                "base_asset": base_asset,
                "config": config,
                "step": "ready",
            })

            await self._show_config(query=query, pending=pending)
            return

        # =========================
        # EDIT PARAMETERS
        # =========================
        if action == "edit":
            text = "✏️ Edit parameters\n\nSelect the parameter you want to change:"
            keyboard = [
                [InlineKeyboardButton("💰 buy_usdt", callback_data="edit_param:buy_usdt")],
                [InlineKeyboardButton("🔁 max_buys_per_day", callback_data="edit_param:max_buys_per_day")],
                [InlineKeyboardButton("📊 daily_budget_usdt", callback_data="edit_param:daily_budget_usdt")],
                [InlineKeyboardButton("📉 trailing_pct", callback_data="edit_param:trailing_pct")],
                [InlineKeyboardButton("🔄 symbol / base_asset", callback_data="edit_param:symbol")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
            ]
            await self._render(query=query, text=text, keyboard=keyboard)
            return
        
        # =========================
        # EDIT PARAMETER SELECTION
        # =========================
        if action.startswith("edit_param:"):
            param = action.split(":")[1]
            pending = self._pending_configs.get(chat_id)

            if not pending:
                await self._render(
                    query=query,
                    text="❌ No pending bot configuration.",
                    keyboard=[[InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")]],
                )
                return

            # Guardamos qué parámetro se está editando
            pending["edit_param"] = param
            pending["edit_step"] = "awaiting_value"

            # Caso especial: symbol
            if param == "symbol":
                pending["edit_step"] = "awaiting_symbol"

                await self._render(
                    query=query,
                    text=(
                        "✏️ Edit SYMBOL\n\n"
                        "Send the trading pair.\n\n"
                        "Example:\n"
                        "SOLUSDT"
                    ),
                    keyboard=[[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]],
                )
                return

            current_value = getattr(pending["config"], param)

            await self._render(
                query=query,
                text=(
                    f"✏️ Editing parameter: {param}\n\n"
                    f"Current value: {current_value}\n\n"
                    "Send the new value:"
                ),
                keyboard=[[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]],
            )
            return


        # =========================
        # CONFIRM / CANCEL
        # =========================
        if action == "confirm":
            pending = self._pending_configs.pop(chat_id)
            self.bot_service.start_bot_from_config(pending["config"])

            text = (
                "✅ Bot started successfully\n\n"
                f"Symbol: {pending['symbol']}\n"
                f"Profile: {pending['profile']}"
            )
            keyboard = [
                [InlineKeyboardButton("🛑 Stop this bot", callback_data=f"stop_confirm:{pending['symbol']}")],
                [InlineKeyboardButton("➕ Create another bot", callback_data="start_new_bot")],
                [InlineKeyboardButton("📊 View running bots", callback_data="status")],
            ]
            await self._render(query=query, text=text, keyboard=keyboard)
            return

        if action == "cancel":
            self._pending_configs.pop(chat_id, None)
            await self._render(
                query=query,
                text="❌ Bot start cancelled.",
                keyboard=[[InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")]],
            )
            return


    # =========================
    # Text handler
    # =========================
    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        text = update.message.text.strip()
        pending = self._pending_configs.get(chat_id)

        if not pending:
            return

        if pending.get("edit_step") == "awaiting_symbol":
            parts = text.split()

            if len(parts) != 2:
                await update.message.reply_text(
                    "❌ Invalid format.\n\nUse:\n<SYMBOL> <BASE_ASSET>\nExample:\nSOLUSDT SOL"
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

            pending.update({
                "symbol": symbol,
                "base_asset": base_asset,
                "config": config,
            })

            pending.pop("edit_step", None)
            pending.pop("edit_param", None)

            await self._show_config(
                message=update.message,
                pending=pending,
            )
            return

        if pending.get("edit_step") == "awaiting_value":
            param = pending["edit_param"]

            try:
                current_value = getattr(pending["config"], param)

                if isinstance(current_value, int):
                    new_value = int(text)
                elif isinstance(current_value, float):
                    new_value = float(text)
                else:
                    new_value = text

                pending["config"] = replace(
                    pending["config"],
                    **{param: new_value}
                )

                pending.pop("edit_step", None)
                pending.pop("edit_param", None)

                await self._show_config(
                    message=update.message,
                    pending=pending,
                )
                return


            except ValueError:
                await update.message.reply_text(
                    f"❌ Invalid value for {param}. Try again:"
                )



    # =========================
    # Commands (notifications)
    # =========================
    async def confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        pending = self._pending_configs.pop(chat_id, None)

        if not pending:
            await update.message.reply_text("❌ No pending bot.")
            return

        self.bot_service.start_bot_from_config(pending["config"])
        await update.message.reply_text("✅ Bot started successfully.")

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self._pending_configs.pop(update.effective_chat.id, None)
        await update.message.reply_text("❌ Bot start cancelled.")

    async def stop_bot(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /stop <SYMBOL>")
            return

        symbol = context.args[0]
        self.bot_service.stop_bot(symbol)
        await update.message.reply_text(f"🛑 Bot stopped\nSymbol: {symbol}")

    async def restart_bot(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        profile, symbol, base_asset = context.args
        self.bot_service.restart_bot(symbol, base_asset, profile)
        await update.message.reply_text(
            f"♻️ Bot restarted\nProfile: {profile}\nSymbol: {symbol}"
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        bots = self.bot_service.list_bots()
        text = (
            "🤷 No bots running"
            if not bots
            else "📊 Running bots:\n" + "\n".join(f"• {b}" for b in bots)
        )

        keyboard = [
            [InlineKeyboardButton("➕ Start new bot", callback_data="start_new_bot")],
            [InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")],
        ]

        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (
            "🤖 **HERMES — Trading Bot Assistant**\n\n"
            "Welcome! 👋\n"
            "HERMES is an automated trading bot that helps you trade cryptocurrencies "
            "on Binance using predefined strategies and risk profiles.\n\n"

            "━━━━━━━━━━━━━━━━━━━━\n"
            "🚀 **How to start (recommended)**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "1️⃣ Type:\n"
            "`/start`\n"
            "2️⃣ Press **Start new bot**\n"
            "3️⃣ Choose a **risk profile**:\n"
            "• Sentinel → conservative (low risk)\n"
            "• Equilibrium → balanced\n"
            "• Vortex → aggressive (high risk)\n"
            "4️⃣ Select the crypto pair (example: ETH/USDT)\n"
            "5️⃣ Review the configuration and press **Start**\n\n"

            "👉 That’s it! The bot will start trading automatically.\n\n"

            "━━━━━━━━━━━━━━━━━━━━\n"
            "📊 **What can I do once a bot is running?**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "• See running bots → **Running bots**\n"
            "• Stop a bot → **Stop a bot**\n"
            "• Start another bot → **Start new bot**\n\n"

            "━━━━━━━━━━━━━━━━━━━━\n"
            "🧠 **Commands (advanced users)**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "`/start` → Open main menu\n"
            "`/start bot` → Start bot wizard directly\n"
            "`/status` → See running bots\n"
            "`/stop <SYMBOL>` → Stop a bot\n"
            "Example:\n"
            "`/stop ETHUSDT`\n\n"

            "━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ **Important notes**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "• The bot trades with REAL money\n"
            "• Make sure your Binance API keys are correct\n"
            "• Conservative profiles reduce risk but also profits\n"
            "• You can stop the bot at any time\n\n"

            "If you are unsure about something:\n"
            "👉 Just use the buttons. They guide you step by step 😊"
        )

        keyboard = [
            [InlineKeyboardButton("⬅️ Main menu", callback_data="main_menu")]
        ]

        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )

