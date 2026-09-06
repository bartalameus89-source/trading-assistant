"""
Пульс: отметка «система прошла», отдельная от журнала сделок.

Зачем. Сторож сначала смотрел на время правки журналов — и ошибался
в обе стороны. Журнал коротких сделок пуст, его никто не трогает, и он
выглядит заброшенным, хотя система исправно ходит каждые четыре часа.
А журнал прорывов выглядел живым, потому что его обновлял чужой шаг,
хотя сами прорывы пять суток не выполнялись.

Значит, различать надо не «файл менялся», а «система прошла». Каждый
запуск отмечается здесь, и тогда молчание означает ровно одно: система
не запускалась. Именно эту беду мы и проглядели.
"""

from __future__ import annotations

import json
import os
import time

БАЗА = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ПАПКА = os.path.join(БАЗА, "state")
ФАЙЛ = os.path.join(ПАПКА, "heartbeat.json")


def отметиться(система: str, событий: int = 0) -> None:
    """Вызывается в конце каждого прохода, чем бы он ни кончился."""
    os.makedirs(ПАПКА, exist_ok=True)
    д = прочитать()
    д[система] = {"когда": time.time(), "событий": событий}
    with open(ФАЙЛ, "w", encoding="utf-8") as f:
        json.dump(д, f, ensure_ascii=False, indent=1)


def прочитать() -> dict:
    if not os.path.exists(ФАЙЛ):
        return {}
    try:
        with open(ФАЙЛ, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
