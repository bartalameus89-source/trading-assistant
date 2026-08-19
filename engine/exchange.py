"""
Ограничения биржи на размер заявки.

Биржа не исполнит сделку меньше минимальной суммы и округлит количество
к своему шагу. Для крупного счёта это незаметно, для счёта в 10-100 $ —
решающее обстоятельство: рассчитанная позиция может просто не пройти.

Здесь ограничения загружаются один раз и кешируются на диск, потому что
меняются они редко, а на каждом запуске тянуть их незачем.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

БАЗА = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ФАЙЛ_КЕША = os.path.join(БАЗА, "cache", "exchange_limits.json")
СРОК_КЕША = 7 * 24 * 3600      # неделя


def _скачать(активы: list[str]) -> dict:
    url = ("https://api.binance.com/api/v3/exchangeInfo?symbols="
           + json.dumps(активы).replace(" ", ""))
    req = urllib.request.Request(url, headers={"User-Agent": "trading-assistant/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        данные = json.loads(r.read())

    лимиты = {}
    for s in данные["symbols"]:
        ф = {x["filterType"]: x for x in s["filters"]}
        объём = ф.get("NOTIONAL") or ф.get("MIN_NOTIONAL") or {}
        лимиты[s["symbol"]] = {
            "мин_объём": float(объём.get("minNotional", 0)),
            "шаг_количества": float(ф["LOT_SIZE"]["stepSize"]),
            "мин_количество": float(ф["LOT_SIZE"]["minQty"]),
            "шаг_цены": float(ф["PRICE_FILTER"]["tickSize"]),
        }
    return лимиты


def загрузить_лимиты(активы: list[str], обновить: bool = False) -> dict:
    """Ограничения по каждому активу. Кеш на неделю; при сбое сети
    возвращается кеш, даже устаревший — это лучше, чем ничего."""
    os.makedirs(os.path.dirname(ФАЙЛ_КЕША), exist_ok=True)
    свежий = (os.path.exists(ФАЙЛ_КЕША)
              and time.time() - os.path.getmtime(ФАЙЛ_КЕША) < СРОК_КЕША)

    if свежий and not обновить:
        with open(ФАЙЛ_КЕША, encoding="utf-8") as f:
            кеш = json.load(f)
        if all(а in кеш for а in активы):
            return кеш

    try:
        лимиты = _скачать(активы)
    except Exception:
        if os.path.exists(ФАЙЛ_КЕША):
            with open(ФАЙЛ_КЕША, encoding="utf-8") as f:
                return json.load(f)
        raise

    with open(ФАЙЛ_КЕША, "w", encoding="utf-8") as f:
        json.dump(лимиты, f, ensure_ascii=False, indent=1)
    return лимиты


def округлить_вниз(количество: float, шаг: float) -> float:
    """Биржа принимает только кратное шагу. Округляем ВНИЗ: превысить
    рассчитанный риск нельзя, а недобрать — можно."""
    if шаг <= 0:
        return количество
    шагов = int(количество / шаг + 1e-9)
    return round(шагов * шаг, 12)


def минимальный_депозит(мин_объём: float, стоп_проц: float, риск_проц: float) -> float:
    """Какой капитал нужен, чтобы позиция дотянула до минимума биржи.

    объём = депозит * риск% / стоп%   =>   депозит = мин_объём * стоп% / риск%
    """
    if риск_проц <= 0:
        return float("inf")
    return мин_объём * стоп_проц / риск_проц
