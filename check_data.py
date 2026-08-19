"""
Проверка связки «биржа -> индикаторы». Это ещё не торговый сигнал:
здесь нет логики режима, подтверждений и уверенности — только сырые числа,
чтобы убедиться, что данные приходят и считаются правильно.

Запуск:  python check_data.py
"""

import json
import os

from engine import data, indicators as ind

БАЗА = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(БАЗА, "config.json"), encoding="utf-8"))
П = CFG["пороги"]


def показать(актив: str) -> None:
    свечи = data.загрузить(
        актив, CFG["таймфрейм"], лимит=400, источники=CFG["источник_данных"]
    )

    закрытия, максимумы, минимумы = свечи.closes, свечи.highs, свечи.lows

    rsi_, _ = ind.last_defined(ind.rsi(закрытия, П["rsi_период"]))
    atr_, _ = ind.last_defined(ind.atr(максимумы, минимумы, закрытия, П["atr_период"]))
    ma50, _ = ind.last_defined(ind.sma(закрытия, П["ma_быстрая"]))
    ma200, _ = ind.last_defined(ind.sma(закрытия, П["ma_медленная"]))
    ema20, _ = ind.last_defined(ind.ema(закрытия, П["ema_быстрая"]))

    adx_ряд, pdi_ряд, mdi_ряд = ind.adx(максимумы, минимумы, закрытия, П["adx_период"])
    adx_, _ = ind.last_defined(adx_ряд)
    pdi, _ = ind.last_defined(pdi_ряд)
    mdi, _ = ind.last_defined(mdi_ряд)

    верх, середина, низ, ширина = ind.bollinger(
        закрытия, П["bb_период"], П["bb_отклонений"]
    )
    bbw, _ = ind.last_defined(ширина)
    окно = [v for v in ширина[-П["bbw_окно_баров"]:] if v is not None]
    перцентиль = ind.percentile_rank(окно, bbw)

    линия, сигнал, гист = ind.macd(закрытия)
    macd_, _ = ind.last_defined(линия)
    macd_сиг, _ = ind.last_defined(сигнал)

    цена = свечи.последняя_цена
    флаги = data.проверить_качество(свечи, CFG["сигналы"]["макс_возраст_данных_баров"])

    print(f"\n{'=' * 58}")
    print(f"  {актив}   {CFG['таймфрейм']}   источник: {свечи.источник}")
    print(f"{'=' * 58}")
    print(f"  Последний бар : {свечи.время_последнего_бара:%Y-%m-%d %H:%M} UTC "
          f"(возраст {свечи.возраст_в_барах():.2f} бара)")
    print(f"  Баров загружено: {len(свечи)}")
    print(f"  Цена          : {цена:,.2f}")
    print()
    print(f"  RSI({П['rsi_период']})      : {rsi_:.1f}")
    print(f"  ATR({П['atr_период']})      : {atr_:,.2f}  ({atr_ / цена * 100:.2f}% от цены)")
    print(f"  MA50 / MA200  : {ma50:,.2f} / {ma200:,.2f}"
          f"   -> {'MA50 выше' if ma50 > ma200 else 'MA50 ниже'}")
    print(f"  EMA20         : {ema20:,.2f}")
    print(f"  ADX({П['adx_период']})      : {adx_:.1f}   (+DI {pdi:.1f} / -DI {mdi:.1f})")
    print(f"  MACD          : {macd_:.2f}  сигнальная {macd_сиг:.2f}"
          f"   -> гистограмма {macd_ - macd_сиг:+.2f}")
    print(f"  Bollinger     : низ {низ[-1]:,.2f}  середина {середина[-1]:,.2f}  "
          f"верх {верх[-1]:,.2f}")
    print(f"  Ширина BB     : {bbw:.2f}%  -> перцентиль {перцентиль:.0f} "
          f"за {len(окно)} баров")

    # Предварительная классификация режима (полная логика — в следующем модуле).
    if adx_ >= П["adx_тренд"]:
        режим = "тренд вверх" if ma50 > ma200 else "тренд вниз"
    elif adx_ <= П["adx_боковик"]:
        режим = "боковик"
    else:
        режим = "переходный (режим не определён)"
    волатильность = ("ВЫСОКАЯ" if перцентиль >= П["bbw_высокая_волатильность_перцентиль"]
                     else "нормальная")
    print()
    print(f"  РЕЖИМ         : {режим}")
    print(f"  ВОЛАТИЛЬНОСТЬ : {волатильность}")

    print()
    if флаги:
        print("  ФЛАГИ КАЧЕСТВА ДАННЫХ:")
        for f in флаги:
            print(f"    ! {f}")
    else:
        print("  Качество данных: замечаний нет")


if __name__ == "__main__":
    for актив in CFG["активы"]:
        try:
            показать(актив)
        except Exception as e:
            print(f"\n  {актив}: ОШИБКА -> {type(e).__name__}: {e}")
    print()
