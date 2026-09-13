"""
Telegram-бот с меню: вся информация по кнопке.

Как работает. Этот процесс живёт на компьютере владельца (запуск — Бот.bat)
и непрерывно спрашивает Telegram о новых сообщениях. На кнопку меню отвечает
сразу, считая по свежим журналам, скачанным с GitHub (engine/remote.py).

Почему на компьютере, а не на GitHub. Автоматика на GitHub запускается раз
в четыре часа и постоянного адреса не имеет — меню, отвечающее через четыре
часа, бесполезно.

Нажатия «взял / пропустил». Пока бот работает, он забирает у Telegram ВСЕ
обновления, в том числе нажатия под сигналами, — иначе проход на GitHub
не увидел бы их вовсе. Сам бот журнал не пишет (его пишет автоматика), а
запускает workflow «Решение из Telegram» с нажатием: решение попадает
в журнал за пару минут. Когда бот выключен, нажатия, как и раньше,
забирает обычный проход автоматики.

Отвечает только владельцу: сообщения из других чатов молча игнорируются.

    python bot.py
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

from engine import botmenu, remote, tgbuttons

БАЗА = os.path.dirname(os.path.abspath(__file__))
ПОРТ_ЗАМКА = 8767              # только чтобы не запустить бота дважды
ОЖИДАНИЕ = 25                  # длинный опрос Telegram, секунд


def секреты() -> dict:
    with open(os.path.join(БАЗА, "secrets.local.json"), encoding="utf-8") as f:
        return json.load(f)


class Бот:
    """Логика отдельно от сети: в проверках Telegram и GitHub подменяются."""

    def __init__(self, токен: str, чат: str, гитхаб: str,
                 телеграм=None, запустить_решение=None, запуски=None,
                 скачать=None, построить=None):
        self.токен, self.чат, self.гитхаб = токен, str(чат), гитхаб
        self.телеграм = телеграм or self._телеграм
        self.запустить_решение = запустить_решение or self._запустить_решение
        self.запуски = запуски or self._запуски
        self.скачать = скачать or (lambda: remote.скачать(self.гитхаб))
        self.построить = построить or self._построить
        self.смещение = 0

    # --------------------------------------------------------------- сеть
    def _телеграм(self, метод: str, поля: dict, таймаут: int = 30) -> dict | None:
        тело = urllib.parse.urlencode(поля).encode()
        try:
            with urllib.request.urlopen(
                    f"https://api.telegram.org/bot{self.токен}/{метод}",
                    тело, timeout=таймаут) as о:
                return json.loads(о.read())
        except urllib.error.HTTPError as e:
            if e.code == 409:          # проход автоматики как раз спрашивает сам
                return {"ok": False, "конфликт": True}
            return None
        except Exception:
            return None

    def _гитхаб(self, путь: str, данные: dict | None = None) -> dict | int | None:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{remote.репозиторий()}{путь}",
            data=json.dumps(данные).encode() if данные is not None else None,
            headers={"Authorization": f"Bearer {self.гитхаб}",
                     "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json",
                     "User-Agent": "trading-assistant-bot"},
            method="POST" if данные is not None else "GET")
        with urllib.request.urlopen(req, timeout=20) as о:
            тело = о.read()
            return json.loads(тело) if тело else о.status

    def _запустить_решение(self, нажатие: dict) -> bool:
        try:
            self._гитхаб("/actions/workflows/decision.yml/dispatches",
                         {"ref": "main", "inputs": {"payload": json.dumps(нажатие, ensure_ascii=False)}})
            return True
        except Exception as e:
            print(f"  не удалось передать решение на GitHub: {type(e).__name__}: {e}")
            return False

    def _запуски(self) -> list[dict] | None:
        try:
            return self._гитхаб("/actions/workflows/paper.yml/runs?per_page=10")["workflow_runs"]
        except Exception:
            return None

    # --------------------------------------------------------------- ответы
    def отправить(self, текст: str, клавиатура: bool = True) -> None:
        поля = {"chat_id": self.чат, "text": текст[:4000], "parse_mode": "HTML",
                "disable_web_page_preview": "true"}
        if клавиатура:
            поля["reply_markup"] = botmenu.клавиатура()
        self.телеграм("sendMessage", поля)

    def _построить(self, кнопка: str) -> str:
        скачивание = self.скачать()
        with remote.журналы_из():
            if кнопка == botmenu.ИТОГ:
                return botmenu.итог(botmenu.цены())
            if кнопка == botmenu.ПОЗИЦИИ:
                return botmenu.позиции(botmenu.цены())
            if кнопка == botmenu.СДЕЛКИ:
                return botmenu.сделки(botmenu.цены())
            if кнопка == botmenu.СИГНАЛЫ:
                return botmenu.сигналы_сейчас()
            if кнопка == botmenu.ОБУЧЕНИЕ:
                return botmenu.обучение()
            if кнопка == botmenu.СОСТОЯНИЕ:
                return botmenu.состояние(self.запуски(), скачивание)
        return botmenu.помощь()

    def обработать(self, обновление: dict) -> None:
        if обновление.get("callback_query"):
            return self._нажатие(обновление["callback_query"])
        сообщение = обновление.get("message") or {}
        if str(сообщение.get("chat", {}).get("id")) != self.чат:
            return                                      # не владелец — молчим
        текст = (сообщение.get("text") or "").strip()
        кнопки = {к for ряд in botmenu.РАСКЛАДКА for к in ряд}
        if текст not in кнопки:
            self.отправить("Меню внизу экрана 👇 Нажмите нужную кнопку.\n\n"
                           + botmenu.помощь())
            return
        if текст in botmenu.ДОЛГИЕ:
            self.отправить("⏳ Считаю по живому рынку, это до минуты…", клавиатура=False)
        try:
            self.отправить(self.построить(текст))
        except Exception as e:
            traceback.print_exc()
            self.отправить(f"⚠️ Не получилось посчитать: {type(e).__name__}. "
                           f"Попробуйте ещё раз через минуту.")

    def _нажатие(self, зв: dict) -> None:
        сообщение = зв.get("message") or {}
        if str(сообщение.get("chat", {}).get("id")) != self.чат:
            return
        данные = зв.get("data") or ""
        части = данные.split(":", 1)
        if len(части) != 2 or части[0] not in (tgbuttons.ВЗЯЛ, tgbuttons.ПРОПУСТИЛ):
            self.телеграм("answerCallbackQuery", {"callback_query_id": зв.get("id"),
                                                   "text": "Уже записано"})
            return
        взял = части[0] == tgbuttons.ВЗЯЛ
        подпись = "✅ Взял сделку" if взял else "⏭ Пропустил"
        нажатие = {"решение": части[0], "сигнал": части[1],
                   "когда": сообщение.get("date") or time.time(),
                   "чат": сообщение.get("chat", {}).get("id"),
                   "сообщение": сообщение.get("message_id")}
        if self.запустить_решение(нажатие):
            self.телеграм("answerCallbackQuery", {
                "callback_query_id": зв.get("id"),
                "text": f"Принято: {подпись}. Запишется в журнал за пару минут."})
            self.телеграм("editMessageReplyMarkup", {
                "chat_id": нажатие["чат"], "message_id": нажатие["сообщение"],
                "reply_markup": json.dumps({"inline_keyboard": [[
                    {"text": f"⏳ Записываю — {подпись}", "callback_data": "x"}]]},
                    ensure_ascii=False)})
        else:
            # Кнопки не трогаем: нажатие уже забрано у Telegram, и если
            # передать не вышло, пусть владелец нажмёт ещё раз.
            self.телеграм("answerCallbackQuery", {
                "callback_query_id": зв.get("id"), "show_alert": "true",
                "text": "Не удалось записать решение — нет связи с GitHub. "
                        "Нажмите кнопку ещё раз через минуту."})

    # --------------------------------------------------------------- цикл
    def шаг(self) -> int:
        ответ = self.телеграм("getUpdates", {
            "offset": self.смещение, "timeout": ОЖИДАНИЕ,
            "allowed_updates": '["message","callback_query"]'}, таймаут=ОЖИДАНИЕ + 10)
        if not ответ or not ответ.get("ok"):
            time.sleep(3)
            return 0
        обновления = ответ.get("result", [])
        for о in обновления:
            # Смещение двигаем ДО обработки: упавшая обработка не должна
            # зациклить бота на одном сообщении.
            self.смещение = max(self.смещение, int(о.get("update_id", 0)) + 1)
            try:
                self.обработать(о)
            except Exception:
                traceback.print_exc()
        return len(обновления)


def занять_единственный_запуск() -> socket.socket | None:
    """Два бота на одном токене отбирают друг у друга сообщения. Порт на
    127.0.0.1 занимается исключительно — второй запуск получит отказ."""
    с = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name == "nt":
        с.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        с.bind(("127.0.0.1", ПОРТ_ЗАМКА))
        return с
    except OSError:
        с.close()
        return None


def главное() -> int:
    замок = занять_единственный_запуск()
    if замок is None:
        print("Бот уже запущен в другом окне. Второй не нужен — закройте это окно.")
        return 1
    с = секреты()
    нужные = ("telegram_bot_token", "telegram_chat_id", "github_token")
    if not all(с.get(к) for к in нужные):
        print("В secrets.local.json не хватает: "
              + ", ".join(к for к in нужные if not с.get(к)))
        return 1
    бот = Бот(с["telegram_bot_token"], с["telegram_chat_id"], с["github_token"])
    # Команда /menu в списке команд бота — чтобы меню можно было вернуть,
    # если клавиатура скрылась.
    бот.телеграм("setMyCommands", {"commands": json.dumps(
        [{"command": "menu", "description": "Показать меню"}], ensure_ascii=False)})
    print("Бот запущен. Откройте его в Telegram и нажмите любую кнопку меню")
    print("или отправьте /menu. Остановить — закрыть это окно или Ctrl+C.\n")
    try:
        while True:
            if бот.шаг():
                print(f"  {time.strftime('%H:%M:%S')} обработано сообщений")
    except KeyboardInterrupt:
        print("\nБот остановлен.")
    finally:
        замок.close()
    return 0


if __name__ == "__main__":
    sys.exit(главное())
