"""
Загрузка свечей (OHLCV) с биржи. Только стандартная библиотека Python.

Основной источник — Binance, запасной — Bybit. Если основной не отвечает,
переключаемся молча, но помечаем в результате, откуда пришли данные:
знать источник важно, потому что цены на разных биржах слегка различаются.

OKX не используется — с этого интернет-подключения отдаёт 403.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

ТАЙМАУТ = 20

# Длительность одного бара в минутах — нужна для проверки свежести данных.
ДЛИТЕЛЬНОСТЬ_БАРА_МИН = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "12h": 720,
    "1d": 1440,
}

_BYBIT_ИНТЕРВАЛ = {
    "1m": "1", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
    "1d": "D",
}


@dataclass
class Свечи:
    актив: str
    таймфрейм: str
    источник: str
    времена: list[int]          #时间 открытия бара, миллисекунды UTC
    opens: list[float]
    highs: list[float]
    lows: list[float]
    closes: list[float]
    volumes: list[float]

    def __len__(self) -> int:
        return len(self.closes)

    @property
    def последняя_цена(self) -> float:
        return self.closes[-1]

    @property
    def время_последнего_бара(self) -> datetime:
        return datetime.fromtimestamp(self.времена[-1] / 1000, tz=timezone.utc)

    def возраст_в_барах(self, сейчас: datetime | None = None) -> float:
        """Сколько баров прошло с открытия последнего. Раздел 1.1: данные
        старше MAX_DATA_AGE баров не годятся для сигнала на вход."""
        сейчас = сейчас or datetime.now(timezone.utc)
        минут = (сейчас - self.время_последнего_бара).total_seconds() / 60
        шаг = ДЛИТЕЛЬНОСТЬ_БАРА_МИН.get(self.таймфрейм, 240)
        return минут / шаг


def _запрос(url: str) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": "trading-assistant/1.0"})
    with urllib.request.urlopen(req, timeout=ТАЙМАУТ) as r:
        return json.loads(r.read())


def _с_binance(актив: str, таймфрейм: str, лимит: int) -> Свечи:
    url = (
        "https://api.binance.com/api/v3/klines"
        f"?symbol={актив}&interval={таймфрейм}&limit={лимит}"
    )
    raw = _запрос(url)
    return Свечи(
        актив=актив, таймфрейм=таймфрейм, источник="binance",
        времена=[int(k[0]) for k in raw],
        opens=[float(k[1]) for k in raw],
        highs=[float(k[2]) for k in raw],
        lows=[float(k[3]) for k in raw],
        closes=[float(k[4]) for k in raw],
        volumes=[float(k[5]) for k in raw],
    )


def _с_bybit(актив: str, таймфрейм: str, лимит: int) -> Свечи:
    интервал = _BYBIT_ИНТЕРВАЛ.get(таймфрейм, "240")
    url = (
        "https://api.bybit.com/v5/market/kline"
        f"?category=spot&symbol={актив}&interval={интервал}&limit={min(лимит, 1000)}"
    )
    raw = _запрос(url)
    if raw.get("retCode") != 0:
        raise RuntimeError(f"Bybit вернул ошибку: {raw.get('retMsg')}")
    # Bybit отдаёт свечи от новых к старым — разворачиваем.
    строки = list(reversed(raw["result"]["list"]))
    return Свечи(
        актив=актив, таймфрейм=таймфрейм, источник="bybit",
        времена=[int(k[0]) for k in строки],
        opens=[float(k[1]) for k in строки],
        highs=[float(k[2]) for k in строки],
        lows=[float(k[3]) for k in строки],
        closes=[float(k[4]) for k in строки],
        volumes=[float(k[5]) for k in строки],
    )


ЗАГРУЗЧИКИ = {"binance": _с_binance, "bybit": _с_bybit}


def загрузить(актив: str, таймфрейм: str = "4h", лимит: int = 400,
              источники: list[str] | None = None) -> Свечи:
    """Пробует источники по порядку. Возвращает первый успешный результат.

    `лимит` по умолчанию 400 баров: MA200 требует 200, плюс запас на
    сглаживание ADX и на окно перцентиля ширины Bollinger."""
    источники = источники or ["binance", "bybit"]
    ошибки = []
    for имя in источники:
        загрузчик = ЗАГРУЗЧИКИ.get(имя)
        if загрузчик is None:
            ошибки.append(f"{имя}: неизвестный источник")
            continue
        try:
            свечи = загрузчик(актив, таймфрейм, лимит)
            if len(свечи) == 0:
                ошибки.append(f"{имя}: пустой ответ")
                continue
            return свечи
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, RuntimeError, KeyError, ValueError) as e:
            ошибки.append(f"{имя}: {type(e).__name__}: {e}")
    raise RuntimeError(
        f"Не удалось загрузить {актив} {таймфрейм}. Попытки:\n  " + "\n  ".join(ошибки)
    )


def проверить_качество(свечи: Свечи, макс_возраст_баров: float) -> list[str]:
    """Раздел 14 промта: помечаем подозрительные данные ДО того, как их анализировать.
    Возвращает список предупреждений; пустой список = данные выглядят нормально."""
    флаги: list[str] = []

    возраст = свечи.возраст_в_барах()
    if возраст > макс_возраст_баров:
        флаги.append(
            f"устаревшие данные: последний бар открыт {возраст:.1f} баров назад "
            f"(предел {макс_возраст_баров})"
        )

    if len(свечи) < 200:
        флаги.append(f"короткая история: {len(свечи)} баров, для MA200 нужно минимум 200")

    # Пропуски во времени: расстояние между барами должно быть постоянным.
    шаг_мс = ДЛИТЕЛЬНОСТЬ_БАРА_МИН.get(свечи.таймфрейм, 240) * 60 * 1000
    пропуски = sum(
        1 for i in range(1, len(свечи.времена))
        if свечи.времена[i] - свечи.времена[i - 1] != шаг_мс
    )
    if пропуски:
        флаги.append(f"разрывы в истории: {пропуски} нестандартных промежутков между барами")

    # Аномальные скачки цены между закрытиями.
    for i in range(1, len(свечи.closes)):
        пред = свечи.closes[i - 1]
        if пред <= 0:
            флаги.append(f"нулевая или отрицательная цена на баре {i - 1}")
            break
        скачок = abs(свечи.closes[i] - пред) / пред * 100
        if скачок > 25:
            флаги.append(
                f"аномальный скачок цены {скачок:.1f}% на баре {i} "
                f"({пред:g} -> {свечи.closes[i]:g})"
            )
            break

    if any(v == 0 for v in свечи.volumes[-20:]):
        флаги.append("нулевой объём в последних 20 барах — низкая ликвидность или сбой данных")

    return флаги
