#!/usr/bin/env python3
"""
阿兔簡易波浪 + 外資成本推估工具
==================================
來源：學習團學員方法，2026-08-27 分享（非投資建議）

CLI 模式
--------
    # 只算三檔目標價
    python3 wave_target.py 3675

    # 算目標價 + 攤平建議（持股 100 股 @400，現價 283，要攤到 370）
    python3 wave_target.py 3675 --hold 100 --cost 400 --price 283 --target 370

    # 指定「近期高」自訂（例如從某起漲點）
    python3 wave_target.py 3675 --high 269 --low 194.5

演算法
------
1. K 線（FinMind TaiwanStockPrice）取最近 5 日均價當「基準價」（或 --high）
2. 區間低點（最近 60 日內最低收盤；或 --low）
3. Δ = 基準 − 低點 → 三檔波浪目標 = 基準 + Δ × 1.5 / 2 / 2.5
4. 外資成本（FinMind TaiwanStockInstitutionalInvestorsBuySell）：
   抓最近 N 天（預設 60），每天用「收盤價 × 當日外資(Investor+Dealer_Self)淨買張數」
   做 VWAP，作為「外資近期加權成本」
5. 外資目標 = 外資成本 × 1.2 / 1.4 / 1.7
6. 兩條腿重疊的價位最值得信

輸出：JSON（stdout）＋ 友善表格（stderr/print）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
INDUSTRIES_PATH = os.path.join(SCRIPTS_DIR, "industries.json")

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
TPE = timezone(timedelta(hours=8))

# 三檔目標乘數（與方法定義一致）
WAVE_MULTIPLIERS = [1.5, 2.0, 2.5]
FOREIGN_MULTIPLIERS = [1.2, 1.4, 1.7]

# 外資認定（包含自營商 foreign dealer self —— 部分 FinMind 帳戶是這樣拆的）
FOREIGN_NAMES = {"Foreign_Investor", "Foreign_Dealer_Self"}


def log(msg: str) -> None:
    print(f"[wave] {msg}", file=sys.stderr, flush=True)


def load_industries() -> dict:
    if not os.path.exists(INDUSTRIES_PATH):
        return {}
    with open(INDUSTRIES_PATH, encoding="utf-8") as f:
        return json.load(f)


def fetch_finmind(dataset: str, stock_id: str, start: str, end: str, retries: int = 3) -> list[dict]:
    params = {
        "dataset": dataset,
        "data_id": stock_id,
        "start_date": start,
        "end_date": end,
    }
    url = f"{FINMIND_URL}?{urlencode(params)}"
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tw-stock-advisor-wave/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read())
            if payload.get("status") != 200:
                raise RuntimeError(f"FinMind status={payload.get('status')} msg={payload.get('msg')}")
            return payload.get("data", []) or []
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, TimeoutError) as e:
            last_err = e
            wait = 1.5 * (attempt + 1)
            log(f"retry {dataset} {stock_id} after error: {e} (sleep {wait}s)")
            time.sleep(wait)
    raise RuntimeError(f"FinMind fetch failed: {dataset} {stock_id}: {last_err}")


def fetch_price(stock_id: str, days: int = 90) -> list[dict]:
    today = datetime.now(TPE).date()
    end = today.isoformat()
    start = (today - timedelta(days=days + 30)).isoformat()
    rows = fetch_finmind("TaiwanStockPrice", stock_id, start, end)
    out = []
    for r in rows:
        if r.get("open") in (None, 0) and r.get("close") in (None, 0):
            continue
        out.append({
            "date": r["date"],
            "close": float(r.get("close") or 0),
            "high": float(r.get("max") or 0),
            "low": float(r.get("min") or 0),
        })
    out.sort(key=lambda x: x["date"])
    return out


def fetch_institutional(stock_id: str, days: int = 60) -> list[dict]:
    """FinMind 每張股票每日 5 個 name（Foreign_Investor / Foreign_Dealer_Self / ...）。"""
    today = datetime.now(TPE).date()
    end = today.isoformat()
    start = (today - timedelta(days=days + 30)).isoformat()
    return fetch_finmind("TaiwanStockInstitutionalInvestorsBuySell", stock_id, start, end)


def recent_baseline(candles: list[dict], days: int = 5) -> tuple[float, float, float, float]:
    """回傳 (5日均, 區間低點, 區間高點, 最近一日收盤)。"""
    closes = [c["close"] for c in candles if c["close"] > 0]
    if not closes:
        raise RuntimeError("no price data")
    last_close = closes[-1]
    ma5 = sum(closes[-days:]) / min(days, len(closes))
    # 區間用最近 60 天（不含今天最後一根以外的極端值要小心，這裡直接 max/min）
    window = min(60, len(closes))
    lo = min(closes[-window:])
    hi = max(closes[-window:])
    return ma5, lo, hi, last_close


def compute_foreign_cost(price_by_date: dict[str, float], institutional: list[dict],
                         window_days: int = 60) -> dict | None:
    """
    外資「近期加權成本」proxy：
      - 每天把 (收盤價 × 當日外資淨買張數) 加起來
      - 總和除以淨買總張數
      - 結果：外資在最近 window_days 內「如果每天買/賣都打在收盤」的平均成本

    這是 proxy，不是真實 intraday VWAP。但作為「近期主力成本」量級判斷夠用，
    且跟原訊息「從起漲點算 271.73」這種 single number 在同一個量級。
    """
    today = datetime.now(TPE).date()
    cutoff = (today - timedelta(days=window_days)).isoformat()

    # 聚合每日外資（Investor + Dealer_Self）買賣張數
    by_date: dict[str, dict[str, float]] = {}
    for row in institutional:
        if row.get("name") not in FOREIGN_NAMES:
            continue
        d = row["date"]
        if d < cutoff:
            continue
        slot = by_date.setdefault(d, {"buy": 0.0, "sell": 0.0})
        slot["buy"] += float(row.get("buy") or 0)
        slot["sell"] += float(row.get("sell") or 0)

    if not by_date:
        return None

    cost_num = 0.0  # ∑ 收盤價 × 淨買張數
    net_buy_total = 0.0
    net_buy_days = 0
    for d, slot in by_date.items():
        close = price_by_date.get(d)
        if close is None or close <= 0:
            continue
        net = slot["buy"] - slot["sell"]
        if net > 0:
            cost_num += close * net
            net_buy_total += net
            net_buy_days += 1

    if net_buy_total <= 0:
        return {"cost": None, "netBuyDays": 0, "netBuyLots": 0,
                "note": "近期無外資淨買天，成本無法估算"}

    cost = cost_num / net_buy_total
    return {
        "cost": round(cost, 2),
        "netBuyDays": net_buy_days,
        "netBuyLots": int(net_buy_total),
        "windowDays": window_days,
    }


def avg_position(stock_id: str, hold: int, cost: float, price: float, target: float) -> dict:
    """
    解套計算：
      給 (持股股數, 持有均價, 目前價, 目標解套價)，
      回傳要在「目前價」加碼多少股能把平均成本壓到 ≤ target。

    (hold * cost + x * price) / (hold + x) = target
    → x = (target - cost) * hold / (price - target)
    """
    # 解套合理性檢查（公式解 x = hold * (target - cost) / (price - target)，要 x > 0）
    # 1. target >= cost → 想把成本拉到比既有更高，沒人會這樣做
    if target >= cost:
        return {"note": f"目標價 {target} ≥ 既有成本 {cost}，通常不需要攤平（攤平目的是拉低均價）",
                "addShares": 0,
                "resultingAvgCost": round(cost, 2),
                "resultingShares": hold}
    # 2. price >= target → 加碼價 ≥ 目標，怎麼買都壓不到 ≤ target
    if price >= target:
        return {"note": f"目前價 {price} ≥ 目標價 {target}，加碼也沒辦法把均價壓到 ≤ {target}（考慮直接賣或換更低目標）",
                "addShares": 0,
                "resultingAvgCost": round(cost, 2),
                "resultingShares": hold}
    x = (target - cost) * hold / (price - target)
    add = int(round(x + 0.499))  # 無條件進位（保守一點，多買一點保險）
    # 用進位後實際算平均成本
    new_total = hold * cost + add * price
    new_shares = hold + add
    new_avg = new_total / new_shares
    new_invest = new_total - hold * cost
    return {
        "addShares": add,
        "addCostTotal": round(new_invest, 0),
        "resultingAvgCost": round(new_avg, 2),
        "resultingShares": new_shares,
        "totalInvestment": round(new_total, 0),
        "toTargetGap": round(price - target, 2),
    }


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="阿兔簡易波浪 + 外資成本推估（非投資建議）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("ticker", help="台股代號，例如 3675")
    ap.add_argument("--high", type=float, help="覆寫基準價（預設 = 5 日均）")
    ap.add_argument("--low", type=float, help="覆寫區間低點（預設 = 60 日最低收盤）")
    ap.add_argument("--window", type=int, default=60, help="區間天數（預設 60）")
    ap.add_argument("--name", help="覆寫股票名稱（industries.json 沒列時用）")
    ap.add_argument("--json", action="store_true", help="只吐 JSON（適合 pipeline）")
    # 攤平計算
    ap.add_argument("--hold", type=int, help="持有股數")
    ap.add_argument("--cost", type=float, help="持有均價")
    ap.add_argument("--price", type=float, help="目前價（不給就用最近收盤）")
    ap.add_argument("--target", type=float, help="想攤到的目標成本價")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    ticker = args.ticker.strip()

    industries = load_industries()
    name = args.name or industries.get(ticker, {}).get("name") or ticker

    log(f"fetch price for {ticker} {name}")
    candles = fetch_price(ticker)
    if not candles:
        print(json.dumps({"error": "no price data"}, ensure_ascii=False))
        return 1

    ma5, lo_default, hi_default, last_close = recent_baseline(candles)
    baseline = args.high if args.high is not None else round(ma5)
    low = args.low if args.low is not None else lo_default

    delta = round(baseline - low, 2)
    wave_targets = [round(baseline + delta * m) for m in WAVE_MULTIPLIERS]

    log("fetch institutional investors")
    inst_rows = fetch_institutional(ticker, days=args.window)
    price_by_date = {c["date"]: c["close"] for c in candles}
    fcost = compute_foreign_cost(price_by_date, inst_rows, window_days=args.window)

    foreign_targets = []
    if fcost and fcost.get("cost"):
        foreign_targets = [round(fcost["cost"] * m) for m in FOREIGN_MULTIPLIERS]

    # 重疊偵測（兩條腿 tolerance 2%）
    overlap = []
    for w in wave_targets:
        for f in foreign_targets:
            if abs(w - f) / max(w, f, 1) < 0.02:
                overlap.append({"wave": w, "foreign": f, "consensus": round((w + f) / 2)})

    # 攤平計算
    avg_plan = None
    if args.hold is not None and args.cost is not None and args.target is not None:
        price_now = args.price if args.price is not None else last_close
        avg_plan = avg_position(ticker, args.hold, args.cost, price_now, args.target)

    result = {
        "ticker": ticker,
        "name": name,
        "asOfDate": candles[-1]["date"],
        "lastClose": round(last_close, 2),
        "baseline": {"value": round(baseline, 2),
                     "source": "manual" if args.high is not None else f"MA{5}"},
        "recentLow": round(low, 2),
        "windowDays": args.window,
        "delta": delta,
        "waveTargets": dict(zip(["x1.5", "x2.0", "x2.5"], wave_targets)),
        "foreignCost": fcost,
        "foreignTargets": dict(zip(["x1.2", "x1.4", "x1.7"], foreign_targets)),
        "overlapConsensus": overlap,
        "disclaimer": "非投資買賣建議，數字僅供學習驗算",
    }
    if avg_plan:
        result["avgDownPlan"] = avg_plan

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    # 友善表格
    print(f"\n{'=' * 56}")
    print(f" {name} ({ticker})  @ {result['asOfDate']}  收 {result['lastClose']}")
    print(f"{'=' * 56}")
    print(f" 基準價（{result['baseline']['source']}）：{baseline}")
    print(f" 區間低點（{args.window}日）：{low}")
    print(f" Δ = {delta}")
    print(f"\n 〔波浪目標〕    ×1.5 → {wave_targets[0]}    ×2.0 → {wave_targets[1]}    ×2.5 → {wave_targets[2]}")
    if fcost and fcost.get("cost"):
        print(f"\n 〔外資成本（{fcost['windowDays']}日 VWAP proxy）〕 {fcost['cost']}  "
              f"（淨買 {fcost['netBuyDays']} 天 / {fcost['netBuyLots']:,} 張）")
        print(f" 〔外資目標〕    ×1.2 → {foreign_targets[0]}    ×1.4 → {foreign_targets[1]}    ×1.7 → {foreign_targets[2]}")
    else:
        print("\n 〔外資成本〕 近期無淨買天，略過")
    if overlap:
        cons = ", ".join(str(o['consensus']) for o in overlap)
        print(f"\n ✅ 兩條腿重疊（共識）：{cons}")
    else:
        print("\n ⚠️  兩條腿沒對齊，看起來比較弱的訊號")
    if avg_plan:
        print(f"\n 〔攤平建議〕")
        if avg_plan.get("note"):
            print(f"  {avg_plan['note']}")
        else:
            print(f"  目前價 → 目標價 差 {avg_plan['toTargetGap']} 元")
            print(f"  建議加碼：{avg_plan['addShares']} 股")
            print(f"  加碼後總投入：{avg_plan['totalInvestment']:,.0f} 元")
            print(f"  加碼後平均成本：{avg_plan['resultingAvgCost']} 元（{avg_plan['resultingShares']} 股）")
    print(f"\n ⚠️  {result['disclaimer']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
