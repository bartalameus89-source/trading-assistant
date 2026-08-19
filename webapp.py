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

from engine import data, exchange, render, risk, signals

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
            "причины": [render._попроще(п) for п in а.причины_отказа],
            "мин_объём": лимиты.get(актив, {}).get("мин_объём", 5.0),
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
</style>"""


def страница_кабинета() -> bytes:
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Кабинет — торговый ассистент</title>{СТИЛИ}</head><body>
<div class="шапка">
  <div><h1>Торговый ассистент</h1>
  <p class="тихо" id="статус">Загрузка…</p></div>
  <div><a href="/logout" class="тихо">Выйти</a></div>
</div>

<div class="карточка">
  <div class="панель">
    <div class="поле"><label>Капитал, $</label>
      <input id="капитал" type="number" min="10" step="10" value="100"></div>
    <div class="поле"><label>Риск на сделку, %</label>
      <select id="риск">
        <option value="0.5" selected>0.5% — осторожно</option>
        <option value="1">1% — стандарт</option>
        <option value="2">2% — предел разумного</option>
      </select></div>
    <div class="поле"><label>Плечо</label>
      <select id="плечо">
        <option value="1" selected>1x — спот, без ликвидации</option>
        <option value="2">2x</option><option value="3">3x</option>
        <option value="5">5x</option><option value="10">10x</option>
      </select></div>
    <div class="поле"><button onclick="загрузить(true)">Пересчитать</button></div>
  </div>
  <p class="тихо" id="подсказка"></p>
</div>

<div class="сетка" id="итоги"></div>

<h2>Сигналы к действию</h2>
<div id="сделки"></div>

<h2>Состояние рынка по монетам</h2>
<div class="карточка обёртка"><table>
<thead><tr><th>Монета</th><th>Цена</th><th>Режим</th><th>Волатильность</th>
<th>Можно входить?</th><th>Почему</th></tr></thead>
<tbody id="строки"></tbody></table></div>

<p class="мелко">Система ничего не исполняет. Все расчёты — предложение,
решение принимаете вы. Прибыльность стратегии пока не подтверждена.</p>

<script>
const $ = id => document.getElementById(id);
const деньги = n => n == null ? '—' : n.toLocaleString('ru-RU',
  {{minimumFractionDigits:2, maximumFractionDigits:2}});

function классРежима(р, вол) {{
  if (вол === 'высокая') return 'хаос';
  if (р === 'тренд вверх') return 'вверх';
  if (р === 'тренд вниз') return 'вниз';
  return 'бок';
}}

function вердикт(м) {{
  if (м.ошибка) return ['бок', 'нет данных'];
  if (м.волатильность === 'высокая') return ['хаос', 'нет — рынок штормит'];
  if (м.сделка) return [м.сделка.направление === 'Long' ? 'вверх' : 'вниз',
                        м.сделка.направление === 'Long' ? 'ДА — покупка' : 'ДА — шорт'];
  if (м.режим === 'тренд вверх') return ['вверх', 'только лонг, ждём момент'];
  if (м.режим === 'тренд вниз') return ['вниз', 'только шорт, ждём момент'];
  if (м.режим === 'боковик') return ['бок', 'ждём выхода из диапазона'];
  return ['бок', 'направление неясно'];
}}

async function загрузить(обновить) {{
  const к = +$('капитал').value, р = +$('риск').value, п = +$('плечо').value;
  localStorage.setItem('настройки', JSON.stringify({{к, р, п}}));
  $('статус').textContent = 'Считаю…';
  const о = await fetch(`/api?капитал=${{к}}&риск=${{р}}&плечо=${{п}}`
    + (обновить ? '&обновить=1' : ''));
  const д = await о.json();

  $('статус').textContent = `Обновлено в ${{д.обновлено}} · монет ${{д.монет}} · сигналов ${{д.сигналов}}`;
  $('подсказка').textContent = `При капитале ${{деньги(д.капитал)}} $ и риске ${{д.риск}}% `
    + `вы рискуете ${{деньги(д.риск_денег)}} $ в одной сделке.`
    + (п > 1 ? ` Плечо ${{п}}x: появляется принудительное закрытие и плата за удержание.`
             : ' Без плеча: ликвидации не существует.');

  $('итоги').innerHTML = [
    ['Капитал', деньги(д.капитал) + ' $'],
    ['Риск на сделку', деньги(д.риск_денег) + ' $'],
    ['Сигналов сейчас', д.сигналов],
    ['Монет под наблюдением', д.монет],
  ].map(([т, з]) => `<div class="карточка"><div class="мелко">${{т}}</div>
    <div class="цифра">${{з}}</div></div>`).join('');

  const сделки = д.монеты.filter(м => м.сделка);
  $('сделки').innerHTML = сделки.length ? сделки.map(м => {{
    const с = м.сделка, длин = с.направление === 'Long';
    return `<div class="карточка сделка">
      <h1>${{длин ? '🟢 Покупка' : '🔴 Продажа в шорт'}} · ${{м.актив.replace('USDT','')}}</h1>
      <p class="тихо">Уверенность: ${{с.уверенность}} · режим: ${{м.режим}}</p>
      <ol class="шаги">
        <li>Ордер на ${{длин ? 'покупку' : 'продажу'}} <b>${{с.размер.toFixed(6)}}</b>
            по цене <b>${{деньги(с.вход)}}</b> (${{с.тип_ордера}}), объём ${{деньги(с.объём)}} $</li>
        <li>Сразу стоп-лосс на <b>${{деньги(с.стоп)}}</b> — обязательно</li>
        <li>Закрыть при <b>${{деньги(с.цель)}}</b></li>
      </ol>
      <p>Рискуете <b>${{деньги(с.риск_денег)}} $</b> (${{с.риск_проц.toFixed(2)}}% капитала),
         возможная прибыль в ${{с.rr.toFixed(1)}} раза больше. Комиссии ~${{деньги(с.издержки)}} $.</p>
      ${{с.ликвидация ? `<p class="ошибка">Ликвидация на ${{деньги(с.ликвидация)}} —
         при плече позицию закроют принудительно, если цена дойдёт сюда.</p>` : ''}}
      <p class="мелко">Основания: ${{с.подтверждения.join(' · ')}}</p>
    </div>`;
  }}).join('') : `<div class="карточка"><p>Сигналов нет.</p>
    <p class="тихо">Это нормальный результат: система молчит, когда не за что зацепиться.</p></div>`;

  $('строки').innerHTML = д.монеты.map(м => {{
    if (м.ошибка) return `<tr><td>${{м.актив}}</td><td colspan="5" class="тихо">нет данных</td></tr>`;
    const [кл, текст] = вердикт(м);
    const причина = м.сделка ? 'все условия совпали'
      : (м.причины && м.причины[0] ? м.причины[0].slice(0, 90) : '—');
    return `<tr>
      <td><b>${{м.актив.replace('USDT','')}}</b></td>
      <td>${{деньги(м.цена)}}</td>
      <td><span class="знак ${{классРежима(м.режим, 'нормальная')}}">${{м.режим}}</span></td>
      <td><span class="знак ${{м.волатильность === 'высокая' ? 'хаос' : 'бок'}}">${{м.волатильность}}</span></td>
      <td><span class="знак ${{кл}}">${{текст}}</span></td>
      <td class="мелко">${{причина}}</td></tr>`;
  }}).join('');
}}

const сохр = localStorage.getItem('настройки');
if (сохр) {{
  const {{к, р, п}} = JSON.parse(сохр);
  $('капитал').value = к; $('риск').value = р; $('плечо').value = п;
}}
['капитал','риск','плечо'].forEach(id => $(id).onchange = () => загрузить(false));
загрузить(false);
</script></body></html>""".encode()


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

        if путь.path == "/api":
            q = urllib.parse.parse_qs(путь.query)
            try:
                капитал = max(10.0, float(q.get("капитал", ["100"])[0]))
                риск = min(5.0, max(0.1, float(q.get("риск", ["0.5"])[0])))
                плечо = min(20.0, max(1.0, float(q.get("плечо", ["1"])[0])))
            except ValueError:
                капитал, риск, плечо = 100.0, 0.5, 1.0
            обновить = q.get("обновить", ["0"])[0] == "1"
            д = данные(капитал, риск, плечо, обновить)
            тело = json.dumps(д, ensure_ascii=False).encode()
            return self._ответ(тело, тип="application/json; charset=utf-8")

        self._ответ(b"not found", HTTPStatus.NOT_FOUND, "text/plain")

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/login":
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


if __name__ == "__main__":
    обеспечить_пароль()
    print(f"Кабинет запущен.  Откройте в браузере:  http://localhost:{ПОРТ}")
    print("Остановить — Ctrl+C\n")
    with Сервер(("127.0.0.1", ПОРТ), Обработчик) as сервер:
        try:
            сервер.serve_forever()
        except KeyboardInterrupt:
            print("\nОстановлено.")
