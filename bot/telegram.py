import logging
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


def get_bot() -> Bot:
    return Bot(token=TELEGRAM_TOKEN)


async def send_opportunity(opportunity: dict) -> None:
    """Отправляет сигнал о найденной возможности с кнопками."""
    bot = get_bot()

    symbol = opportunity["symbol"]
    exchange = opportunity["exchange"]
    direction = opportunity["direction"]
    gross_apr = opportunity["gross_apr"]
    net_apr = opportunity["net_apr"]
    rate_per_hour = opportunity["rate_per_hour"]
    description = opportunity["description"]

    direction_emoji = "📈" if direction == "LONG" else "📉"
    oi = opportunity.get("open_interest_usd", 0)
    oi_str = f"${oi/1_000_000:.1f}M" if oi >= 1_000_000 else f"${oi/1_000:.0f}K"

    text = (
        f"🔔 *Найдена возможность*\n\n"
        f"*{symbol}* — {exchange}\n"
        f"{direction_emoji} Направление: *{direction}*\n\n"
        f"💰 Валовый APR: `{gross_apr}%`\n"
        f"✅ Чистый APR (после комиссий): `{net_apr}%`\n"
        f"⏱ Ставка в час: `{rate_per_hour}%`\n"
        f"📊 Open Interest: `{oi_str}`\n\n"
        f"_{description}_"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Открыть позицию", callback_data=f"open:{exchange}:{symbol}:{direction}"),
            InlineKeyboardButton("❌ Пропустить", callback_data="skip"),
        ]
    ])

    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard,
    )
    logger.info(f"Сигнал отправлен: {symbol} на {exchange}, APR={net_apr}%")


async def send_message(text: str, reply_markup=None) -> None:
    """Простое уведомление, опционально с кнопками."""
    bot = get_bot()
    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup,
    )
