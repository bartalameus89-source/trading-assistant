"""
Выгрузка журналов в CSV: для налоговой, для таблицы, для чужого разбора.

Зачем. Всё состояние лежит в JSON — удобно машине, неудобно человеку.
Открыть в Excel, отсортировать, посчитать по-своему или отдать бухгалтеру
сейчас нельзя никак.

Что выгружается. Все закрытые и открытые сделки всех трёх систем одной
таблицей, с общими колонками: когда, что, какая система, рекомендация
или тень, вход, выход, результат в R и в деньгах, срок, версия настроек.
Версия важна: без неё через месяц строки разных правил смешаются
и средние по ним будут числом, которого не существовало.

    python export.py                  в файл export/сделки-ГГГГ-ММ-ДД.csv
    python export.py --путь моё.csv   в указанный файл
"""

from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime, timezone

БАЗА = os.path.dirname(os.path.abspath(__file__))
ПАПКА = os.path.join(БАЗА, "state")
ВЫГРУЗКА = os.path.join(БАЗА, "export")

КОЛОНКИ = [
    "открыта", "закрыта", "система", "тип", "монета", "направление",
    "состояние", "исход", "вход", "цена_выхода", "стоп_начальный",
    "результат_R", "результат_денег", "риск_денег", "дней", "частей",
    "версия_настроек",
]


def _когда(t) -> str:
    if not t:
        return ""
    return datetime.fromtimestamp(float(t), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _читать(файл: str, ключ: str) -> list[dict]:
    п = os.path.join(ПАПКА, файл)
    if not os.path.exists(п):
        return []
    try:
        with open(п, encoding="utf-8") as f:
            return json.load(f).get(ключ, []) or []
    except (json.JSONDecodeError, OSError):
        return []


def строки() -> list[dict]:
    из: list[dict] = []

    def добавить(з: dict, система: str, r_поле: str, риск_поле: str,
                 вход_поле: str = "вход") -> None:
        r = з.get(r_поле)
        риск = з.get(риск_поле)
        части = з.get("части") or []
        из.append({
            "открыта": _когда(з.get("открыта_в") or з.get("создана") or з.get("когда")),
            "закрыта": _когда(з.get("закрыта_в")),
            "система": система,
            "тип": "тень" if з.get("тень") else "рекомендация",
            "монета": (з.get("актив") or "").replace("USDT", ""),
            "направление": з.get("направление", "Long"),
            "состояние": з.get("состояние", ""),
            "исход": з.get("исход") or "",
            "вход": з.get(вход_поле) or з.get("вход"),
            "цена_выхода": з.get("выход") or з.get("цена_выхода") or "",
            "стоп_начальный": з.get("стоп_начальный") or з.get("стоп") or "",
            "результат_R": round(r, 4) if isinstance(r, (int, float)) else "",
            "результат_денег": (round(r * риск, 4)
                                if isinstance(r, (int, float))
                                and isinstance(риск, (int, float)) else ""),
            "риск_денег": риск if isinstance(риск, (int, float)) else "",
            "дней": з.get("дней", ""),
            "частей": len(части) if части else 1,
            "версия_настроек": з.get("настройки", ""),
        })

    for з in _читать("breakouts.json", "позиции"):
        добавить(з, "прорывы", "r", "риск_usd")
    for з in _читать("paper_long.json", "позиции"):
        добавить(з, "долгосрочная", "r", "риск_usd")
    for з in _читать("paper.json", "сделки"):
        добавить(з, "короткая", "результат_R", "риск_денег", "цена_входа")
    for з in _читать("shadow.json", "сделки"):
        з = {**з, "тень": True}
        добавить(з, "короткая", "результат_R", "риск_денег", "цена_входа")
    for з in _читать("decisions.json", "решения"):
        з = {**з, "тень": з.get("решение") == "пропустил"}
        добавить(з, "мои решения", "результат_R", "риск_денег", "цена_входа")

    из.sort(key=lambda с: с["открыта"] or "")
    return из


def главное() -> int:
    данные = строки()
    if "--путь" in sys.argv:
        куда = sys.argv[sys.argv.index("--путь") + 1]
    else:
        os.makedirs(ВЫГРУЗКА, exist_ok=True)
        сегодня = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        куда = os.path.join(ВЫГРУЗКА, f"сделки-{сегодня}.csv")

    # utf-8-sig, иначе Excel на Windows покажет кириллицу кракозябрами.
    # Разделитель точка с запятой — по той же причине: русский Excel
    # ждёт его, а запятую считает десятичным знаком.
    with open(куда, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=КОЛОНКИ, delimiter=";")
        w.writeheader()
        w.writerows(данные)

    закрытых = sum(1 for с in данные if с["состояние"] in ("закрыта", "завершена"))
    print(f"Выгружено строк: {len(данные)} (закрытых {закрытых})")
    print(f"Файл: {куда}")
    return 0


if __name__ == "__main__":
    raise SystemExit(главное())
