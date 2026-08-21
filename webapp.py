"""
Личный кабинет: локальный веб-сервер с входом по паролю.

Почему локальный, а не в интернете: страница показывает ваш капитал и позиции.
Бесплатный хостинг GitHub Pages отдаёт статику всем подряд и логина не умеет.
Здесь сервер работает на вашем компьютере, данные никуда не уходят,
а вход по паролю защищает от чужих глаз в той же сети.

Запуск:  python webapp.py
Затем открыть в браузере:  http://localhost:8765

Пароль задаётся в secrets.local.json. Если его там нет — при первом запуске
будет создан и показан в консоли один раз.
"""

from __future__ import annotations

import hashlib
import hmac
import http.server
import json
import os
import secrets as _secrets
import socketserver
import threading
import time
import urllib.parse
from http import HTTPStatus

from engine import (breakout, data, decisions, exchange, journal, longterm,
                    render, risk, shadow, signals)

БАЗА = os.path.dirname(os.path.abspath(__file__))
ПОРТ = 8765
ЖИЗНЬ_СЕССИИ = 12 * 3600
КЕШ_АНАЛИЗА_СЕК = 300          # не пересчитывать чаще раза в 5 минут

_сессии: dict[str, float] = {}
_кеш: dict = {"время": 0.0, "данные": None}
_замок = threading.Lock()


# ------------------------------------------------------------------ пароль

def _путь_секретов() -> str:
    return os.path.join(БАЗА, "secrets.local.json")


def читать_секреты() -> dict:
    with open(_путь_секретов(), encoding="utf-8") as f:
        return json.load(f)


