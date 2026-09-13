"""
Свежие журналы с GitHub — для бота на компьютере владельца.

Журналы ведёт автоматика на GitHub, а на компьютере лежит копия, которая
отстаёт, пока не сделан git pull. Боту нужна правда на сейчас, поэтому он
скачивает журналы напрямую из репозитория в отдельную папку state_remote/
(не в state/: там рабочая копия, её трогать нельзя).

Модули движка знают свои файлы по константам ПАПКА и ФАЙЛ. На время ответа
бота они перенаправляются в state_remote/ и потом возвращаются на место —
считают всё те же функции, что у кабинета и недельного отчёта.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

from . import (decisions, equity, heartbeat, journal, pnl, portfolio,
               settings_log, shadow, trades, variants, volzones)

БАЗА = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ПАПКА = os.path.join(БАЗА, "state_remote")
ЖУРНАЛЫ = ["paper.json", "shadow.json", "breakouts.json", "paper_long.json",
           "decisions.json", "equity.json", "heartbeat.json", "variants.json",
           "settings_log.json"]
СВЕЖЕСТЬ_СЕК = 60
_скачано: dict = {"когда": 0.0, "итог": None}


def репозиторий() -> str:
    """owner/repo из .git/config — чтобы не зашивать адрес второй раз."""
    try:
        with open(os.path.join(БАЗА, ".git", "config"), encoding="utf-8") as f:
            текст = f.read()
        м = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?\s", текст)
        if м:
            return м.group(1)
    except OSError:
        pass
    return "bartalameus89-source/trading-assistant"


def _получить(путь: str, токен: str) -> bytes | None:
    """None — файла в репозитории нет (404). Прочие ошибки — исключение."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{репозиторий()}/contents/{путь}?ref=main",
        headers={"Authorization": f"Bearer {токен}",
                 "Accept": "application/vnd.github.raw",
                 "User-Agent": "trading-assistant-bot"})
    try:
        with urllib.request.urlopen(req, timeout=20) as о:
            return о.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def скачать(токен: str, принудительно: bool = False) -> dict:
    """Кладёт журналы в state_remote/. Не чаще раза в минуту: кнопки жмут
    подряд, а журналы на GitHub меняются раз в четыре часа."""
    if (not принудительно and _скачано["итог"]
            and time.time() - _скачано["когда"] < СВЕЖЕСТЬ_СЕК):
        return _скачано["итог"]
    os.makedirs(ПАПКА, exist_ok=True)
    итог = {"скачано": [], "нет": [], "ошибки": []}
    for имя in ЖУРНАЛЫ:
        куда = os.path.join(ПАПКА, имя)
        try:
            тело = _получить(f"state/{имя}", токен)
            if тело is not None:
                json.loads(тело.decode("utf-8"))    # битое не кладём, остальные качаем
        except Exception as e:
            итог["ошибки"].append(f"{имя}: {type(e).__name__}")
            continue
        if тело is None:
            итог["нет"].append(имя)
            if os.path.exists(куда):
                os.remove(куда)
            continue
        with open(куда, "wb") as f:
            f.write(тело)
        итог["скачано"].append(имя)
    _скачано.update(когда=time.time(), итог=итог)
    return итог


@contextmanager
def журналы_из(папка: str = ПАПКА):
    """Перенаправить модули движка в указанную папку на время блока."""
    def п(имя: str) -> str:
        return os.path.join(папка, имя)

    подмена = [
        (decisions, "ПАПКА", папка), (decisions, "ФАЙЛ", п("decisions.json")),
        (equity, "ПАПКА", папка), (equity, "ФАЙЛ", п("equity.json")),
        (heartbeat, "ПАПКА", папка), (heartbeat, "ФАЙЛ", п("heartbeat.json")),
        (journal, "ПАПКА", папка), (journal, "ФАЙЛ", п("paper.json")),
        (pnl, "ПАПКА", папка), (portfolio, "ПАПКА", папка), (trades, "ПАПКА", папка),
        (settings_log, "ПАПКА", папка), (settings_log, "ФАЙЛ", п("settings_log.json")),
        (shadow, "ПАПКА", папка), (shadow, "ФАЙЛ", п("shadow.json")),
        (variants, "ФАЙЛ", п("variants.json")), (volzones, "ФАЙЛ", п("shadow.json")),
    ]
    прежние = [(м, имя, getattr(м, имя)) for м, имя, _ in подмена]
    try:
        for м, имя, значение in подмена:
            setattr(м, имя, значение)
        yield папка
    finally:
        for м, имя, значение in прежние:
            setattr(м, имя, значение)
