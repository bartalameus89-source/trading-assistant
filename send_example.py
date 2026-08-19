"""
Отправляет в Telegram пример сигнала в новом формате, чтобы можно было оценить
понятность сообщения до того, как пойдут настоящие алерты.

Берётся реальный сигнал из истории — не выдуманный.
Запуск:  python send_example.py [ТИКЕР]
"""

import json
import os
import sys
from dataclasses import replace

from engine import data, render, signals
from run import отправить_в_телеграм

БАЗА = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(БАЗА, "config.json"), encoding="utf-8"))
УСЛОВНЫЙ_ДЕПОЗИТ = 10000.0


def найти(актив: str):
    свечи = data.загрузить(актив, CFG["таймфрейм"], лимит=1000,
                           источники=CFG["источник_данных"])
    for i in range(len(свечи) - 1, 240, -1):
        срез = replace(
            свечи,
            времена=свечи.времена[:i + 1], opens=свечи.opens[:i + 1],
            highs=свечи.highs[:i + 1], lows=свечи.lows[:i + 1],
            closes=свечи.closes[:i + 1], volumes=свечи.volumes[:i + 1],
        )
        а = signals.проанализировать(срез, CFG, флаги_данных=[],
                                     депозит=УСЛОВНЫЙ_ДЕПОЗИТ,
                                     проверять_свежесть=False)
        if а.вывод == signals.СИГНАЛ:
            return а
    return None


if __name__ == "__main__":
    актив = sys.argv[1] if len(sys.argv) > 1 else "SOLUSDT"
    а = найти(актив)
    if not а:
        print(f"В доступной истории {актив} сигналов не нашлось.")
        raise SystemExit(1)

    текст = render.телеграм(а)
    шапка = ("<b>ПРИМЕР СООБЩЕНИЯ</b> — это не действующий сигнал, "
             f"а реальный случай из истории. Депозит взят условный: "
             f"{УСЛОВНЫЙ_ДЕПОЗИТ:,.0f}.\n\n").replace(",", " ")

    print(текст.replace("<b>", "").replace("</b>", "")
              .replace("<i>", "").replace("</i>", ""))
    print()
    if отправить_в_телеграм(шапка + текст):
        print("Отправлено в Telegram.")
