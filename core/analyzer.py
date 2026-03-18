from scanners.base import FundingRate
from config import MIN_APR_THRESHOLD, MIN_OPEN_INTEREST_USD

# Примерные комиссии на открытие+закрытие (обе стороны)
# Выраженные в APR — чтобы сравнивать с funding APR
# Taker fee 0.05% * 2 стороны * 2 (открытие + закрытие) = 0.2%
# При $1000 и 1 цикле в 30 дней это примерно 2.4% APR
ESTIMATED_FEES_APR = 5.0  # берём с запасом


def find_best_opportunities(
    all_rates: list[FundingRate],
    min_apr=None,
) -> list[dict]:
    if min_apr is None:
        min_apr = MIN_APR_THRESHOLD
    """
    Ищет лучшие возможности для заработка на фандинге.

    Сейчас работает в режиме "одна биржа":
    - Если APR высокий положительный → продавцы (шорты) получают выплаты
    - Если APR высокий отрицательный → покупатели (лонги) получают выплаты

    В Фазе 2 добавим сравнение между биржами.
    """
    opportunities = []

    for rate in all_rates:
        # Фильтр по ликвидности — пропускаем монеты с маленьким OI
        if rate.open_interest_usd < MIN_OPEN_INTEREST_USD:
            continue

        abs_apr = abs(rate.apr)
        net_apr = abs_apr - ESTIMATED_FEES_APR  # чистый APR после комиссий

        if net_apr < min_apr:
            continue

        # Определяем направление: кто получает фандинг
        if rate.apr > 0:
            direction = "SHORT"  # лонги платят шортам → открываем шорт
            description = f"Лонги переплачивают, шорты получают"
        else:
            direction = "LONG"   # шорты платят лонгам → открываем лонг
            description = f"Шорты переплачивают, лонги получают"

        opportunities.append({
            "exchange": rate.exchange,
            "symbol": rate.symbol,
            "direction": direction,
            "gross_apr": round(abs_apr, 2),
            "net_apr": round(net_apr, 2),
            "rate_per_hour": round(rate.rate * 100, 6),
            "open_interest_usd": round(rate.open_interest_usd),
            "description": description,
        })

    # Сортируем по чистому APR — лучшие наверху
    opportunities.sort(key=lambda x: x["net_apr"], reverse=True)
    return opportunities
