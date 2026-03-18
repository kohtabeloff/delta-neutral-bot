import httpx
import logging
from .base import BaseScanner, FundingRate

logger = logging.getLogger(__name__)


class VariationalScanner(BaseScanner):
    """Variational (peer-to-peer perp DEX) — публичный API, без авторизации."""

    URL = "https://omni-client-api.prod.ap-northeast-1.variational.io/metadata/stats"

    async def get_funding_rates(self) -> list[FundingRate]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(self.URL)
                data = resp.json()
        except Exception as e:
            logger.error(f"Variational: ошибка запроса: {e}")
            return []

        listings = data.get("listings", [])

        rates = []
        for item in listings:
            symbol = item.get("ticker") or item.get("symbol") or ""
            if not symbol:
                continue

            try:
                # funding_rate — это УЖЕ годовая ставка в виде доли
                # (подтверждено: сайт показывает Annual Funding Rate = funding_rate * 100%)
                # funding_interval_s — как часто выплачивается (не влияет на APR)
                annual_rate = float(item.get("funding_rate", 0) or 0)
                apr = annual_rate * 100                        # например -0.669 → -66.9% APR
                hourly_rate = annual_rate / (24 * 365)         # для единообразия с другими

                rates.append(FundingRate(
                    exchange="Variational",
                    symbol=symbol.upper(),
                    rate=hourly_rate,
                    interval_hours=1,
                    apr=apr,
                    open_interest_usd=0,
                ))
            except Exception as e:
                logger.debug(f"Variational: ошибка парсинга {symbol}: {e}")

        logger.info(f"Variational: получено {len(rates)} рынков")
        return rates