def хеш(пароль: str, соль: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", пароль.encode(), соль.encode(), 200_000).hex()


def обеспечить_пароль() -> None:
    """Если пароля ещё нет — создаём случайный и показываем один раз."""
    с = читать_секреты()
    if с.get("веб_пароль_хеш") and с.get("веб_пароль_соль"):
        return
    пароль = _secrets.token_urlsafe(9)
    соль = _secrets.token_hex(16)
    с["веб_пароль_соль"] = соль
    с["веб_пароль_хеш"] = хеш(пароль, соль)
    с["_веб_комментарий"] = ("Пароль хранится только как хеш — восстановить его нельзя. "
                             "Чтобы сменить: удалите оба поля веб_пароль_* и перезапустите.")
    with open(_путь_секретов(), "w", encoding="utf-8") as f:
        json.dump(с, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 58)
    print("  СОЗДАН ПАРОЛЬ ДЛЯ ВХОДА В КАБИНЕТ")
    print(f"  Логин не нужен. Пароль:  {пароль}")
    print("  Запишите его — второй раз показан не будет.")
    print("=" * 58 + "\n")


def пароль_верен(введённый: str) -> bool:
    с = читать_секреты()
    ожидаемый = с.get("веб_пароль_хеш", "")
    соль = с.get("веб_пароль_соль", "")
    if not ожидаемый:
        return False
    return hmac.compare_digest(хеш(введённый, соль), ожидаемый)


# ------------------------------------------------------------------ данные

def конфиг() -> dict:
    with open(os.path.join(БАЗА, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def ожидание(плечо: float = 1.0) -> dict:
    """Чего реально ожидать — по измеренной статистике, а не по надеждам.

    Числа берутся из результата бэктеста (trades.json). Если его нет,
    честнее сказать «неизвестно», чем показать красивый прогноз.
    """
    пусто = {"сделок": 0, "доля_побед": 0.0, "средний_R": 0.0, "просадка_R": 0.0,
             "сделок_в_месяц": 0, "порог_побед": 45.0,
             "вывод": "Бэктест не проводился — ожидаемый результат неизвестен."}
    путь = os.path.join(БАЗА, "trades.json")
    if not os.path.exists(путь):
        return пусто
    try:
        with open(путь, encoding="utf-8") as f:
            сделки = json.load(f)
    except Exception:
        return пусто
    if not сделки:
        return пусто

    R = [с["r"] for с in сделки]
    победы = [r for r in R if r > 0]
    проигрыши = [r for r in R if r <= 0]
    накоп = пик = просадка = 0.0
    for r in R:
        накоп += r
        пик = max(пик, накоп)
        просадка = min(просадка, накоп - пик)

    средний = sum(R) / len(R)
    cfg_ = конфиг()

    # Плата за фондирование. На споте её нет вовсе; на фьючерсах списывается
    # каждые 8 часов с ОБЪЁМА позиции, а не с плеча — поэтому 2x и 10x платят
    # одинаково, если объём одинаковый. Разница только между «без плеча»
    # и «с плечом», и вот она реальная.
    фондирование_R = 0.0
    if плечо > 1:
        ставка = cfg_["риск"].get("фондирование_за_8ч_пр", 0.01) / 100
        средний_стоп_отн = 0.02          # типичная дистанция до стопа, 2%
        часов_в_сделке = 12 * 4          # средние 12 баров по 4 часа
        объём_на_риск = 1 / средний_стоп_отн
        фондирование_R = ставка * (часов_в_сделке / 8) * объём_на_риск
        средний -= фондирование_R

    цель_R = cfg_["риск"]["рабочая_цель_R"]
    издержки_R = 0.125
    порог = (1 + издержки_R) / (1 + цель_R) * 100

    # Период истории: 1000 баров минус разогрев, переведённые в месяцы.
    месяцев = (1000 - 240) * 4 / 24 / 30.4
    в_месяц = round(len(R) / месяцев)

    if средний > 0.02:
        вывод = ("По этой выборке система в плюсе, но выборка мала — "
                 "относиться к числу как к оценке, а не к обещанию.")
    elif средний > -0.02:
        вывод = ("Ожидаемый результат неотличим от нуля: система пока не "
                 "зарабатывает и не теряет. Это не повод вкладывать деньги — "
                 "это повод накопить статистику бумажной торговли.")
    else:
        вывод = ("Ожидаемый результат отрицательный. Увеличение риска "
                 "ускорит потерю, а не приблизит доход.")

    return {"сделок": len(R), "доля_побед": len(победы) / len(R) * 100,
            "средний_R": средний, "просадка_R": просадка,
            "сделок_в_месяц": max(в_месяц, 1), "порог_побед": порог,
            # Средний выигрыш и средний проигрыш по отдельности: без них
            # непонятно, откуда берётся отрицательное среднее — кажется,
            # будто система теряет в каждой сделке, а это не так.
            "средний_выигрыш_R": (sum(победы) / len(победы)) if победы else 0.0,
            "средний_проигрыш_R": (sum(проигрыши) / len(проигрыши)) if проигрыши else 0.0,
            "побед_из_100": round(len(победы) / len(R) * 100),
            "фондирование_R": фондирование_R, "плечо": плечо,
            "вывод": вывод}


_кеш_прорывы: dict = {"время": 0.0, "данные": None}


def сводный_счёт() -> dict:
    """Что открыто и закрыто по всем трём системам сразу, включая тени."""
    итог = {"открыто": 0, "закрыто": 0, "разделы": []}

    def добавить(имя: str, открыто: int, закрыто: int, пояснение: str) -> None:
        итог["открыто"] += открыто
        итог["закрыто"] += закрыто
        итог["разделы"].append({"имя": имя, "открыто": открыто,
                                "закрыто": закрыто, "пояснение": пояснение})

    к = journal.статистика()
    добавить("Короткие — рекомендации", к["ожидают"] + к["открыты"], к["закрыто"],
             "сигналы, прошедшие все фильтры")

    св = shadow.сводка()
    т = св["отсеянные"]
    добавить("Короткие — тень", т["ждут"] + т["в_работе"], т["закрыто"],
             "отсеянные фильтрами, ведутся для сравнения")

    путь = os.path.join(БАЗА, "state", "breakouts.json")
    рек_о = рек_з = тень_о = тень_з = 0
    if os.path.exists(путь):
        try:
            with open(путь, encoding="utf-8") as f:
                for п in json.load(f).get("позиции", []):
                    закрыта = п.get("состояние") == "закрыта"
                    if п.get("тень"):
                        тень_з += закрыта
                        тень_о += not закрыта
                    else:
                        рек_з += закрыта
                        рек_о += not закрыта
        except Exception:
            pass
    добавить("Прорывы — рекомендации", рек_о, рек_з, "в пределах лимита позиций")
    добавить("Прорывы — тень", тень_о, тень_з, "сверх лимита, для проверки лимита")

    путь = os.path.join(БАЗА, "state", "paper_long.json")
    д_о = д_з = 0
    if os.path.exists(путь):
        try:
            with open(путь, encoding="utf-8") as f:
                for п in json.load(f).get("позиции", []):
                    if п.get("состояние") == "закрыта":
                        д_з += 1
                    else:
                        д_о += 1
        except Exception:
            pass
    добавить("Долгосрочные", д_о, д_з, "покупки на месяцы")
    return итог


def прорывы_обзор() -> dict:
    """Третья система: прорывы. Кешируется на час — дневная свеча
    меняется раз в сутки, чаще пересчитывать нечего."""
    if (_кеш_прорывы["данные"] is not None
            and time.time() - _кеш_прорывы["время"] < 3600):
        return _кеш_прорывы["данные"]

    cfg = конфиг()
    сигналы = []
    for актив in cfg["активы"]:
        try:
            свечи = data.загрузить(актив, "1d", лимит=120,
                                   источники=cfg["источник_данных"])
            в = breakout.проанализировать_прорыв(свечи, cfg)
        except Exception:
            continue
        сигналы.append({
            "актив": актив, "цена": в.цена, "вывод": в.вывод,
            "максимум_окна": в.максимум_окна,
            "до_прорыва_проц": в.до_прорыва_проц,
            "вход": в.вход, "стоп": в.стоп, "стоп_проц": в.стоп_проц,
            "уровень_выхода": в.уровень_выхода,
            "словами": breakout.словами(в),
        })

    # Открытые бумажные позиции этой системы
    путь = os.path.join(БАЗА, "state", "breakouts.json")
    позиции = []
    if os.path.exists(путь):
        try:
            with open(путь, encoding="utf-8") as f:
                позиции = json.load(f).get("позиции", [])
        except Exception:
            позиции = []

    д = {"сигналы": сигналы, "позиции": позиции,
         "настройки": cfg.get("прорывы", {})}
    _кеш_прорывы.update({"время": time.time(), "данные": д})
    return д


_кеш_долгий: dict = {"время": 0.0, "данные": None}


def долгосрочный_обзор() -> list[dict]:
    """Отдельная система — дневной таймфрейм, покупка на месяцы.
    Кешируется на час: дневная свеча меняется раз в сутки."""
    if (_кеш_долгий["данные"] is not None
            and time.time() - _кеш_долгий["время"] < 3600):
        return _кеш_долгий["данные"]

    cfg = конфиг()
    вышло = []
    for актив in cfg["активы"]:
        try:
            свечи = data.загрузить(актив, "1d", лимит=400,
                                   источники=cfg["источник_данных"])
            в = longterm.проанализировать_долгий(свечи, cfg)
        except Exception:
            continue
        вышло.append({
            "актив": актив, "цена": в.цена, "вывод": в.вывод,
            "выполнено": в.выполнено, "всего_условий": len(в.условия),
            "выше_ma200": в.выше_ma200, "adx": round(в.adx, 1),
            "rsi": round(в.rsi, 1),
            "просадка_от_максимума": round(в.просадка_от_максимума, 1),
            "вход": в.вход, "стоп": в.стоп, "стоп_проц": в.стоп_проц,
            "выход_по": в.выход_по, "горизонт": в.горизонт,
            "словами": longterm.словами(в),
            "условия": [{"имя": и, "ок": о, "факт": ф} for и, о, ф in в.условия],
        })
    _кеш_долгий.update({"время": time.time(), "данные": вышло})
    return вышло


def демо_сигнал(цена_btc: float, cfg: dict) -> dict:
    """Учебный сигнал для знакомства с интерфейсом.

    Строится от настоящей цены BTC, чтобы числа выглядели правдоподобно,
    но помечен как демо во всех местах: в данных, в карточке и в журнале
    (идентификатор начинается с DEMO-). Спутать с настоящим невозможно.
    """
    вход = round(цена_btc * 0.995, 2)          # чуть ниже рынка, лимитный ордер
    стоп = round(вход * 0.978, 2)              # 2.2% — типичная дистанция
    риск = вход - стоп
    цель = round(вход + риск * cfg["риск"]["рабочая_цель_R"], 2)
    return {
        "актив": "BTCUSDT", "цена": цена_btc, "демо": True,
        "режим": "боковик", "волатильность": "нормальная",
        "пояснение": "учебный пример", "adx": 18.0, "перцентиль": 45,
        "вывод": signals.СИГНАЛ, "почему": "учебный пример",
        "мин_объём": 5.0, "шаг": 0.00001,
        "сделка": {
            "направление": "Long", "уверенность": "средняя",
            "вход": вход, "стоп": стоп, "цель": цель,
            "тип_ордера": "Limit", "размер": 0.0, "объём": 0.0,
            "риск_денег": 0.0, "риск_проц": 0.0,
            "дистанция_проц": (вход - стоп) / вход * 100,
            "rr": cfg["риск"]["рабочая_цель_R"], "издержки": 0.0,
            "ликвидация": None, "тег": "demo_range_long",
            "подтверждения": ["Цена у нижней границы диапазона: в боковике границы "
                              "чаще отрабатывают как отбой",
                              "RSI в зоне перепроданности: возможен отбой вверх"],
        },
    }


def разобрать_всё(капитал: float, риск_пр: float, плечо: float) -> dict:
    """Считает состояние по всем монетам. Тяжёлая операция — кешируется."""
    cfg = конфиг()
    cfg["риск"] = dict(cfg["риск"])
    cfg["риск"]["депозит"] = капитал
    cfg["риск"]["стартовый_риск_на_сделку_пр"] = риск_пр
    cfg["риск"]["макс_риск_на_сделку_пр"] = max(риск_пр, cfg["риск"]["макс_риск_на_сделку_пр"])
    cfg["риск"]["макс_плечо"] = плечо

    try:
        лимиты = exchange.загрузить_лимиты(cfg["активы"])
    except Exception:
        лимиты = {}

    монеты = []
    for актив in cfg["активы"]:
        try:
            свечи = data.загрузить(актив, cfg["таймфрейм"], лимит=400,
                                   источники=cfg["источник_данных"])
            флаги = data.проверить_качество(
                свечи, cfg["сигналы"]["макс_возраст_данных_баров"])
            а = signals.проанализировать(свечи, cfg, флаги, депозит=капитал)
        except Exception as e:
            монеты.append({"актив": актив, "ошибка": f"{type(e).__name__}"})
            continue

        запись = {
            "актив": актив,
            "цена": а.цена,
            "режим": а.режим.направление,
            "волатильность": а.режим.волатильность,
            "пояснение": а.режим.пояснение,
            "adx": round(а.режим.adx, 1),
            "перцентиль": round(а.режим.bbw_перцентиль),
            "вывод": а.вывод,
            # Причины показываются человеку, поэтому проходят через тот же
            # словарь упрощений, что и сообщения в Telegram: без ссылок
            # на разделы промта и без индикаторной латыни.
            "почему": render.человеку(а.причины_отказа),
            "мин_объём": лимиты.get(актив, {}).get("мин_объём", 5.0),
            "шаг": лимиты.get(актив, {}).get("шаг_количества", 0.0),
        }

        if а.вывод == signals.СИГНАЛ and а.план:
            p = а.план
            ликв = risk.цена_ликвидации(а.направление, p.вход, плечо)
            запись["сделка"] = {
                "направление": а.направление,
                "уверенность": а.уверенность,
                "вход": p.вход, "стоп": p.стоп, "цель": p.цели[0],
                "тип_ордера": p.тип_ордера,
                "размер": p.размер, "объём": p.notional,
                "риск_денег": p.риск_валюта,
                "риск_проц": p.риск_пр_депозита,
                "дистанция_проц": p.дистанция_пр,
                "rr": p.rr_по_целям[0] if p.rr_по_целям else 0,
                "издержки": p.издержки,
                "ликвидация": ликв,
                "подтверждения": [f"{x.название}: {x.интерпретация}"
                                  for x in а.подтверждения],
                "тег": а.тег_стратегии,
            }
        монеты.append(запись)

    доступно = sum(1 for м in монеты if "ошибка" not in м)
    сигналов = sum(1 for м in монеты if м.get("сделка"))
    return {
        "обновлено": time.strftime("%H:%M:%S"),
        "капитал": капитал, "риск": риск_пр, "плечо": плечо,
        "риск_денег": капитал * риск_пр / 100,
        "монет": доступно, "сигналов": сигналов,
        "монеты": монеты,
        "ожидание": ожидание(плечо),
        "журнал": journal.статистика(),
        # Сводный счёт по ВСЕМ системам. Без него кабинет показывал только
        # короткую систему, и при 25 открытых позициях выглядело, будто
        # ничего не происходит.
        "счёт": сводный_счёт(),
        "долгосрочные": долгосрочный_обзор(),
        "прорывы": прорывы_обзор(),
        "долгая_статистика": {"побед_проц": 18, "средний_выигрыш_R": 3.95,
                              "средний_проигрыш_R": -0.29, "сделок": 87,
                              "риск_пр": конфиг()["долгосрочная"]["риск_на_позицию_пр"]},
    }


def данные(капитал: float, риск: float, плечо: float, обновить: bool) -> dict:
    with _замок:
        ключ = (капитал, риск, плечо)
        свежо = (_кеш["данные"] is not None
                 and _кеш.get("ключ") == ключ
                 and time.time() - _кеш["время"] < КЕШ_АНАЛИЗА_СЕК)
        if свежо and not обновить:
            return _кеш["данные"]
        д = разобрать_всё(капитал, риск, плечо)
        _кеш.update({"время": time.time(), "данные": д, "ключ": ключ})
        return д


# ------------------------------------------------------------------ сервер

def страница_входа(ошибка: str = "") -> bytes:
    блок = f'<p class="ошибка">{ошибка}</p>' if ошибка else ""
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Вход — торговый ассистент</title>{СТИЛИ}</head><body class="центр">
<form method="post" action="/login" class="карточка вход">
  <h1>Торговый ассистент</h1>
  <p class="тихо">Введите пароль из консоли при первом запуске</p>
  {блок}
  <input type="password" name="password" placeholder="Пароль" autofocus required>
  <button type="submit">Войти</button>
</form></body></html>""".encode()


СТИЛИ = """<style>
:root{--фон:#0f1115;--блок:#181b22;--рамка:#262b36;--текст:#e6e8ec;--тихо:#8b93a3;
--зел:#3fb950;--крас:#f85149;--жёлт:#d29922;--син:#4493f8}
*{box-sizing:border-box}
body{margin:0;background:var(--фон);color:var(--текст);
font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;padding:20px}
body.центр{display:flex;align-items:center;justify-content:center;min-height:100vh}
h1{font-size:20px;margin:0 0 4px}
h2{font-size:16px;margin:26px 0 12px;font-weight:600}
.тихо{color:var(--тихо);font-size:13px;margin:4px 0}
.ошибка{color:var(--крас);font-size:13px}
.карточка{background:var(--блок);border:1px solid var(--рамка);
border-radius:10px;padding:18px;margin-bottom:14px}
.вход{width:320px}
input,select,button{font:inherit;padding:9px 11px;border-radius:7px;
border:1px solid var(--рамка);background:#11141a;color:var(--текст);width:100%}
button{background:var(--син);border-color:var(--син);color:#fff;
cursor:pointer;font-weight:600;margin-top:10px}
button:hover{opacity:.9}
.панель{display:flex;gap:14px;flex-wrap:wrap;align-items:end}
.поле{flex:1;min-width:120px}
.поле label{display:block;font-size:12px;color:var(--тихо);margin-bottom:5px}
.шапка{display:flex;justify-content:space-between;align-items:center;
flex-wrap:wrap;gap:10px;margin-bottom:16px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:var(--тихо);font-weight:500;font-size:12px;
padding:8px 10px;border-bottom:1px solid var(--рамка)}
td{padding:9px 10px;border-bottom:1px solid #1e222b}
tr:last-child td{border-bottom:none}
.знак{display:inline-block;padding:2px 8px;border-radius:20px;font-size:12px;font-weight:600}
.вверх{background:rgba(63,185,80,.14);color:var(--зел)}
.вниз{background:rgba(248,81,73,.14);color:var(--крас)}
.бок{background:rgba(139,147,163,.14);color:var(--тихо)}
.хаос{background:rgba(210,153,34,.16);color:var(--жёлт)}
.сделка{border-left:3px solid var(--син)}
.сетка{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.цифра{font-size:22px;font-weight:600}
.мелко{font-size:12px;color:var(--тихо)}
.шаги{margin:10px 0 0;padding-left:20px}
.шаги li{margin-bottom:5px}
.обёртка{overflow-x:auto}
a{color:var(--син)}
.метка{display:block;font-size:12px;color:var(--тихо);margin-bottom:8px}
.чипы{display:flex;gap:8px;flex-wrap:wrap}
.чип{width:auto;padding:8px 16px;background:#11141a;border:1px solid var(--рамка);
color:var(--текст);cursor:pointer;margin:0;font-weight:500}
.чип.активный{background:var(--син);border-color:var(--син);color:#fff}
.сетка3{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px;margin:8px 0}
.узкая td,.узкая th{padding:6px 10px;font-size:13px}
.подсветка{background:rgba(68,147,248,.10)}
td.вверх{color:var(--зел)}td.вниз{color:var(--крас)}
.цифра.вверх{color:var(--зел)}.цифра.вниз{color:var(--крас)}
.вкладки{display:flex;gap:8px;margin:26px 0 4px;border-bottom:1px solid var(--рамка)}
.вкладка{width:auto;margin:0;padding:10px 18px;background:transparent;border:none;
border-bottom:2px solid transparent;color:var(--тихо);font-weight:600;border-radius:0}
.вкладка.активная{color:var(--текст);border-bottom-color:var(--син)}
.вкладка:hover{color:var(--текст)}
.решение{display:flex;align-items:center;gap:10px;flex-wrap:wrap;
margin:14px 0;padding:12px;background:#11141a;border-radius:8px;
border:1px solid var(--рамка)}
.решение button{width:auto;margin:0;padding:8px 18px;font-size:14px}
.кнопка-взял{background:var(--зел);border-color:var(--зел)}
.кнопка-пропустил{background:transparent;border-color:var(--рамка);color:var(--тихо)}
.кнопка-пропустил:hover{color:var(--текст)}
.ссылка-журнал{display:inline-block;padding:8px 14px;background:var(--блок);
border:1px solid var(--рамка);border-radius:7px;text-decoration:none;font-size:14px}
.ссылка-журнал:hover{border-color:var(--син)}
span.вверх{color:var(--зел)}span.вниз{color:var(--крас)}
.плашка-демо{background:rgba(210,153,34,.16);color:var(--жёлт);
padding:10px 12px;border-radius:7px;font-size:13px;margin-bottom:12px;
border:1px solid rgba(210,153,34,.35)}
.карточка.демо{border-left-color:var(--жёлт)}
.кнопка-демо{background:transparent;border-color:var(--рамка);color:var(--тихо)}
.кнопка-демо:hover{color:var(--текст);border-color:var(--жёлт)}
.мини{width:auto;margin:0 4px 0 0;padding:5px 12px;font-size:12px;border-radius:6px}
.мини.серая{background:transparent;border-color:var(--рамка);color:var(--тихо)}
.мини.серая:hover{color:var(--текст)}
td.действия{white-space:nowrap}
</style>"""


def _страница(имя: str) -> bytes:
    """Читает шаблон и подставляет стили. Шаблоны читаются на каждый запрос —
    так правку в вёрстке видно сразу, без перезапуска сервера."""
    путь = os.path.join(БАЗА, "templates", имя)
    with open(путь, encoding="utf-8") as f:
        return f.read().replace("__СТИЛИ__", СТИЛИ).encode()


def страница_журнала() -> bytes:
    return _страница("journal.html")


def страница_кабинета() -> bytes:
    """Страница живёт в templates/cabinet.html — так её проще править,
    не трогая серверный код."""
    путь = os.path.join(БАЗА, "templates", "cabinet.html")
    with open(путь, encoding="utf-8") as f:
        html = f.read()
    return html.replace("__СТИЛИ__", СТИЛИ).encode()


class Обработчик(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass                      # не засорять консоль

    # -- вспомогательное --
    def _куки(self) -> dict:
        сырое = self.headers.get("Cookie", "")
        пары = [ч.strip().split("=", 1) for ч in сырое.split(";") if "=" in ч]
        return {к: v for к, v in пары}

    def _авторизован(self) -> bool:
        т = self._куки().get("session", "")
        истекает = _сессии.get(т)
        if истекает and истекает > time.time():
            return True
        _сессии.pop(т, None)
        return False

    def _ответ(self, тело: bytes, код=HTTPStatus.OK, тип="text/html; charset=utf-8",
               куки: str | None = None):
        self.send_response(код)
        self.send_header("Content-Type", тип)
        self.send_header("Content-Length", str(len(тело)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if куки:
            self.send_header("Set-Cookie", куки)
        self.end_headers()
        self.wfile.write(тело)

    def _редирект(self, куда: str, куки: str | None = None):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", куда)
        self.send_header("Content-Length", "0")
        if куки:
            self.send_header("Set-Cookie", куки)
        self.end_headers()

    # -- маршруты --
    def do_GET(self):
        путь = urllib.parse.urlparse(self.path)

        if путь.path == "/logout":
            т = self._куки().get("session", "")
            _сессии.pop(т, None)
            return self._редирект("/", "session=; Max-Age=0; Path=/")

        if not self._авторизован():
            return self._ответ(страница_входа())

        if путь.path == "/":
            return self._ответ(страница_кабинета())

        if путь.path == "/journal":
            return self._ответ(страница_журнала())

        if путь.path == "/api/decisions":
            q = urllib.parse.parse_qs(путь.query)
            от = float(q.get("from", ["0"])[0])
            до = float(q.get("to", ["99999999999"])[0])
            все = decisions.все()
            # Фильтр по времени РЕШЕНИЯ, а не закрытия: пользователь думает
            # категориями «что я решил на этой неделе».
            в_окне = [р for р in все if от <= р.когда <= до]
            тело = json.dumps({
                "решения": [
                    {**{к: v for к, v in р.__dict__.items()}} for р in в_окне
                ],
                "статистика": decisions.статистика(в_окне),
                "всего_за_всё_время": len(все),
            }, ensure_ascii=False, default=str).encode()
            return self._ответ(тело, тип="application/json; charset=utf-8")

        if путь.path == "/api":
            q = urllib.parse.parse_qs(путь.query)
            try:
                плечо = min(20.0, max(1.0, float(q.get("leverage", ["1"])[0])))
            except ValueError:
                плечо = 1.0
            # Капитал и риск в расчёте на сервере не нужны: размер позиции
            # пересчитывается в браузере сразу для всех сумм. Сервер отдаёт
            # уровни сделки, они от капитала не зависят.
            капитал, риск = 100.0, 1.0
            обновить = q.get("refresh", ["0"])[0] == "1"
            д = данные(капитал, риск, плечо, обновить)
            if q.get("demo", ["0"])[0] == "1":
                # Копия, чтобы демо-сигнал не осел в кеше и не всплыл потом
                # как настоящий.
                д = dict(д)
                btc = next((м["цена"] for м in д["монеты"]
                            if м["актив"] == "BTCUSDT"), 60000.0)
                д["монеты"] = [демо_сигнал(btc, конфиг())] + list(д["монеты"])
                д["сигналов"] = д["сигналов"] + 1
                д["демо_режим"] = True
            тело = json.dumps(д, ensure_ascii=False).encode()
            return self._ответ(тело, тип="application/json; charset=utf-8")

        self._ответ(b"not found", HTTPStatus.NOT_FOUND, "text/plain")

    def do_POST(self):
        путь = urllib.parse.urlparse(self.path).path

        if путь == "/api/decision/action":
            if not self._авторизован():
                return self._ответ(b'{"ok":false}', HTTPStatus.UNAUTHORIZED,
                                   "application/json")
            длина = int(self.headers.get("Content-Length", 0))
            try:
                д = json.loads(self.rfile.read(длина).decode("utf-8-sig",
                                                             errors="replace"))
                ок, текст = decisions.действие(
                    д["id"], д["action"], д.get("price"))
                ответ = {"ok": ок, "текст": текст}
            except Exception as e:
                ответ = {"ok": False, "текст": f"{type(e).__name__}: {e}"}
            return self._ответ(json.dumps(ответ, ensure_ascii=False).encode(),
                               тип="application/json; charset=utf-8")

        if путь == "/api/decision":
            if not self._авторизован():
                return self._ответ(b'{"ok":false}', HTTPStatus.UNAUTHORIZED,
                                   "application/json")
            длина = int(self.headers.get("Content-Length", 0))
            try:
                данные = json.loads(self.rfile.read(длина).decode("utf-8-sig",
                                                                 errors="replace"))
                р = decisions.записать(decisions.из_запроса(данные))
                ответ = {"ok": True, "состояние": р.состояние, "решение": р.решение}
            except Exception as e:
                # Текст ошибки нужен в ответе: без него отладка превращается
                # в угадывание, а это локальный сервер только для владельца.
                ответ = {"ok": False, "ошибка": f"{type(e).__name__}: {e}"}
            return self._ответ(json.dumps(ответ, ensure_ascii=False).encode(),
                               тип="application/json; charset=utf-8")

        if путь != "/login":
            return self._ответ(b"not found", HTTPStatus.NOT_FOUND, "text/plain")
        длина = int(self.headers.get("Content-Length", 0))
        сырое = self.rfile.read(длина)
        # Тело может прийти с BOM или в чужой кодировке — падать на этом нельзя,
        # иначе форма входа просто обрывает соединение без объяснения.
        поля = urllib.parse.parse_qs(сырое.decode("utf-8-sig", errors="replace"))
        введённый = поля.get("password", [""])[0]

        time.sleep(0.4)           # притормаживает перебор пароля
        if not пароль_верен(введённый):
            return self._ответ(страница_входа("Неверный пароль"), HTTPStatus.UNAUTHORIZED)

        токен = _secrets.token_urlsafe(24)
        _сессии[токен] = time.time() + ЖИЗНЬ_СЕССИИ
        self._редирект("/", f"session={токен}; Path=/; HttpOnly; SameSite=Strict; "
                            f"Max-Age={ЖИЗНЬ_СЕССИИ}")


class Сервер(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def задать_пароль(новый: str) -> None:
    """Смена пароля из командной строки: python webapp.py --пароль мойпароль"""
    if len(новый) < 6:
        print("Пароль короче 6 знаков — так нельзя.")
        raise SystemExit(1)
    с = читать_секреты()
    соль = _secrets.token_hex(16)
    с["веб_пароль_соль"] = соль
    с["веб_пароль_хеш"] = хеш(новый, соль)
    with open(_путь_секретов(), "w", encoding="utf-8") as f:
        json.dump(с, f, ensure_ascii=False, indent=2)
    print(f"Пароль изменён на: {новый}")


if __name__ == "__main__":
    import sys
    if "--пароль" in sys.argv:
        задать_пароль(sys.argv[sys.argv.index("--пароль") + 1])
        raise SystemExit(0)
    обеспечить_пароль()
    print(f"Кабинет запущен.  Откройте в браузере:  http://localhost:{ПОРТ}")
    print("Остановить — Ctrl+C\n")
    with Сервер(("127.0.0.1", ПОРТ), Обработчик) as сервер:
        try:
            сервер.serve_forever()
        except KeyboardInterrupt:
            print("\nОстановлено.")
