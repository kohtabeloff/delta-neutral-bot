import aiosqlite
import os
import time

# Абсолютный путь — работает независимо от рабочей директории при запуске
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "positions.db")


async def init_db():
    """Создаёт таблицы. Мигрирует схему при необходимости."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("PRAGMA table_info(positions)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]

        # Старая схема без direction — пересоздаём
        if "spot_size" in columns:
            await db.execute("DROP TABLE positions")
            await db.commit()
            columns = []

        await db.execute("""
            CREATE TABLE IF NOT EXISTS funding_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                rate REAL NOT NULL,
                apr REAL NOT NULL,
                open_interest_usd REAL NOT NULL DEFAULT 0
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_ts ON funding_history (timestamp)"
        )
        await db.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                exchange TEXT NOT NULL,
                direction TEXT NOT NULL DEFAULT 'SHORT',
                size REAL NOT NULL,
                entry_price REAL NOT NULL,
                position_size_usd REAL NOT NULL,
                entry_apr REAL NOT NULL,
                opened_at REAL NOT NULL,
                status TEXT DEFAULT 'open',
                pair_id TEXT DEFAULT NULL,
                closed_at REAL DEFAULT NULL,
                exit_price REAL DEFAULT NULL,
                pnl_price_usd REAL DEFAULT NULL,
                fees_usd REAL DEFAULT NULL
            )
        """)

        # Миграция: добавляем pair_id если его нет
        if columns and "pair_id" not in columns:
            await db.execute("ALTER TABLE positions ADD COLUMN pair_id TEXT DEFAULT NULL")

        # Миграция: добавляем closed_at если его нет
        if columns and "closed_at" not in columns:
            await db.execute("ALTER TABLE positions ADD COLUMN closed_at REAL DEFAULT NULL")

        # Миграция: поля для реального P&L при закрытии
        if columns and "exit_price" not in columns:
            await db.execute("ALTER TABLE positions ADD COLUMN exit_price REAL DEFAULT NULL")
        if columns and "pnl_price_usd" not in columns:
            await db.execute("ALTER TABLE positions ADD COLUMN pnl_price_usd REAL DEFAULT NULL")
        if columns and "fees_usd" not in columns:
            await db.execute("ALTER TABLE positions ADD COLUMN fees_usd REAL DEFAULT NULL")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        await db.commit()


async def save_setting(key: str, value: str):
    """Сохраняет настройку в БД."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
        await db.commit()


async def load_setting(key: str, default: str = "") -> str:
    """Загружает настройку из БД. Возвращает default если не найдено."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else default


async def save_position(symbol, exchange, direction, size,
                        entry_price, position_size_usd, entry_apr,
                        pair_id=None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO positions
            (symbol, exchange, direction, size, entry_price,
             position_size_usd, entry_apr, opened_at, pair_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (symbol, exchange, direction, size, entry_price,
              position_size_usd, entry_apr, time.time(), pair_id))
        await db.commit()


async def save_pair(pair_id: str, legs: list[dict]):
    """
    Атомарно сохраняет обе ноги пары в одной транзакции.
    legs: список словарей с ключами symbol, exchange, direction, size,
          entry_price, position_size_usd, entry_apr.
    """
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        for leg in legs:
            await db.execute("""
                INSERT INTO positions
                (symbol, exchange, direction, size, entry_price,
                 position_size_usd, entry_apr, opened_at, pair_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (leg["symbol"], leg["exchange"], leg["direction"],
                  leg["size"], leg["entry_price"], leg["position_size_usd"],
                  leg["entry_apr"], now, pair_id))
        await db.commit()  # оба INSERT или ни одного


async def scale_pair_db(pair_id: str, legs: list, lt_new_size: float, lt_new_price: float,
                        bp_new_size: float, bp_new_price: float, add_size_usd: float):
    """Обновляет ноги LT+BP пары после scale in."""
    await scale_pair_db_generic(
        legs=legs,
        results_by_exchange={
            "Lighter": {"size": lt_new_size, "price": lt_new_price},
            "Backpack": {"size": bp_new_size, "price": bp_new_price},
        },
        add_size_usd=add_size_usd,
    )


async def scale_pair_db_generic(legs: list, results_by_exchange: dict, add_size_usd: float):
    """
    Универсальное обновление ног пары после scale in.
    results_by_exchange: {exchange_name: {"size": float, "price": float}}
    """
    async with aiosqlite.connect(DB_PATH) as db:
        for leg in legs:
            exch = leg["exchange"]
            if exch not in results_by_exchange:
                continue
            r = results_by_exchange[exch]
            old_size = leg["size"]
            new_size = old_size + r["size"]
            new_avg_price = (leg["entry_price"] * old_size + r["price"] * r["size"]) / new_size
            new_usd = leg["position_size_usd"] + add_size_usd
            await db.execute(
                "UPDATE positions SET size=?, entry_price=?, position_size_usd=? WHERE id=?",
                (new_size, new_avg_price, new_usd, leg["id"]),
            )
        await db.commit()


