import logging
from decimal import Decimal

import httpx

logger = logging.getLogger(__name__)

EXTENDED_API_BASE = "https://api.starknet.extended.exchange/api/v1"


class ExtendedExecutor:
    """
    Клиент для торговли на Extended Exchange (StarkNet perp DEX).

    Требует: pip install x10-python-trading-starknet

    Credentials получить на extended.exchange → API Management:
      - api_key, public_key, private_key, vault_id
    """

    def __init__(self, api_key: str, public_key: str, private_key: str, vault_id: int):
        self._api_key = api_key
        self._public_key = public_key
        self._private_key = private_key
        self._vault_id = vault_id
        self._trading_client = None
        self._stark_account = None
        self._endpoint_config = None

    def _init_client(self):
        """Ленивая инициализация SDK клиента."""
        if self._trading_client is not None:
            return
        try:
            from x10.perpetual.accounts import StarkPerpetualAccount
            from x10.perpetual.configuration import MAINNET_CONFIG
            from x10.perpetual.trading_client import PerpetualTradingClient
        except ImportError:
            raise RuntimeError(
                "x10-python-trading-starknet не установлен: pip install x10-python-trading-starknet"
            )
        self._endpoint_config = MAINNET_CONFIG
        self._stark_account = StarkPerpetualAccount(
            api_key=self._api_key,
            public_key=self._public_key,
            private_key=self._private_key,
            vault=self._vault_id,
        )
        self._trading_client = PerpetualTradingClient(MAINNET_CONFIG, self._stark_account)

    @staticmethod
    def _market_name(symbol: str) -> str:
        """BTC → BTC-USD"""
        s = symbol.upper()
        if "-" not in s:
            return f"{s}-USD"
        return s

    async def _get_market(self, symbol: str):
        """Получает объект рынка из SDK."""
        self._init_client()
        market_name = self._market_name(symbol)
        markets = await self._trading_client.markets_info.get_markets_dict()
        market = markets.get(market_name)
        if not market:
            raise ValueError(f"Extended: рынок {market_name} не найден")
        return market

    async def _get_mark_price(self, symbol: str) -> float:
        """Получает mark price через публичный REST API (без авторизации)."""
        market_name = self._market_name(symbol)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{EXTENDED_API_BASE}/info/markets")
            items = resp.json()
        items = items if isinstance(items, list) else items.get("data", [])
        for item in items:
            sym = item.get("market") or item.get("symbol") or item.get("name") or ""
            if sym.upper() == market_name.upper():
                stats = item.get("marketStats") or {}
                price = (
                    item.get("mark_price")
                    or item.get("markPrice")
                    or stats.get("markPrice")
                    or stats.get("mark_price")
                )
                if price:
                    return float(price)
        raise ValueError(f"Extended: mark price для {symbol} не найдена")

    async def market_open(self, symbol: str, is_long: bool, size_usd: float) -> dict:
        """Открывает позицию через IOC-ордер по mark price ± 2%."""
        from x10.perpetual.order_object import create_order_object
        from x10.perpetual.orders import OrderSide, TimeInForce

        self._init_client()
        market = await self._get_market(symbol)
        mark_price = await self._get_mark_price(symbol)

        mark = Decimal(str(mark_price))
        # Для гарантированного исполнения IOC-ордером: лонг — платим чуть дороже, шорт — чуть дешевле
        slippage = Decimal("0.02")
        price = mark * (1 + slippage) if is_long else mark * (1 - slippage)
        price = market.trading_config.round_price(price)

        qty = Decimal(str(size_usd)) / mark
        qty = max(qty, market.trading_config.min_order_size)
        # Округляем до шага размера рынка
        step = market.trading_config.min_order_size_change
        if step and step > 0:
            qty = Decimal(int(qty / step)) * step

        side = OrderSide.BUY if is_long else OrderSide.SELL

        logger.info(
            f"Extended: {'лонг' if is_long else 'шорт'} {symbol}, "
            f"qty={qty}, price={price}, mark={mark_price}"
        )

        order = create_order_object(
            account=self._stark_account,
            starknet_domain=self._endpoint_config.starknet_domain,
            market=market,
            side=side,
            amount_of_synthetic=qty,
            price=price,
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
            post_only=False,
        )
        result = await self._trading_client.orders.place_order(order=order)
        order_id = result.data.id if hasattr(result, "data") else result.id

        logger.info(f"Extended: ордер исполнен {symbol}, id={order_id}")
        return {
            "order_id": order_id,
            "size": float(qty),
            "size_usd": size_usd,
            "price": mark_price,
        }

    async def market_close(self, symbol: str, original_size: float, was_long: bool) -> dict:
        """Закрывает позицию через reduce_only IOC-ордер."""
        from x10.perpetual.order_object import create_order_object
        from x10.perpetual.orders import OrderSide, TimeInForce

        self._init_client()
        market = await self._get_market(symbol)
        mark_price = await self._get_mark_price(symbol)

        mark = Decimal(str(mark_price))
        slippage = Decimal("0.02")
        # Закрываем в противоположную сторону
        close_side = OrderSide.SELL if was_long else OrderSide.BUY
        price = mark * (1 - slippage) if was_long else mark * (1 + slippage)
        price = market.trading_config.round_price(price)

        qty = Decimal(str(original_size))
        step = market.trading_config.min_order_size_change
        if step and step > 0:
            qty = Decimal(int(qty / step)) * step

        logger.info(f"Extended: закрытие {symbol}, qty={qty}, side={close_side}")

        try:
            order = create_order_object(
                account=self._stark_account,
                starknet_domain=self._endpoint_config.starknet_domain,
                market=market,
                side=close_side,
                amount_of_synthetic=qty,
                price=price,
                time_in_force=TimeInForce.IOC,
                reduce_only=True,
                post_only=False,
            )
            result = await self._trading_client.orders.place_order(order=order)
            logger.info(f"Extended: позиция {symbol} закрыта")
            return {"symbol": symbol, "price": mark_price}
        except Exception as e:
            err_str = str(e).lower()
            safe_errors = ("no position", "nothing to close", "reduce only",
                           "no open position", "position not found")
            if any(s in err_str for s in safe_errors):
                logger.warning(f"Extended закрытие {symbol}: позиция уже закрыта ({e})")
                return {"symbol": symbol, "price": mark_price}
            raise RuntimeError(f"Extended ошибка закрытия {symbol}: {e}")

    async def get_positions(self) -> list | None:
        """
        Возвращает открытые позиции.
        None при ошибке, [] если позиций нет.
        Каждый элемент: {symbol, qty, mark_price, liquidation_price}.
        """
        try:
            self._init_client()
            resp = await self._trading_client.account.get_positions()
            result = []
            for pos in resp.data:
                symbol = pos.market.split("-")[0].upper()
                qty = float(pos.size)
                # PositionSide.SHORT → отрицательный qty
                side_str = str(pos.side).upper()
                if "SHORT" in side_str:
                    qty = -abs(qty)
                result.append({
                    "symbol": symbol,
                    "qty": qty,
                    "mark_price": float(pos.mark_price),
                    "liquidation_price": float(pos.liquidation_price) if pos.liquidation_price else 0,
                })
            logger.debug(f"Extended positions: {result}")
            return result
        except Exception as e:
            logger.warning(f"Extended get_positions ошибка: {e}")
            return None
