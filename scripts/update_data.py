#!/usr/bin/env python3
"""금융위원회 주식시세정보 API(공공데이터포털)로 국내 주식 일봉을 받아
지표를 계산하고 일일 추천을 만든다.

필요 환경변수
- DATA_GO_KR_KEY : 공공데이터포털 인증키 (일반 인증키 Decoding 값)

출력
- docs/data.json    : 앱이 읽는 최신 분석 결과
- docs/history.json : 날짜별 추천 이력 (나중에 성과 검증용)
"""
import os
import json
import time
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "docs" / "data.json"
HISTORY_PATH = ROOT / "docs" / "history.json"
KST = dt.timezone(dt.timedelta(hours=9))

API_URL = "https://apis.data.go.kr/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo"
PAGE_SIZE = 1000
LOOKBACK_CAL_DAYS = 110      # 약 70거래일 확보 (60일선, 60일 수익률 계산용)
MAX_BASE_TRIES = 10          # 최신 데이터가 없을 때 과거로 거슬러 올라갈 최대 평일 수
UNIVERSE_SIZE = 100          # 시가총액 상위 N개
MARKETS = {"KOSPI", "KOSDAQ"}
MIN_SCORE = 5                # 6개 조건 중 이 개수 이상 충족해야 추천
CHART_POINTS = 60            # 앱 차트에 쓸 최근 종가 개수
HORIZON = 5                  # 예측 구간: 5거래일
Z80 = 1.2816                 # 80% 구간용 z값 (정규분포 가정)
EXCLUDE_KEYWORDS = ["ETF", "ETN", "스팩", "KODEX", "TIGER", "KBSTAR", "ARIRANG", "KOSEF", "HANARO"]
DISCLAIMER = (
    "본 결과는 과거 가격 데이터에 기반한 규칙 기반 참고 정보이며 투자 권유가 아닙니다. "
    "투자 판단과 그에 따른 손익의 책임은 본인에게 있습니다."
)


def fetch_day(bas_dt):
    """기준일(YYYYMMDD)의 전 종목 시세를 모두 가져온다. 휴장일이면 빈 리스트."""
    if not os.environ.get("DATA_GO_KR_KEY"):
        raise RuntimeError("환경변수 DATA_GO_KR_KEY가 없습니다. GitHub Secrets에 추가하세요.")
    rows, page = [], 1
    while True:
        r = requests.get(
            API_URL,
            params={
                "serviceKey": os.environ["DATA_GO_KR_KEY"],
                "resultType": "json",
                "numOfRows": PAGE_SIZE,
                "pageNo": page,
                "basDt": bas_dt,
            },
            timeout=60,
        )
        r.raise_for_status()
        resp = r.json()["response"]
        header = resp["header"]
        if str(header.get("resultCode")) != "00":
            raise RuntimeError(f"API 오류 {header.get('resultCode')}: {header.get('resultMsg')}")
        body = resp.get("body") or {}
        items = (body.get("items") or {}).get("item") or []
        if isinstance(items, dict):
            items = [items]
        rows.extend(items)
        total = int(body.get("totalCount") or 0)
        if not items or page * PAGE_SIZE >= total:
            break
        page += 1
    return rows


def to_frame(rows):
    df = pd.DataFrame(rows)
    rename = {
        "basDt": "date",
        "srtnCd": "code",
        "itmsNm": "name",
        "mrktCtg": "market",
        "clpr": "close",
        "trqu": "volume",
        "mrktTotAmt": "cap",
    }
    missing = [k for k in rename if k not in df.columns]
    if missing:
        raise RuntimeError(f"응답 필드가 예상과 다릅니다: {missing}")
    df = df[list(rename)].rename(columns=rename)
    df["code"] = df["code"].astype(str).str[-6:]
    for col in ("close", "volume", "cap"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    return df.dropna(subset=["close"])


def load_market(now):
    """최신 데이터가 있는 날(기준일)을 찾고, 그 전후 약 70거래일 전 종목 시세를 모은다."""
    d = now.date()
    base, base_rows = None, None
    for _ in range(MAX_BASE_TRIES):
        while d.weekday() >= 5:
            d -= dt.timedelta(days=1)
        rows = fetch_day(d.strftime("%Y%m%d"))
        if rows:
            base, base_rows = d, rows
            break
        d -= dt.timedelta(days=1)
    if base is None:
        raise RuntimeError("최근 시세 데이터를 찾지 못했습니다. 인증키와 활용신청 상태를 확인하세요.")

    frames = [to_frame(base_rows)]
    floor = base - dt.timedelta(days=LOOKBACK_CAL_DAYS)
    d = base - dt.timedelta(days=1)
    while d >= floor:
        if d.weekday() < 5:
            rows = fetch_day(d.strftime("%Y%m%d"))
            if rows:
                frames.append(to_frame(rows))
            time.sleep(0.2)
        d -= dt.timedelta(days=1)
    market = pd.concat(frames, ignore_index=True).sort_values(["code", "date"])
    return base, market


def rsi(close, n=14):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def analyze(df):
    """일봉 DataFrame(종가, 거래량, 날짜 인덱스)으로 지표와 조건 점검 결과를 만든다."""
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
    return any(k in str(name) for k in EXCLUDE_KEYWORDS)


def main():
    now = dt.datetime.now(KST)
    base, market = load_market(now)
    base_ts = pd.Timestamp(base)

    latest = market[(market["date"] == base_ts) & (market["market"].isin(MARKETS))]
    latest = latest[~latest["name"].apply(is_excluded)]
    top = latest.sort_values("cap", ascending=False).head(UNIVERSE_SIZE)

    stocks, errors = [], []
    for _, row in top.iterrows():
        try:
            g = market[market["code"] == row["code"]].set_index("date")
            res = analyze(pd.DataFrame({"종가": g["close"], "거래량": g["volume"]}))
            if res is None:
                errors.append(f"{row['code']} {row['name']}: 데이터 부족")
                continue
            res.update({"code": row["code"], "name": row["name"], "last_date": base.strftime("%Y-%m-%d")})
            stocks.append(res)
        except Exception as e:  # 한 종목 실패가 전체 갱신을 멈추지 않도록
            errors.append(f"{row['code']}: {e}")

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
    print(f"분석 완료: 기준일 {payload['base_date']}, {len(stocks)}개 종목, 추천: {name}, 오류 {len(errors)}건")


if __name__ == "__main__":
    main()
