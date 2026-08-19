"""
Главный запуск: загрузить данные -> проанализировать -> показать -> отправить в Telegram.

    python run.py              разбор по всем активам из config.json
    python run.py --tg         то же + отправка в Telegram
    python run.py --demo       показать, как выглядит полная карточка сигнала,
                               на искусственном учебном примере (данные ненастоящие)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

from engine import data, render, signals

БАЗА = os.path.dirname(os.path.abspath(__file__))


def конфиг() -> dict:
    with open(os.path.join(БАЗА, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def секреты() -> dict:
    """Сначала переменные окружения — так работает автозапуск в облаке,
    где файла с секретами нет и быть не должно. Потом локальный файл."""
    из_среды = {
        "telegram_bot_token": os.environ.get("TELEGRAM_BOT_TOKEN"),
        "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID"),
    }
    if из_среды["telegram_bot_token"] and из_среды["telegram_chat_id"]:
        return из_среды

    путь = os.path.join(БАЗА, "secrets.local.json")
    if not os.path.exists(путь):
        return {}
    with open(путь, encoding="utf-8") as f:
        return json.load(f)


def отправить_в_телеграм(текст: str) -> bool:
    s = секреты()
    токен, чат = s.get("telegram_bot_token"), s.get("telegram_chat_id")
    if not токен or not чат:
        print("  (Telegram не настроен — запустите python tg_setup.py)")
        return False
    url = f"https://api.telegram.org/bot{токен}/sendMessage"
    тело = urllib.parse.urlencode({
        "chat_id": чат, "text": текст, "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=тело), timeout=20) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception as e:
        print(f"  (ошибка отправки в Telegram: {type(e).__name__}: {e})")
        return False


def разобрать_актив(актив: str, cfg: dict) -> signals.Анализ | None:
    свечи = data.загрузить(актив, cfg["таймфрейм"], лимит=400,
                           источники=cfg["источник_данных"])
    флаги = data.проверить_качество(свечи, cfg["сигналы"]["макс_возраст_данных_баров"])
    return signals.проанализировать(
        свечи, cfg, флаги, депозит=cfg["риск"]["депозит"],
    )


def демонстрация(cfg: dict) -> None:
    """Учебный пример: показывает, как выглядит карточка, когда сигнал ЕСТЬ.
    Данные синтетические — это витрина формата, а не рекомендация."""
    import math
    from datetime import datetime, timezone
    from engine.data import Свечи

    n = 400
    # Псевдослучайный шум без внешних библиотек: линейный конгруэнтный генератор
    # с фиксированной затравкой — демо всегда выглядит одинаково.
    зерно = 20240819
    def шум() -> float:
        nonlocal зерно
        зерно = (зерно * 1103515245 + 12345) % (2 ** 31)
        return зерно / (2 ** 31) - 0.5

    закр = []
    for i in range(n):
        тренд = 100 + i * 0.30                       # устойчивый рост
        волна = math.sin(i / 26) * 7.5               # медленные глубокие откаты
        закр.append(тренд + волна + шум() * 1.1)     # шум, чтобы ADX был реалистичным
    # Учебная ситуация: сильный тренд, цена обновила максимум (сопротивления сверху
    # нет), затем глубокий откат к поддержке — классический вход по тренду.
    закр[-1] = закр[-1] - 7.5

    # Метки времени привязаны к «сейчас», иначе сработает проверка свежести данных.
    шаг_мс = data.ДЛИТЕЛЬНОСТЬ_БАРА_МИН[cfg["таймфрейм"]] * 60 * 1000
    конец = int(datetime.now(timezone.utc).timestamp() * 1000)
    конец -= конец % шаг_мс                          # выравниваем на границу бара

    свечи = Свечи(
        актив="ДЕМО-АКТИВ", таймфрейм=cfg["таймфрейм"], источник="синтетика",
        времена=[конец - (n - 1 - i) * шаг_мс for i in range(n)],
        opens=[c - 0.3 for c in закр],
        highs=[c + 0.9 for c in закр],
        lows=[c - 0.9 for c in закр],
        closes=закр,
        volumes=[1000.0] * (n - 1) + [2100.0],       # всплеск объёма на последнем баре
    )

    print("\n" + "!" * 60)
    print("  ДЕМОНСТРАЦИЯ ФОРМАТА. Данные искусственные, это не сигнал.")
    print("  Цель — показать, как выглядит полная карточка со сделкой.")
    print("!" * 60)

    а = signals.проанализировать(свечи, cfg, флаги_данных=[], депозит=10000.0)
    print(render.консоль(а, валидатор_есть=cfg.get("валидатор_кода", False)))


def главное() -> int:
    cfg = конфиг()
    аргументы = set(sys.argv[1:])

    if "--demo" in аргументы:
        демонстрация(cfg)
        return 0

    слать = "--tg" in аргументы
    сигналов = 0

    for актив in cfg["активы"]:
        try:
            а = разобрать_актив(актив, cfg)
        except Exception as e:
            print(f"\n{актив}: ОШИБКА ЗАГРУЗКИ -> {type(e).__name__}: {e}")
            continue

        print(render.консоль(а, валидатор_есть=cfg.get("валидатор_кода", False)))

        if а.вывод == signals.СИГНАЛ:
            сигналов += 1
        if слать and а.вывод != signals.НЕТ_СИГНАЛА:
            # Молчание — тоже результат, но спамить им в мессенджер незачем.
            # Шлём только сигналы и предупреждения о риске.
            if отправить_в_телеграм(render.телеграм(а)):
                print(f"  -> отправлено в Telegram")

    print(f"\nИтог: сигналов на вход — {сигналов} из {len(cfg['активы'])} активов.")
    if сигналов == 0:
        print("Отсутствие сигналов — нормальный результат, а не сбой.")
    return 0


if __name__ == "__main__":
    raise SystemExit(главное())
