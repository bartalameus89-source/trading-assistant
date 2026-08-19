"""
Сбор материала для экспериментов: скачивает свечи и находит все сигналы,
сохраняя их вместе с планом сделки.

Смысл: генерация сигналов — самая долгая часть (индикаторы пересчитываются
на каждом баре). Сделав её один раз, можно быстро проверять десятки вариантов
правил выхода из сделки на одних и тех же входах.

Запуск:  python collect.py
Результат: cache/candles.json и cache/signals.json
"""

from __future__ import annotations

import json
import os
from dataclasses import replace

from engine import data, signals

БАЗА = os.path.dirname(os.path.abspath(__file__))
КЕШ = os.path.join(БАЗА, "cache")
CFG = json.load(open(os.path.join(БАЗА, "config.json"), encoding="utf-8"))
РАЗОГРЕВ = 240


def срез(свечи: data.Свечи, до: int) -> data.Свечи:
    от = max(0, до + 1 - 400)     # окно: глубже MA200 анализ не смотрит
    return replace(
        свечи,
        времена=свечи.времена[от:до + 1], opens=свечи.opens[от:до + 1],
        highs=свечи.highs[от:до + 1], lows=свечи.lows[от:до + 1],
        closes=свечи.closes[от:до + 1], volumes=свечи.volumes[от:до + 1],
    )


def главное() -> None:
    os.makedirs(КЕШ, exist_ok=True)
    все_свечи: dict[str, dict] = {}
    все_сигналы: list[dict] = []

    for актив in CFG["активы"]:
        try:
            свечи = data.загрузить_глубоко(актив, CFG["таймфрейм"], баров=6000,
                                           источники=CFG["источник_данных"])
        except Exception as e:
            print(f"  {актив:10s} ошибка загрузки: {type(e).__name__}")
            continue

        все_свечи[актив] = {
            "времена": свечи.времена, "opens": свечи.opens, "highs": свечи.highs,
            "lows": свечи.lows, "closes": свечи.closes, "volumes": свечи.volumes,
        }

        найдено = 0
        for i in range(РАЗОГРЕВ, len(свечи) - 2):
            а = signals.проанализировать(срез(свечи, i), CFG, флаги_данных=[],
                                         депозит=CFG["риск"]["депозит"],
                                         проверять_свежесть=False)
            if а.вывод != signals.СИГНАЛ:
                continue
            p = а.план
            все_сигналы.append({
                "актив": актив, "бар": i, "направление": а.направление,
                "вход": p.вход, "стоп": p.стоп, "цели": p.цели,
                "atr": p.atr, "тип_ордера": p.тип_ордера,
                "уверенность": а.уверенность, "режим": а.режим.направление,
                "волатильность": а.режим.волатильность,
                "перцентиль_bbw": round(а.режим.bbw_перцентиль),
                "тег": а.тег_стратегии,
                "групп": len(а.группы),
            })
            найдено += 1
        print(f"  {актив:10s} баров {len(свечи):4d}  сигналов {найдено}")

    with open(os.path.join(КЕШ, "candles.json"), "w", encoding="utf-8") as f:
        json.dump(все_свечи, f)
    with open(os.path.join(КЕШ, "signals.json"), "w", encoding="utf-8") as f:
        json.dump(все_сигналы, f, ensure_ascii=False)

    print(f"\nСохранено: {len(все_свечи)} монет, {len(все_сигналы)} сигналов.")
    print(f"Папка: {КЕШ}")


if __name__ == "__main__":
    главное()
