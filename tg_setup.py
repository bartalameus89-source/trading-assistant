"""
Настройка Telegram-бота: проверка токена и автоматическое определение вашего chat_id.

Запуск:  python tg_setup.py

Токен читается из secrets.local.json и НИКОГДА не печатается на экран —
даже частично. Раздел 12 промта: ключи не попадают в вывод, логи и отчёты.
"""

import json
import os
import urllib.error
import urllib.request

БАЗА = os.path.dirname(os.path.abspath(__file__))
ФАЙЛ = os.path.join(БАЗА, "secrets.local.json")


def читать_секреты() -> dict:
    with open(ФАЙЛ, encoding="utf-8") as f:
        return json.load(f)


def писать_секреты(данные: dict) -> None:
    with open(ФАЙЛ, "w", encoding="utf-8") as f:
        json.dump(данные, f, ensure_ascii=False, indent=2)


def api(токен: str, метод: str, параметры: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{токен}/{метод}"
    if параметры:
        url += "?" + urllib.parse.urlencode(параметры)
    req = urllib.request.Request(url, headers={"User-Agent": "trading-assistant/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


import urllib.parse  # noqa: E402  (нужен внутри api)


def главное() -> int:
    секреты = читать_секреты()
    токен = секреты.get("telegram_bot_token")

    if not токен:
        print("В secrets.local.json нет токена.")
        return 1

    # --- Шаг 1: жив ли бот ---
    try:
        ответ = api(токен, "getMe")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print("ТОКЕН НЕ ПРИНЯТ (401). Он неверный или уже отозван.")
            print("Получите новый у @BotFather командой /token и впишите в secrets.local.json.")
        else:
            print(f"Ошибка обращения к Telegram: HTTP {e.code}")
        return 1
    except Exception as e:
        print(f"Нет связи с Telegram: {type(e).__name__}: {e}")
        return 1

    if not ответ.get("ok"):
        print(f"Telegram отклонил запрос: {ответ}")
        return 1

    бот = ответ["result"]
    print("Бот на связи:")
    print(f"  имя      : {бот.get('first_name')}")
    print(f"  username : @{бот.get('username')}")
    print(f"  ссылка   : https://t.me/{бот.get('username')}")
    print()

    # --- Шаг 2: кому слать алерты ---
    if секреты.get("telegram_chat_id"):
        print(f"chat_id уже настроен: {секреты['telegram_chat_id']}")
        print("Отправляю проверочное сообщение...")
        api(токен, "sendMessage", {
            "chat_id": секреты["telegram_chat_id"],
            "text": "Проверка связи. Торговый ассистент подключён.",
        })
        print("Отправлено — посмотрите в Telegram.")
        return 0

    обновления = api(токен, "getUpdates")
    результаты = обновления.get("result", [])

    чаты = {}
    for u in результаты:
        сообщение = u.get("message") or u.get("edited_message") or {}
        чат = сообщение.get("chat")
        if чат and чат.get("type") == "private":
            чаты[чат["id"]] = чат.get("username") or чат.get("first_name") or "без имени"

    if not чаты:
        print("Пока никто не писал боту, поэтому получателя определить не из чего.")
        print()
        print("ЧТО СДЕЛАТЬ:")
        print(f"  1. Откройте https://t.me/{бот.get('username')}")
        print("  2. Нажмите кнопку START (или отправьте /start)")
        print("  3. Запустите этот скрипт ещё раз: python tg_setup.py")
        return 2

    if len(чаты) > 1:
        print("Боту писали несколько человек. Выберите нужный ID вручную:")
        for cid, имя in чаты.items():
            print(f"  {cid}  —  {имя}")
        print("Впишите нужный в secrets.local.json в поле telegram_chat_id.")
        return 2

    chat_id, имя = next(iter(чаты.items()))
    секреты["telegram_chat_id"] = chat_id
    писать_секреты(секреты)
    print(f"Получатель определён: {имя} (chat_id {chat_id}) — сохранено.")

    api(токен, "sendMessage", {
        "chat_id": chat_id,
        "text": (
            "Связь установлена.\n\n"
            "Сюда будут приходить торговые сигналы с разбором: что предлагается, "
            "сколько денег под риском и что будет, если расчёт не оправдается.\n\n"
            "Пока система только считает и ничего не исполняет."
        ),
    })
    print("Проверочное сообщение отправлено — посмотрите в Telegram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(главное())
