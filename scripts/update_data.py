#!/usr/bin/env python3
"""국내 주식(시가총액 상위 종목) 일봉을 받아 지표를 계산하고 일일 추천을 만든다.

출력
- docs/data.json    : 앱이 읽는 최신 분석 결과
- docs/history.json : 날짜별 추천 이력 (나중에 성과 검증용)
"""
import json
import time
import datetime as dt
from pathlib import Path

import numpy as np
from pykrx import stock

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "docs" / "data.json"
HISTORY_PATH = ROOT / "docs" / "history.json"
KST = dt.timezone(dt.timedelta(hours=9))

UNIVERSE_SIZE = 100          # 시가총액 상위 N개
LOOKBACK_CAL_DAYS = 400      # 60일선 계산을 위한 과거 데이터 기간(달력일)
MIN_SCORE = 5                # 6개 조건 중 이 개수 이상 충족해야 추천
CHART_POINTS = 60            # 앱 차트에 쓸 최근 종가 개수
HORIZON = 5                  # 예측 구간: 5거래일
Z80 = 1.2816                 # 80% 구간용 z값 (정규분포 가정)
EXCLUDE_KEYWORDS = ["ETF", "ETN", "스팩", "KODEX", "TIGER", "KBSTAR", "ARIRANG", "KOSEF", "HANARO"]
DISCLAIMER = (
    "본 결과는 과거 가격 데이터에 기반한 규칙 기반 참고 정보이며 투자 권유가 아닙니다. "
    "투자 판단과 그에 따른 손익의 책임은 본인에게 있습니다."
)


def find_base_date(now):
    """장 마감(16시 이후 기준) 이후 가장 최근의 거래일과 시가총액 표를 찾는다."""
    d = now.date()
    if now.hour < 16:
        d -= dt.timedelta(days=1)
    for _ in range(15):
        ds = d.strftime("%Y%m%d")
        cap = stock.get_market_cap(ds, market="ALL")
        if not cap.empty and cap["시가총액"].sum() > 0:
            return ds, cap
        d -= dt.timedelta(days=1)
    raise RuntimeError("최근 거래일 데이터를 찾지 못했습니다.")


def rsi(close, n=14):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def analyze(df):
    """일봉 DataFrame(종가, 거래량)으로 지표와 조건 점검 결과를 만든다."""
    if len(df) < 65:
        return None
    c = df["종가"].astype(float)
    v = df["거래량"].astype(float)

    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    r = float(rsi(c).iloc[-1])
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    hist = float((macd - macd.ewm(span=9, adjust=False).mean()).iloc[-1])
    vol_avg20 = float(v.rolling(20).mean().iloc[-1])
    vol_ratio = float(v.iloc[-1]) / vol_avg20 if vol_avg20 else 0.0

    last = float(c.iloc[-1])
    prev = float(c.iloc[-2])
    chg = last / prev - 1
    ret60 = float(c.iloc[-1] / c.iloc[-61] - 1)
    daily_vol = float(np.log(c).diff().tail(20).std())
    band = Z80 * daily_vol * np.sqrt(HORIZON)

    checks = {
        "종가가 20일선 위": bool(last > ma20.iloc[-1]),
        "20일선이 60일선 위": bool(ma20.iloc[-1] > ma60.iloc[-1]),
        "RSI 40~65 (과열·침체 아님)": bool(40 <= r <= 65),
        "MACD 히스토그램 양수": bool(hist > 0),
        "거래량 1.2배 이상 증가 & 상승 마감": bool(vol_ratio >= 1.2 and chg > 0),
        "60일 수익률 양수": bool(ret60 > 0),
    }
    tail = c.tail(CHART_POINTS)
    return {
        "close": int(round(last)),
        "change_pct": round(chg * 100, 2),
        "ma20": int(round(float(ma20.iloc[-1]))),
        "ma60": int(round(float(ma60.iloc[-1]))),
        "rsi": round(r, 1),
        "macd_hist": round(hist, 2),
        "vol_ratio": round(vol_ratio, 2),
        "ret60_pct": round(ret60 * 100, 2),
        "range_low": int(round(last * float(np.exp(-band)))),
        "range_high": int(round(last * float(np.exp(band)))),
        "checks": checks,
        "score": int(sum(checks.values())),
        "series": [{"d": idx.strftime("%Y-%m-%d"), "c": int(round(float(x)))} for idx, x in tail.items()],
    }


def is_excluded(name):
    return any(k in name for k in EXCLUDE_KEYWORDS)


def main():
    now = dt.datetime.now(KST)
    base_ds, cap = find_base_date(now)
    base = dt.datetime.strptime(base_ds, "%Y%m%d")
    start = (base - dt.timedelta(days=LOOKBACK_CAL_DAYS)).strftime("%Y%m%d")
    top = cap.sort_values("시가총액", ascending=False).head(UNIVERSE_SIZE)

    stocks, errors = [], []
    for code in top.index:
        try:
            name = stock.get_market_ticker_name(code)
            if is_excluded(name):
                continue
            df = stock.get_market_ohlcv(start, base_ds, code)
            res = analyze(df)
            if res is None:
                errors.append(f"{code} {name}: 데이터 부족")
                continue
            res.update({"code": code, "name": name, "last_date": df.index[-1].strftime("%Y-%m-%d")})
            stocks.append(res)
        except Exception as e:  # 한 종목 실패가 전체 갱신을 멈추지 않도록
            errors.append(f"{code}: {e}")
        time.sleep(0.2)  # 과도한 요청 방지

    ranked = sorted(stocks, key=lambda s: (s["score"], s["ret60_pct"]), reverse=True)
    recommendation = None
    if ranked and ranked[0]["score"] >= MIN_SCORE:
        pick = ranked[0]
        recommendation = {
            "code": pick["code"],
            "name": pick["name"],
            "close": pick["close"],
            "score": pick["score"],
            "rsi": pick["rsi"],
            "ret60_pct": pick["ret60_pct"],
            "range_low": pick["range_low"],
            "range_high": pick["range_high"],
            "reasons": [k for k, ok in pick["checks"].items() if ok],
        }

    payload = {
        "updated_at": now.strftime("%Y-%m-%d %H:%M KST"),
        "base_date": base.strftime("%Y-%m-%d"),
        "horizon_days": HORIZON,
        "universe_size": len(stocks),
        "disclaimer": DISCLAIMER,
        "recommendation": recommendation,
        "stocks": stocks,
        "errors": errors,
    }
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    history = []
    if HISTORY_PATH.exists():
        history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    history = [h for h in history if h["date"] != payload["base_date"]]
    history.append({"date": payload["base_date"], "recommendation": recommendation})
    HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")

    name = recommendation["name"] if recommendation else "없음"
    print(f"분석 완료: {len(stocks)}개 종목, 추천: {name}, 오류 {len(errors)}건")


if __name__ == "__main__":
    main()
