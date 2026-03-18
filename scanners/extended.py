import httpx
import logging
from .base import BaseScanner, FundingRate

logger = logging.getLogger(__name__)


def _strip_symbol(raw: str) -> str:
    """BTC-USD → BTC, ETH-USDC → ETH"""
    return raw.split("-")[0]


class ExtendedScanner(BaseScanner):
    """Extended Exchange (Starknet) — публичный API, без авторизации."""

    BASE_URL = "https://api.starknet.extended.exchange/api/v1"

    async def get_funding_rates(self) -> list[FundingRate]:
        # Шаг 1: получаем список рынков
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.BASE_URL}/info/markets")
                markets_data = resp.json()
        except Exception as e:
            logger.error(f"Extended: ошибка получения рынков: {e}")
            return []

        items = markets_data if isinstance(markets_data, list) else markets_data.get("data", markets_data.get("markets", []))

        rates = []
        for item in items:
            symbol_raw = item.get("name") or item.get("market") or item.get("symbol") or ""
            if not symbol_raw:
                continue

            try:
                stats = item.get("marketStats") or {}
                funding_rate = float(stats.get("fundingRate") or 0)
                apr = funding_rate * 24 * 365 * 100

                oi = float(stats.get("openInterest") or 0)
                mark_price = float(stats.get("markPrice") or 0)
                oi_usd = oi * mark_price if mark_price else oi
                volume_usd = float(stats.get("dailyVolume") or 0)

                rates.append(FundingRate(
                    exchange="Extended",
                    symbol=_strip_symbol(symbol_raw),
                    rate=funding_rate,
                    interval_hours=1,
                    apr=apr,
                    open_interest_usd=oi_usd,
                    volume_usd=volume_usd,
                    mark_price=mark_price,
                ))
            except Exception as e:
                logger.debug(f"Extended: ошибка парсинга {symbol_raw}: {e}")

        logger.info(f"Extended: получено {len(rates)} рынков")
        return rates
