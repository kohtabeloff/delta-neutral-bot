import httpx
import logging
from .base import BaseScanner, FundingRate

logger = logging.getLogger(__name__)


def _strip_symbol(raw: str) -> str:
    """BTCUSDT → BTC, ETHUSDC → ETH"""
    for suffix in ["USDT", "USDC", "USD", "PERP"]:
        if raw.endswith(suffix):
            return raw[:-len(suffix)]
    return raw


class BybitScanner(BaseScanner):
    """Bybit Futures — публичный API, без авторизации."""

    URL = "https://api.bybit.com/v5/market/tickers"

    async def get_funding_rates(self) -> list[FundingRate]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(self.URL, params={"category": "linear"})
                data = resp.json()
        except Exception as e:
            logger.error(f"Bybit: ошибка запроса: {e}")
            return []

        rates = []
        for item in data.get("result", {}).get("list", []):
            symbol_raw = item.get("symbol", "")
            # Берём только USDT-перпы для простоты
            if not symbol_raw.endswith("USDT"):
                continue

            try:
                # Bybit даёт 8-часовую ставку → переводим в часовую
                rate_8h = float(item.get("fundingRate", 0) or 0)
                hourly_rate = rate_8h / 8
                apr = hourly_rate * 24 * 365 * 100

                oi = float(item.get("openInterest", 0) or 0)
                mark_price = float(item.get("markPrice", 0) or 0)
                oi_usd = oi * mark_price

                rates.append(FundingRate(
                    exchange="Bybit",
                    symbol=_strip_symbol(symbol_raw),
                    rate=hourly_rate,
                    interval_hours=1,
                    apr=apr,
                    open_interest_usd=oi_usd,
                ))
            except Exception as e:
                logger.debug(f"Bybit: ошибка парсинга {symbol_raw}: {e}")

        logger.info(f"Bybit: получено {len(rates)} рынков")
        return rates