async def get_open_positions():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM positions WHERE status = 'open' ORDER BY opened_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def save_funding_snapshot(rates_by_exchange: dict):
    """Сохраняет снапшот фандинга со всех бирж."""
    now = time.time()
    rows = []
    for exchange_name, rates in rates_by_exchange.items():
        for r in rates:
            rows.append((now, r.exchange, r.symbol, r.rate, r.apr, r.open_interest_usd))

    if not rows:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany("""
            INSERT INTO funding_history (timestamp, exchange, symbol, rate, apr, open_interest_usd)
            VALUES (?, ?, ?, ?, ?, ?)
        """, rows)
        await db.commit()


async def get_funding_stats(hours: int = 24) -> list[dict]:
    """Возвращает среднее и макс APR по каждой бирже+символу за последние N часов."""
    since = time.time() - hours * 3600
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT exchange, symbol,
                   ROUND(AVG(apr), 1) as avg_apr,
                   ROUND(MAX(ABS(apr)), 1) as max_apr,
                   COUNT(*) as samples
            FROM funding_history
            WHERE timestamp > ?
            GROUP BY exchange, symbol
            ORDER BY max_apr DESC
        """, (since,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def close_position(position_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE positions SET status = 'closed', closed_at = ? WHERE id = ?",
            (time.time(), position_id)
        )
        await db.commit()


async def close_pair(pair_id: str, leg_pnl: dict = None):
    """
    Закрывает обе ноги пары по pair_id.
    leg_pnl: {position_id: {exit_price, pnl_price_usd, fees_usd}} — опционально.
    """
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        if leg_pnl:
            for pos_id, data in leg_pnl.items():
                await db.execute(
                    """UPDATE positions
                       SET status = 'closed', closed_at = ?,
                           exit_price = ?, pnl_price_usd = ?, fees_usd = ?
                       WHERE id = ?""",
                    (now,
                     data.get("exit_price"),
                     data.get("pnl_price_usd"),
                     data.get("fees_usd"),
                     pos_id)
                )
        else:
            await db.execute(
                "UPDATE positions SET status = 'closed', closed_at = ? WHERE pair_id = ?",
                (now, pair_id)
            )
        await db.commit()


async def get_positions_by_pair(pair_id: str) -> list[dict]:
    """Возвращает обе ноги пары."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM positions WHERE pair_id = ? AND status = 'open'",
            (pair_id,)
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def count_closed_pairs() -> int:
    """Возвращает общее число закрытых пар и одиночных позиций."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT COUNT(*) FROM (
                SELECT pair_id FROM positions
                WHERE status='closed' AND pair_id IS NOT NULL
                GROUP BY pair_id
                UNION ALL
                SELECT CAST(id AS TEXT) FROM positions
                WHERE status='closed' AND pair_id IS NULL
            )
        """) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def get_closed_pairs(limit: int = 5, offset: int = 0) -> list[dict]:
    """Возвращает закрытые пары постранично, отсортированные по дате закрытия."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # Получаем страницу pair_id/id в нужном порядке
        async with db.execute("""
            SELECT pair_id, MAX(closed_at) as closed_at, 'pair' as kind
            FROM positions WHERE status='closed' AND pair_id IS NOT NULL
            GROUP BY pair_id
            UNION ALL
            SELECT CAST(id AS TEXT), closed_at, 'single' as kind
            FROM positions WHERE status='closed' AND pair_id IS NULL
            ORDER BY closed_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)) as cur:
            page_items = [dict(row) for row in await cur.fetchall()]

        result = []
        for item in page_items:
            if item["kind"] == "pair":
                async with db.execute(
                    "SELECT * FROM positions WHERE pair_id=? AND status='closed'",
                    (item["pair_id"],)
                ) as cur:
                    legs = [dict(r) for r in await cur.fetchall()]
                result.append({"pair_id": item["pair_id"], "legs": legs, "closed_at": item["closed_at"]})
            else:
                async with db.execute(
                    "SELECT * FROM positions WHERE id=?", (int(item["pair_id"]),)
                ) as cur:
                    legs = [dict(r) for r in await cur.fetchall()]
                result.append({"pair_id": None, "legs": legs, "closed_at": item["closed_at"]})
        return result


async def get_open_pairs() -> list[dict]:
    """
    Возвращает открытые двуногие позиции, сгруппированные по pair_id.
    Одиночные позиции (pair_id IS NULL) возвращаются как есть.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM positions WHERE status = 'open' ORDER BY opened_at DESC"
        ) as cursor:
            rows = [dict(row) for row in await cursor.fetchall()]

    # Группируем по pair_id
    pairs: dict = {}
    singles = []
    for row in rows:
        pid = row.get("pair_id")
        if pid:
            pairs.setdefault(pid, []).append(row)
        else:
            singles.append({"pair_id": None, "legs": [row]})

    result = []
    for pid, legs in pairs.items():
        result.append({"pair_id": pid, "legs": legs})
    result.extend(singles)
    return result
