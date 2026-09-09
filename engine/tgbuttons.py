"""
Кнопки «взял / пропустил» прямо в сообщении Telegram.

Зачем. Журнал решений пустой не потому, что решения не принимаются,
а потому что ради одной кнопки надо открыть кабинет, вспомнить пароль
и найти нужную карточку. Сообщение уже пришло на телефон — кнопка
должна быть в нём.

Как это работает без сервера. GitHub Actions не может принимать вебхуки:
у прохода нет постоянного адреса. Поэтому опрашиваем сами — каждый проход
спрашивает у Telegram, что нажимали с прошлого раза (метод getUpdates
со смещением). Задержка до четырёх часов, но для решения «беру или нет»
это неважно: сигнал живёт шестнадцать часов, и запись делается тем же
временем, когда кнопка была нажата, а не когда мы о ней узнали.

Смещение храним в state/tg_offset.json. Без него Telegram отдаёт одни
и те же нажатия снова и снова, и одна кнопка превращается в шесть записей.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

БАЗА = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ПАПКА = os.path.join(БАЗА, "state")
ФАЙЛ_СМЕЩЕНИЯ = os.path.join(ПАПКА, "tg_offset.json")
ТАЙМАУТ = 20

# Данные кнопки Telegram ограничены 64 байтами, поэтому кладём в них
# только то, без чего не обойтись: что нажали и по какому сигналу.
ВЗЯЛ = "t"
ПРОПУСТИЛ = "s"


def _токен() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")


def _запрос(метод: str, данные: dict) -> dict | None:
    токен = _токен()
    if not токен:
        return None
    тело = urllib.parse.urlencode(данные).encode()
    try:
        with urllib.request.urlopen(
                f"https://api.telegram.org/bot{токен}/{метод}",
                тело, timeout=ТАЙМАУТ) as о:
            return json.loads(о.read())
    except Exception:
        return None


def клавиатура(ид_сигнала: str) -> str:
    """JSON клавиатуры для sendMessage. Идентификатор сигнала кладём
    в данные кнопки, чтобы при нажатии знать, о какой сделке речь."""
    return json.dumps({"inline_keyboard": [[
        {"text": "✅ Взял сделку", "callback_data": f"{ВЗЯЛ}:{ид_сигнала}"[:64]},
        {"text": "⏭ Пропустил", "callback_data": f"{ПРОПУСТИЛ}:{ид_сигнала}"[:64]},
    ]]})


def _смещение() -> int:
    if not os.path.exists(ФАЙЛ_СМЕЩЕНИЯ):
        return 0
    try:
        with open(ФАЙЛ_СМЕЩЕНИЯ, encoding="utf-8") as f:
            return int(json.load(f).get("смещение", 0))
    except (json.JSONDecodeError, OSError, ValueError):
        return 0


def _запомнить_смещение(значение: int) -> None:
    os.makedirs(ПАПКА, exist_ok=True)
    with open(ФАЙЛ_СМЕЩЕНИЯ, "w", encoding="utf-8") as f:
        json.dump({"смещение": значение, "обновлён": time.time()}, f,
                  ensure_ascii=False)


def собрать_нажатия() -> list[dict]:
    """Что нажали с прошлого раза. Возвращает список
    {"решение": "t"|"s", "сигнал": ид, "когда": unix, "чат": id}.

    Смещение двигаем ДО обработки: если обработка упадёт, лучше потерять
    одно нажатие, чем зациклиться на нём и обрабатывать его каждый проход."""
    ответ = _запрос("getUpdates", {"offset": _смещение(), "timeout": 0,
                                   "allowed_updates": '["callback_query"]'})
    if not ответ or not ответ.get("ok"):
        return []

    нажатия: list[dict] = []
    последний = _смещение()
    for о in ответ.get("result", []):
        последний = max(последний, int(о.get("update_id", 0)) + 1)
        зв = о.get("callback_query")
        if not зв:
            continue
        данные = зв.get("data") or ""
        если = данные.split(":", 1)
        if len(если) != 2 or если[0] not in (ВЗЯЛ, ПРОПУСТИЛ):
            continue
        нажатия.append({
            "решение": если[0], "сигнал": если[1],
            "когда": зв.get("message", {}).get("date") or time.time(),
            "чат": зв.get("message", {}).get("chat", {}).get("id"),
            "сообщение": зв.get("message", {}).get("message_id"),
            "id_нажатия": зв.get("id"),
        })
    if последний != _смещение():
        _запомнить_смещение(последний)
    return нажатия


def подтвердить(нажатие: dict, текст: str) -> None:
    """Убрать «часики» с кнопки и показать всплывающее подтверждение.
    Без этого Telegram минуту крутит индикатор, и кажется, что не сработало."""
    if нажатие.get("id_нажатия"):
        _запрос("answerCallbackQuery",
                {"callback_query_id": нажатие["id_нажатия"], "text": текст})


def отметить_в_сообщении(нажатие: dict, текст: str) -> None:
    """Заменить кнопки на строку с принятым решением. Иначе через день
    непонятно, нажимал ты по этому сигналу или нет."""
    if нажатие.get("чат") and нажатие.get("сообщение"):
        _запрос("editMessageReplyMarkup", {
            "chat_id": нажатие["чат"], "message_id": нажатие["сообщение"],
            "reply_markup": json.dumps({"inline_keyboard": [[
                {"text": текст, "callback_data": "x"}]]}),
        })
