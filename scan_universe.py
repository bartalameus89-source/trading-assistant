"""
Сколько сигналов даст система, если следить не за тремя монетами, а за многими.

Смысл эксперимента: частоту сделок можно поднимать двумя способами —
(1) ослабить требования к каждой сделке, (2) смотреть больше рынков.
Первый способ ухудшает каждую сделку. Второй сохраняет планку и просто даёт
системе больше мест, где эта планка может быть выполнена. Здесь измеряется второй.

Запуск:  python scan_universe.py [сколько_монет]
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from dataclasses import replace

from engine import data, signals

БАЗА = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(БАЗА, "config.json"), encoding="utf-8"))
РАЗОГРЕВ = 240

# Не торгуем: стейблкоины к стейблкоинам и «плечевые» токены — у них
# принципиально другая механика цены, индикаторы к ним неприменимы.
ИСКЛЮЧИТЬ = ("USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT", "DAIUSDT",
             "EURUSDT", "USDPUSDT")
ПЛЕЧЕВЫЕ = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")


def топ_пары(сколько: int) -> list[str]:
    """Самые ликвидные пары к USDT по обороту за сутки."""
    req = urllib.request.Request(
        "https://api.binance.com/api/v3/ticker/24hr",
        headers={"User-Agent": "trading-assistant/1.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        строки = json.loads(r.read())

    годные = []
    for s in строки:
        символ = s["symbol"]
        if not символ.endswith("USDT"):
            continue
        if символ in ИСКЛЮЧИТЬ or символ.endswith(ПЛЕЧЕВЫЕ):
            continue
        оборот = float(s["quoteVolume"])
        if оборот < CFG["допустимость_актива"]["мин_дневной_объём_usd"]:
            continue
        годные.append((символ, оборот))

    годные.sort(key=lambda п: -п[1])
    return [символ for символ, _ in годные[:сколько]]


def срез(свечи: data.Свечи, до: int) -> data.Свечи:
    return replace(
        свечи,
        времена=свечи.времена[:до + 1], opens=свечи.opens[:до + 1],
        highs=свечи.highs[:до + 1], lows=свечи.lows[:до + 1],
        closes=свечи.closes[:до + 1], volumes=свечи.volumes[:до + 1],
    )


def посчитать(актив: str) -> tuple[int, int, dict[str, int]]:
    свечи = data.загрузить(актив, CFG["таймфрейм"], лимит=1000,
                           источники=CFG["источник_данных"])
    if len(свечи) <= РАЗОГРЕВ + 10:
        return 0, 0, {}

    сигналов = 0
    причины: dict[str, int] = {}
    баров = 0
    for i in range(РАЗОГРЕВ, len(свечи)):
        баров += 1
        а = signals.проанализировать(срез(свечи, i), CFG, флаги_данных=[],
                                     депозит=10000.0, проверять_свежесть=False)
        if а.вывод == signals.СИГНАЛ:
            сигналов += 1
        elif а.план and а.план.нарушения:
            к = а.план.нарушения[0].правило
            причины[к] = причины.get(к, 0) + 1
        elif а.причины_отказа:
            к = а.причины_отказа[0].split(":")[0][:40]
            причины[к] = причины.get(к, 0) + 1
    return сигналов, баров, причины


def главное() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--топ":
        # Разведочный режим: взять самые оборотистые пары с биржи как есть.
        пары = топ_пары(int(sys.argv[2]) if len(sys.argv) > 2 else 20)
        print(f"Отобрано {len(пары)} самых ликвидных пар к USDT (разведка).")
    else:
        # Рабочий режим: список из конфига — только проверенные монеты.
        пары = CFG["активы"]
        print(f"Список из конфига: {len(пары)} устоявшихся монет.")
    print(f"Таймфрейм {CFG['таймфрейм']}, история — по 1000 баров на пару.\n")

    итого_сигналов = 0
    итого_баров = 0
    общие_причины: dict[str, int] = {}
    строки: list[tuple[str, int]] = []

    for n, актив in enumerate(пары, 1):
        try:
            с, б, причины = посчитать(актив)
        except Exception as e:
            print(f"  {n:2d}. {актив:12s} ошибка: {type(e).__name__}")
            continue
        итого_сигналов += с
        итого_баров += б
        for к, v in причины.items():
            общие_причины[к] = общие_причины.get(к, 0) + v
        строки.append((актив, с))
        print(f"  {n:2d}. {актив:12s} сигналов: {с}")

    дней = итого_баров / len(строки) / 6 if строки else 1   # 6 баров по 4ч в сутках
    print("\n" + "=" * 58)
    print(f"ИТОГО: {итого_сигналов} сигналов по {len(строки)} монетам "
          f"за ~{дней:.0f} дней истории")
    if дней:
        print(f"В среднем: {итого_сигналов / дней:.2f} сигнала в день "
              f"({итого_сигналов / дней * 7:.1f} в неделю)")
    print("=" * 58)
    print("\nЧто чаще всего мешало (по всем монетам):")
    for к, v in sorted(общие_причины.items(), key=lambda kv: -kv[1])[:8]:
        print(f"  {v:6d}  {к}")

    лучшие = sorted(строки, key=lambda п: -п[1])[:10]
    print("\nМонеты, дающие больше всего сигналов:")
    for актив, с in лучшие:
        print(f"  {актив:12s} {с}")


if __name__ == "__main__":
    главное()
