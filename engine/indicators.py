"""
Технические индикаторы на чистом Python. Внешних библиотек нет намеренно:
меньше зависимостей — меньше того, что может сломаться без предупреждения.

Соглашение для всех функций: возвращается список той же длины, что и вход.
Позиции, для которых индикатор ещё не определён (не хватает истории), = None.
Это важно: индикатор, посчитанный на неполной истории, — выдуманное число.
"""

from __future__ import annotations


# ---------------------------------------------------------------- скользящие

def sma(values: list[float], period: int) -> list[float | None]:
    """Простая скользящая средняя."""
    out: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    running = sum(values[:period])
    out[period - 1] = running / period
    for i in range(period, len(values)):
        running += values[i] - values[i - period]
        out[i] = running / period
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    """Экспоненциальная скользящая средняя. Затравка — SMA первых `period` значений."""
    out: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def _wilder_smooth(values: list[float], period: int, start: int) -> list[float | None]:
    """Сглаживание Уайлдера: первое значение — среднее, дальше рекуррентно.
    `start` — индекс, с которого в values начинаются осмысленные данные."""
    out: list[float | None] = [None] * len(values)
    first = start + period - 1
    if first >= len(values):
        return out
    prev = sum(values[start:first + 1]) / period
    out[first] = prev
    for i in range(first + 1, len(values)):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


# ------------------------------------------------------------------- моментум

def rsi(closes: list[float], period: int = 14) -> list[float | None]:
    """RSI по Уайлдеру. Значения 0..100."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out

    gains = [0.0] * len(closes)
    losses = [0.0] * len(closes)
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains[i] = delta if delta > 0 else 0.0
        losses[i] = -delta if delta < 0 else 0.0

    avg_gain = sum(gains[1:period + 1]) / period
    avg_loss = sum(losses[1:period + 1]) / period
    out[period] = _rsi_from_averages(avg_gain, avg_loss)

    for i in range(period + 1, len(closes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i] = _rsi_from_averages(avg_gain, avg_loss)
    return out


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """Возвращает (линия MACD, сигнальная линия, гистограмма)."""
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)

    line: list[float | None] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(ema_fast, ema_slow)
    ]

    # Сигнальная линия — EMA от линии MACD, считается только по определённой части.
    defined = [i for i, v in enumerate(line) if v is not None]
    sig: list[float | None] = [None] * len(closes)
    hist: list[float | None] = [None] * len(closes)
    if defined:
        head = defined[0]
        sig_part = ema([line[i] for i in defined], signal)
        for offset, i in enumerate(defined):
            sig[i] = sig_part[offset]
            if sig[i] is not None:
                hist[i] = line[i] - sig[i]
        del head
    return line, sig, hist


# --------------------------------------------------------------- волатильность

def true_range(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    tr = [highs[0] - lows[0]] if highs else []
    for i in range(1, len(highs)):
        pc = closes[i - 1]
        tr.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))
    return tr


def atr(highs: list[float], lows: list[float], closes: list[float],
        period: int = 14) -> list[float | None]:
    """ATR по Уайлдеру — мера типичного размаха свечи. Нужен для расчёта стопа."""
    if len(highs) < period:
        return [None] * len(highs)
    return _wilder_smooth(true_range(highs, lows, closes), period, start=0)


def bollinger(closes: list[float], period: int = 20, mult: float = 2.0):
    """Возвращает (верх, середина, низ, ширина). Ширина = (верх-низ)/середина в процентах."""
    mid = sma(closes, period)
    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    width: list[float | None] = [None] * len(closes)

    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1:i + 1]
        m = mid[i]
        variance = sum((x - m) ** 2 for x in window) / period
        sd = variance ** 0.5
        upper[i] = m + mult * sd
        lower[i] = m - mult * sd
        width[i] = (upper[i] - lower[i]) / m * 100.0 if m else None
    return upper, mid, lower, width


# -------------------------------------------------------------- сила тренда

def adx(highs: list[float], lows: list[float], closes: list[float],
        period: int = 14):
    """ADX по Уайлдеру. Возвращает (adx, +DI, -DI).
    ADX измеряет СИЛУ тренда, а не направление: 25+ обычно считают трендом,
    ниже 20 — боковиком. Направление даёт соотношение +DI и -DI."""
    n = len(highs)
    empty = [None] * n
    if n < period * 2:
        return empty, empty[:], empty[:]

    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0

    tr = true_range(highs, lows, closes)

    # Сглаживание начинаем с индекса 1: нулевой бар не имеет предыдущего.
    tr_s = _wilder_smooth(tr, period, start=1)
    plus_s = _wilder_smooth(plus_dm, period, start=1)
    minus_s = _wilder_smooth(minus_dm, period, start=1)

    plus_di: list[float | None] = [None] * n
    minus_di: list[float | None] = [None] * n
    dx: list[float | None] = [None] * n

    for i in range(n):
        if tr_s[i] is None or tr_s[i] == 0:
            continue
        plus_di[i] = 100.0 * plus_s[i] / tr_s[i]
        minus_di[i] = 100.0 * minus_s[i] / tr_s[i]
        denom = plus_di[i] + minus_di[i]
        dx[i] = 100.0 * abs(plus_di[i] - minus_di[i]) / denom if denom else 0.0

    defined = [i for i, v in enumerate(dx) if v is not None]
    adx_out: list[float | None] = [None] * n
    if len(defined) >= period:
        head = defined[0]
        smoothed = _wilder_smooth([dx[i] for i in defined], period, start=0)
        for offset, i in enumerate(defined):
            adx_out[i] = smoothed[offset]
        del head
    return adx_out, plus_di, minus_di


# ------------------------------------------------------------------ утилиты

def percentile_rank(window: list[float], value: float) -> float:
    """На каком перцентиле находится `value` внутри `window`. 0..100."""
    clean = [v for v in window if v is not None]
    if not clean:
        return 50.0
    below = sum(1 for v in clean if v < value)
    return below / len(clean) * 100.0


def last_defined(series: list[float | None]):
    """Последнее определённое значение серии и его индекс, иначе (None, None)."""
    for i in range(len(series) - 1, -1, -1):
        if series[i] is not None:
            return series[i], i
    return None, None
