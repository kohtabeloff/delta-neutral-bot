import logging
import time

logger = logging.getLogger(__name__)

LIGHTER_BASE_URL = "https://mainnet.zklighter.elliot.ai"


class LighterExecutor:
    """
    Клиент для торговли на Lighter (ZK order book DEX).
    Требует lighter-sdk: pip install lighter-sdk
    Ключи генерируются на lighter.xyz → Settings → API Keys.
    """

    def __init__(self, api_private_key: str, api_key_index: int, account_index: int):
        self._api_private_key = api_private_key
        self._api_key_index = api_key_index
        self._account_index = account_index
        self._signer = None
        self._markets: dict = {}  # symbol → OrderBook объект из SDK

    def _get_signer(self):
        """Ленивая инициализация SignerClient."""
        if self._signer is None:
            try:
                import lighter
                self._signer = lighter.SignerClient(
                    url=LIGHTER_BASE_URL,
                    api_private_keys={self._api_key_index: self._api_private_key},
                    account_index=self._account_index,
                )
            except ImportError:
                raise RuntimeError("lighter-sdk не установлен: pip install lighter-sdk")
        return self._signer

    async def _ensure_markets(self):
        """Загружает список рынков через SDK (symbol → market_id, параметры)."""
        if self._markets:
            return
        signer = self._get_signer()
        result = await signer.order_api.order_books()
        for ob in (result.order_books or []):
            self._markets[ob.symbol.upper()] = ob
        logger.info(f"Lighter: загружено {len(self._markets)} рынков")

    async def _get_price(self, symbol: str) -> float:
        """Получает последнюю цену сделки из exchange_stats."""
        signer = self._get_signer()
        stats = await signer.order_api.exchange_stats()
        for ob in (stats.order_book_stats or []):
            if ob.symbol.upper() == symbol.upper():
                return float(ob.last_trade_price)
        raise ValueError(f"Цена {symbol} не найдена на Lighter")

    async def market_open(self, symbol: str, is_long: bool, size_usd: float) -> dict:
        """
        Открывает рыночный ордер на Lighter по USD сумме.
        is_long=True → лонг, False → шорт.
        """
        signer = self._get_signer()
        await self._ensure_markets()

        market = self._markets.get(symbol.upper())
        if not market:
            raise ValueError(f"Рынок {symbol} не найден на Lighter")

        market_index = int(market.market_id)
        price = await self._get_price(symbol)
        client_order_id = int(time.time() * 1000) % 1_000_000

        logger.info(
            f"Lighter: {'лонг' if is_long else 'шорт'} {symbol}, "
            f"market_id={market_index}, ${size_usd}, цена={price}"
        )

        tx, tx_hash, err = await signer.create_market_order_quote_amount(
            market_index=market_index,
            client_order_index=client_order_id,
            quote_amount=size_usd,
            max_slippage=0.10,      # 10% максимальное проскальзывание
            is_ask=not is_long,     # is_ask=True → SHORT (продажа)
        )

        if err:
            raise RuntimeError(f"Lighter ошибка открытия {symbol}: {err}")

        logger.info(f"Lighter: ордер исполнен {symbol}, tx={tx_hash}")
        return {
            "tx_hash": str(tx_hash),
            "size": size_usd / price,   # приблизительный размер в базовом токене
            "size_usd": size_usd,
            "price": price,
        }

    async def market_close(self, symbol: str, original_size: float, was_long: bool) -> dict:
        """
        Закрывает позицию через reduce_only ордер.
        original_size: размер в базовом токене (из БД).
        was_long: True если открывали лонг (нужно закрыть шортом).
        """
        signer = self._get_signer()
        await self._ensure_markets()

        market = self._markets.get(symbol.upper())
        if not market:
            raise ValueError(f"Рынок {symbol} не найден на Lighter")

        market_index = int(market.market_id)
        price = await self._get_price(symbol)
        size_usd = original_size * price
        client_order_id = int(time.time() * 1000) % 1_000_000

        logger.info(f"Lighter: закрытие {symbol}, market_id={market_index}, ~${size_usd:.2f}")

        tx, tx_hash, err = await signer.create_market_order_quote_amount(
            market_index=market_index,
            client_order_index=client_order_id,
            quote_amount=size_usd,
            max_slippage=0.15,      # 15% для закрытия (чуть шире)
            is_ask=was_long,        # закрываем лонг через Ask (продажа)
            reduce_only=True,       # только уменьшаем существующую позицию
        )

        if err:
            err_str = str(err).lower()
            # reduce_only ордер отклоняется когда позиции нет — это нормально
            safe_errors = ("no position", "position not found", "nothing to close",
                           "reduce only", "no open position")
            if any(s in err_str for s in safe_errors):
                logger.warning(f"Lighter закрытие {symbol}: позиция уже закрыта ({err})")
                return {"tx_hash": "", "symbol": symbol, "price": price}
            # Любая другая ошибка — настоящая проблема, прокидываем выше
            raise RuntimeError(f"Lighter ошибка закрытия {symbol}: {err}")

        logger.info(f"Lighter: позиция {symbol} закрыта, tx={tx_hash}")
        return {"tx_hash": str(tx_hash), "symbol": symbol, "price": price}

    async def get_positions(self):
        """
        Возвращает открытые перп-позиции с Lighter через REST API.
        Возвращает None при ошибке (нельзя определить состояние),
        [] если позиций нет (API ответил, позиций нет).
        Каждый элемент: {symbol, quantity} (quantity > 0 = лонг, < 0 = шорт).
        """
        import httpx
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{LIGHTER_BASE_URL}/v1/accounts",
                    params={"blockchain_index": self._account_index},
                )
            if resp.status_code != 200:
                logger.warning(f"Lighter positions: HTTP {resp.status_code}: {resp.text[:200]}")
                return None
            data = resp.json()

            # Пробуем разные структуры ответа
            account = data.get("account") or data
            raw_positions = (
                account.get("perp_positions")
                or account.get("positions")
                or account.get("open_positions")
                or []
            )

            positions = []
            for pos in raw_positions:
                qty_raw = pos.get("quantity") or pos.get("size") or 0
                qty = float(qty_raw)
                if qty == 0:
                    continue
                symbol = (
                    pos.get("market_symbol") or pos.get("symbol") or ""
                ).replace("-PERP", "").replace("/USDC", "").upper()
                positions.append({"symbol": symbol, "quantity": qty})

            logger.debug(f"Lighter positions: {positions}")
            return positions

        except Exception as e:
            logger.warning(f"Lighter get_positions ошибка: {e}")
            return None

    async def close(self):
        """Закрывает соединение SDK."""
        if self._signer:
            await self._signer.close()
