import httpx
from .base import BaseScanner, FundingRate

HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"

# Комиссия за открытие+закрытие позиции (maker ~0.02%, taker ~0.05%)
# Берём taker с запасом
TAKER_FEE = 0.0005  # 0.05% за одну сторону


class HyperliquidScanner(BaseScanner):
    """
    Получает funding rates со всех рынков Hyperliquid.
    Не требует API-ключей — публичные данные.
    """

    async def get_funding_rates(self) -> list[FundingRate]:
        payload = {"type": "metaAndAssetCtxs"}

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(HYPERLIQUID_API, json=payload)
            response.raise_for_status()
            data = response.json()

        # data[0] — мета (список монет), data[1] — контексты (ставки и т.д.)
        assets = data[0]["universe"]
        contexts = data[1]

        results = []
        for asset, ctx in zip(assets, contexts):
            symbol = asset["name"]
            funding_str = ctx.get("funding")

            if funding_str is None:
                continue

            rate = float(funding_str)   # ставка за 1 час (в долях)
            interval_hours = 1          # на Hyperliquid фандинг каждый час
            apr = rate * 24 * 365 * 100 # переводим в годовой %

            # Open interest в USD = количество контрактов * цена
            mark_price = float(ctx.get("markPx") or 0)
            oi_contracts = float(ctx.get("openInterest") or 0)
            open_interest_usd = oi_contracts * mark_price

            results.append(FundingRate(
                exchange="Hyperliquid",
                symbol=symbol,
                rate=rate,
                interval_hours=interval_hours,
                apr=apr,
                open_interest_usd=open_interest_usd,
            ))

        return results
