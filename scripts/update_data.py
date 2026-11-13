#!/usr/bin/env python3
"""국내 주요 종목의 일봉(Yahoo Finance)을 받아 지표를 계산하고 일일 추천을 만든다.

출력
- docs/data.json    : 앱이 읽는 최신 분석 결과
- docs/history.json : 날짜별 추천 이력 (나중에 성과 검증용)

종목 목록은 scripts/universe.json 에서 읽는다. 수정하려면 그 파일을 편집하세요.
"""
import json
import time
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "docs" / "data.json"
HISTORY_PATH = ROOT / "docs" / "history.json"
UNIVERSE_PATH = Path(__file__).resolve().parent / "universe.json"
KST = dt.timezone(dt.timedelta(hours=9))

HISTORY_PERIOD = "6mo"       # 60일선, 60일 수익률 계산에 충분한 기간
MIN_SCORE = 5                # 6개 조건 중 이 개수 이상 충족해야 추천
CHART_POINTS = 60            # 앱 차트에 쓸 최근 종가 개수
HORIZON = 5                  # 예측 구간: 5거래일
Z80 = 1.2816                 # 80% 구간용 z값 (정규분포 가정)
REQUEST_PAUSE = 0.3          # 요청 간격(초)
DISCLAIMER = (
    "본 결과는 과거 가격 데이터에 기반한 규칙 기반 참고 정보이며 투자 권유가 아닙니다. "
    "투자 판단과 그에 따른 손익의 책임은 본인에게 있습니다."
)


def load_universe():
    return json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))


def fetch_history(code):
    """코스피(.KS)를 먼저 보고, 없으면 코스닥(.KQ)을 본다."""
    for suffix in (".KS", ".KQ"):
        df = yf.Ticker(code + suffix).history(period=HISTORY_PERIOD, auto_adjust=True)
        if not df.empty and len(df) >= 65:
            return df
    return None


def rsi(close, n=14):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def analyze(df):
    """종가, 거래량 컬럼과 날짜 인덱스를 가진 DataFrame으로 지표와 조건 점검 결과를 만든다."""
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
        "series": [
            {"d": idx.strftime("%Y-%m-%d"), "c": int(round(float(x)))} for idx, x in tail.items()
        ],
    }


def main():
    now = dt.datetime.now(KST)
    stocks, errors = [], []

    for item in load_universe():
        code, name = item["code"], item["name"]
        try:
            df = fetch_history(code)
            if df is None:
                errors.append(f"{code} {name}: 데이터 없음")
                continue
            frame = pd.DataFrame({"종가": df["Close"].values, "거래량": df["Volume"].values},
                                 index=pd.to_datetime(df.index.date))
            res = analyze(frame)
            if res is None:
                errors.append(f"{code} {name}: 데이터 부족")
                continue
            res.update({
                "code": code,
                "name": name,
                "last_date": frame.index[-1].strftime("%Y-%m-%d"),
            })
            stocks.append(res)
        except Exception as e:  # 한 종목 실패가 전체 갱신을 멈추지 않도록
            errors.append(f"{code} {name}: {e}")
        time.sleep(REQUEST_PAUSE)

    if not stocks:
        raise RuntimeError("모든 종목의 데이터 수집에 실패했습니다. Actions 로그를 확인하세요.")

    base = max(s["last_date"] for s in stocks)

    ranked = sorted(stocks, key=lambda s: (s["score"], s["ret60_pct"]), reverse=True)
    recommendation = None
    if ranked[0]["score"] >= MIN_SCORE:
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
        "base_date": base,
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
    history = [h for h in history if h["date"] != base]
    history.append({"date": base, "recommendation": recommendation})
    HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")

    name = recommendation["name"] if recommendation else "없음"
    print(f"분석 완료: 기준일 {base}, {len(stocks)}개 종목, 추천: {name}, 오류 {len(errors)}건")


if __name__ == "__main__":
    main()
