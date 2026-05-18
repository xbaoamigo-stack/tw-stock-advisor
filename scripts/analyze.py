#!/usr/bin/env python3
"""
台股型態學每日掃描
- 從 FinMind 抓日K
- 偵測 W 底 / M 頭 / 突破 / 跌破 / 量價背離
- 輸出 data/latest.json 與 data/YYYY-MM-DD.json
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
INDUSTRIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "industries.json")

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
TPE = timezone(timedelta(hours=8))


def log(msg: str) -> None:
    print(f"[analyze] {msg}", flush=True)


def load_industries() -> dict:
    with open(INDUSTRIES_PATH, encoding="utf-8") as f:
        return json.load(f)


def fetch_finmind(stock_id: str, start: str, end: str, retries: int = 3) -> list[dict]:
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": stock_id,
        "start_date": start,
        "end_date": end,
    }
    url = f"{FINMIND_URL}?{urlencode(params)}"
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tw-stock-advisor/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read())
            if payload.get("status") != 200:
                raise RuntimeError(f"FinMind status={payload.get('status')} msg={payload.get('msg')}")
            return payload.get("data", []) or []
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, TimeoutError) as e:
            last_err = e
            wait = 1.5 * (attempt + 1)
            log(f"  retry {stock_id} after error: {e} (sleep {wait}s)")
            time.sleep(wait)
    raise RuntimeError(f"fetch_finmind failed for {stock_id}: {last_err}")


def normalize(rows: list[dict]) -> list[dict]:
    """FinMind 欄位 -> 我們用的格式。"""
    out = []
    for r in rows:
        # 過濾無交易的天 (volume == 0 也保留，但 open=0 通常是停牌)
        if r.get("open") in (None, 0) and r.get("close") in (None, 0):
            continue
        out.append({
            "date": r["date"],
            "open": float(r.get("open") or 0),
            "high": float(r.get("max") or 0),
            "low": float(r.get("min") or 0),
            "close": float(r.get("close") or 0),
            "volume": int(r.get("Trading_Volume") or 0),
        })
    out.sort(key=lambda x: x["date"])
    return out


# ---- 型態偵測 ---------------------------------------------------------------

def find_local_extrema(values: list[float], window: int = 5) -> tuple[list[int], list[int]]:
    """回傳 (lows_idx, highs_idx)。window 表示左右各看幾天。"""
    lows, highs = [], []
    n = len(values)
    for i in range(window, n - window):
        seg = values[i - window:i + window + 1]
        v = values[i]
        if v == min(seg) and seg.count(v) == 1:
            lows.append(i)
        if v == max(seg) and seg.count(v) == 1:
            highs.append(i)
    return lows, highs


def detect_w_bottom(candles: list[dict]) -> dict | None:
    """W 底：兩個低點接近，中間反彈高點 = 頸線。"""
    if len(candles) < 30:
        return None
    closes = [c["close"] for c in candles]
    lows_idx, highs_idx = find_local_extrema(closes, window=5)
    if len(lows_idx) < 2:
        return None
    # 在最後 60 天內找
    recent_cut = max(0, len(candles) - 80)
    lows_idx = [i for i in lows_idx if i >= recent_cut]
    if len(lows_idx) < 2:
        return None

    # 取最後兩個低點
    i1, i2 = lows_idx[-2], lows_idx[-1]
    if i2 - i1 < 8 or i2 - i1 > 60:
        return None
    low1, low2 = closes[i1], closes[i2]
    if low1 == 0:
        return None
    # 兩底差距 <= 5%
    if abs(low2 - low1) / low1 > 0.05:
        return None
    # 中間反彈高點 = 頸線
    mid_highs = [h for h in highs_idx if i1 < h < i2]
    if not mid_highs:
        return None
    neck_idx = max(mid_highs, key=lambda h: closes[h])
    neckline = closes[neck_idx]
    if neckline <= max(low1, low2):
        return None

    last_close = closes[-1]
    bottom = min(low1, low2)
    breakout = last_close > neckline
    # 滿足點：頸線 + (頸線 - 底)
    target = neckline + (neckline - bottom)
    stop = bottom * 0.97
    confidence = "高" if breakout and (last_close > neckline * 1.01) else ("中" if breakout else "低")
    signal = "買進" if breakout else "觀望"
    return {
        "type": "W 底",
        "signal": signal,
        "neckline": round(neckline, 2),
        "bottom": round(bottom, 2),
        "target": round(target, 2),
        "stopLoss": round(stop, 2),
        "breakout": breakout,
        "confidence": confidence,
    }


def detect_m_top(candles: list[dict]) -> dict | None:
    if len(candles) < 30:
        return None
    closes = [c["close"] for c in candles]
    lows_idx, highs_idx = find_local_extrema(closes, window=5)
    if len(highs_idx) < 2:
        return None
    recent_cut = max(0, len(candles) - 80)
    highs_idx = [i for i in highs_idx if i >= recent_cut]
    if len(highs_idx) < 2:
        return None

    i1, i2 = highs_idx[-2], highs_idx[-1]
    if i2 - i1 < 8 or i2 - i1 > 60:
        return None
    h1, h2 = closes[i1], closes[i2]
    if h1 == 0:
        return None
    if abs(h2 - h1) / h1 > 0.05:
        return None
    mid_lows = [l for l in lows_idx if i1 < l < i2]
    if not mid_lows:
        return None
    neck_idx = min(mid_lows, key=lambda l: closes[l])
    neckline = closes[neck_idx]
    if neckline >= min(h1, h2):
        return None

    last_close = closes[-1]
    top = max(h1, h2)
    breakdown = last_close < neckline
    target = neckline - (top - neckline)
    stop = top * 1.03
    confidence = "高" if breakdown and (last_close < neckline * 0.99) else ("中" if breakdown else "低")
    signal = "賣出" if breakdown else "觀望"
    return {
        "type": "M 頭",
        "signal": signal,
        "neckline": round(neckline, 2),
        "top": round(top, 2),
        "target": round(target, 2),
        "stopLoss": round(stop, 2),
        "breakdown": breakdown,
        "confidence": confidence,
    }


def detect_trend(candles: list[dict]) -> dict:
    """無明顯型態時，用均線/動能簡單判斷。"""
    closes = [c["close"] for c in candles]
    if len(closes) < 20:
        return {
            "type": "資料不足",
            "signal": "觀望",
            "confidence": "低",
        }
    ma5 = sum(closes[-5:]) / 5
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else ma20
    last = closes[-1]
    # 20 日新高/新低
    hi20 = max(closes[-20:])
    lo20 = min(closes[-20:])

    if last >= hi20 and ma5 > ma20 > ma60:
        return {
            "type": "多頭趨勢/突破前高",
            "signal": "買進",
            "neckline": round(hi20, 2),
            "target": round(last * 1.1, 2),
            "stopLoss": round(ma20, 2),
            "confidence": "中",
        }
    if last <= lo20 and ma5 < ma20 < ma60:
        return {
            "type": "空頭趨勢/跌破前低",
            "signal": "賣出",
            "neckline": round(lo20, 2),
            "target": round(last * 0.9, 2),
            "stopLoss": round(ma20, 2),
            "confidence": "中",
        }
    if ma5 > ma20:
        return {"type": "盤整偏多", "signal": "觀望", "confidence": "低"}
    return {"type": "盤整偏空", "signal": "觀望", "confidence": "低"}


def analyze_volume(candles: list[dict]) -> dict:
    vols = [c["volume"] for c in candles if c["volume"] > 0]
    if len(vols) < 20:
        return {
            "avgVolume": 0,
            "volumeRatio": 1.0,
            "isHighVolume": False,
            "isLowVolume": False,
            "mainForce": "中性",
            "divergence": "正常",
        }
    avg20 = sum(vols[-20:]) / 20
    last_vol = candles[-1]["volume"]
    ratio = last_vol / avg20 if avg20 > 0 else 1.0
    is_high = ratio >= 1.8
    is_low = ratio <= 0.5

    # 量價背離：價格新高但量縮，或價格新低但量增
    closes = [c["close"] for c in candles]
    recent_closes = closes[-10:]
    recent_vols = [c["volume"] for c in candles[-10:]]
    price_trend_up = recent_closes[-1] > recent_closes[0]
    vol_avg_recent = sum(recent_vols) / len(recent_vols)
    vol_avg_prev = sum([c["volume"] for c in candles[-20:-10]]) / 10 if len(candles) >= 20 else vol_avg_recent
    vol_trend_up = vol_avg_recent > vol_avg_prev
    divergence = "正常"
    if price_trend_up and not vol_trend_up:
        divergence = "價漲量縮（背離）"
    elif (not price_trend_up) and vol_trend_up:
        divergence = "價跌量增（背離）"

    main_force = "中性"
    if is_high and price_trend_up:
        main_force = "進貨"
    elif is_high and not price_trend_up:
        main_force = "出貨"
    elif is_low:
        main_force = "縮量觀望"

    return {
        "avgVolume": int(avg20),
        "volumeRatio": round(ratio, 2),
        "isHighVolume": is_high,
        "isLowVolume": is_low,
        "mainForce": main_force,
        "divergence": divergence,
    }


def analyze_one(stock_id: str, name: str, candles: list[dict]) -> dict | None:
    if len(candles) < 30:
        return None
    last = candles[-1]
    prev = candles[-2] if len(candles) >= 2 else last
    change_pct = ((last["close"] - prev["close"]) / prev["close"] * 100) if prev["close"] else 0.0

    pattern = detect_w_bottom(candles) or detect_m_top(candles) or detect_trend(candles)
    volume = analyze_volume(candles)

    return {
        "symbol": stock_id,
        "name": name,
        "currentPrice": round(last["close"], 2),
        "change": round(change_pct, 2),
        "pattern": pattern,
        "volumePrice": volume,
        "volumeRatio": volume["volumeRatio"],
        # 只塞最近 90 天，避免 JSON 太大
        "data": candles[-90:],
    }


# ---- 主流程 -----------------------------------------------------------------

def main() -> int:
    industries = load_industries()
    stock_ids = list(industries.keys())
    log(f"target stocks: {len(stock_ids)}")

    # 抓 200 個交易日，給型態偵測足夠樣本
    today = datetime.now(TPE).date()
    end = today.isoformat()
    start = (today - timedelta(days=300)).isoformat()
    log(f"date range: {start} ~ {end}")

    results: list[dict] = []
    failures: list[str] = []
    latest_date = ""

    for idx, sid in enumerate(stock_ids, 1):
        meta = industries[sid]
        name = meta.get("name", sid)
        log(f"[{idx:>2}/{len(stock_ids)}] fetch {sid} {name}")
        try:
            rows = fetch_finmind(sid, start, end)
            candles = normalize(rows)
            if not candles:
                log(f"  empty data for {sid}")
                failures.append(sid)
                continue
            r = analyze_one(sid, name, candles)
            if r is None:
                failures.append(sid)
                continue
            r["industry"] = meta.get("industry", "其他")
            r["subIndustry"] = meta.get("subIndustry", "")
            r["tags"] = meta.get("tags", [])
            results.append(r)
            if candles[-1]["date"] > latest_date:
                latest_date = candles[-1]["date"]
        except Exception as e:
            log(f"  FAIL {sid}: {e}")
            failures.append(sid)
        # 對 FinMind 客氣一點，避免被限速
        time.sleep(0.3)

    if not results:
        log("ERROR: no results; keeping existing latest.json untouched")
        return 1

    # 大盤 (TAIEX) 簡單訊號：對所有股票訊號做加權
    buys = sum(1 for r in results if r["pattern"].get("signal") == "買進")
    sells = sum(1 for r in results if r["pattern"].get("signal") == "賣出")
    if buys > sells * 1.5:
        idx_signal = "偏多"
    elif sells > buys * 1.5:
        idx_signal = "偏空"
    else:
        idx_signal = "中性"

    output = {
        "date": latest_date or end,
        "timestamp": int(time.time() * 1000),
        "index": {
            "signal": idx_signal,
            "reason": f"買進 {buys} / 賣出 {sells} / 觀望 {len(results)-buys-sells}",
        },
        "stats": {
            "total": len(results),
            "buy": buys,
            "sell": sells,
            "watch": len(results) - buys - sells,
            "failures": failures,
        },
        "stocks": results,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    latest_path = os.path.join(DATA_DIR, "latest.json")
    dated_path = os.path.join(DATA_DIR, f"{output['date']}.json")
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))
    with open(dated_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))
    log(f"wrote {latest_path}  ({len(results)} stocks, failures={len(failures)})")
    log(f"wrote {dated_path}")

    # 更新 data/index.json：可用日期清單（降序）
    dates = []
    for fn in os.listdir(DATA_DIR):
        if fn == "latest.json" or fn == "index.json":
            continue
        if fn.endswith(".json") and len(fn) == 15:  # YYYY-MM-DD.json
            dates.append(fn[:-5])
    dates.sort(reverse=True)
    with open(os.path.join(DATA_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"dates": dates[:90]}, f, ensure_ascii=False)
    log(f"wrote index.json ({len(dates)} dates)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
