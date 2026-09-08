#!/usr/bin/env python3
import concurrent.futures
import datetime as dt
import json
import os
import re
import time
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup

CONFIG_PATH = Path("config.json")
OUTPUT_PATH = Path("data/opportunities.json")
OUTPUT_PATH.parent.mkdir(exist_ok=True)

config = json.loads(CONFIG_PATH.read_text())
screen = config.get("dividendOpportunityScreen", {})
criteria = screen.get("criteria", {})

MIN_REVENUE_YEARS = int(criteria.get("minConsecutiveRevenueYears", 3))
MIN_ROA = float(criteria.get("minRoaPct", 10))
MIN_DIVIDEND_YEARS = int(criteria.get("minDividendGrowthYears", 10))
MAX_NET_DEBT_EBITDA = float(criteria.get("maxNetDebtToEbitda", 4))
MAX_PE = float(criteria.get("maxPe", 25))
MAX_WORKERS = int(os.environ.get("OPPORTUNITY_WORKERS", "8"))

SCREENER_BASE = "https://stockanalysis.com/api/screener/s"
STOCK_BASE = "https://stockanalysis.com/stocks/{symbol}/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "application/json,text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

METRICS = {
    "roaPct": "roa",
    "dividendGrowthYears": "dividendGrowthYears",
    "cash": "cash",
    "totalDebt": "debt",
    "ebitda": "ebitda",
    "revenueGrowth3Y": "revenueGrowth3Y",
}


def fetch_bytes(url, timeout=20, retries=3):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            last = exc
            time.sleep(0.6 * (attempt + 1))
    raise last


def fetch_json(url):
    return json.loads(fetch_bytes(url).decode("utf-8", errors="replace"))


def fetch_text(url):
    return fetch_bytes(url).decode("utf-8", errors="replace")


def parse_number(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"n/a", "na", "-", "—", "none", "null"}:
        return None
    text = text.replace("$", "").replace("%", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    num = float(match.group())
    suffix = text.upper().strip()[-1:] if text else ""
    if suffix == "T":
        num *= 1_000_000_000_000
    elif suffix == "B":
        num *= 1_000_000_000
    elif suffix == "M":
        num *= 1_000_000
    elif suffix == "K":
        num *= 1_000
    return num


def stockanalysis_symbol(symbol):
    return symbol.lower().replace(".", "-")


def payload_rows(payload):
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        rows = data.get("data")
        if isinstance(rows, list):
            return rows
    return []


def fetch_initial_universe():
    rows = payload_rows(fetch_json(f"{SCREENER_BASE}/i"))
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("s") or "").upper().strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}", symbol):
            continue
        out[symbol] = {
            "symbol": symbol,
            "name": row.get("n") or symbol,
            "pe": parse_number(row.get("peRatio")),
            "marketCap": parse_number(row.get("marketCap")),
            "price": parse_number(row.get("price")),
            "industry": row.get("industry"),
            "error": None,
        }
    return out


def fetch_metric(metric_name):
    rows = payload_rows(fetch_json(f"{SCREENER_BASE}/d/{metric_name}"))
    out = {}
    for row in rows:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            symbol = str(row[0] or "").upper().strip()
            out[symbol] = parse_number(row[1])
        elif isinstance(row, dict):
            symbol = str(row.get("s") or row.get("symbol") or "").upper().strip()
            value = row.get(metric_name)
            if value is None:
                value = row.get("v") if "v" in row else row.get("value")
            if symbol:
                out[symbol] = parse_number(value)
    return out


def parse_revenue_history(html):
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        headers = [" ".join(x.stripped_strings).strip() for x in table.find_all("th")]
        if not any(h.startswith("FY ") for h in headers):
            continue
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 4:
                continue
            label = " ".join(cells[0].stripped_strings).strip()
            if not label.startswith("Revenue") or label == "Revenue Growth":
                continue
            values = [" ".join(c.stripped_strings).strip() for c in cells[1:]]
            nums = [parse_number(v) for v in values]
            nums = [v for v in nums if v is not None]
            if len(nums) >= 4:
                # StockAnalysis financials are ordered TTM first, then completed fiscal years newest -> oldest.
                return nums[1:] if len(nums) >= 5 else nums
    return []


def revenue_streak(values):
    streak = 0
    for newer, older in zip(values, values[1:]):
        if newer > older:
            streak += 1
        else:
            break
    return streak


def fetch_revenue(record):
    slug = stockanalysis_symbol(record["symbol"])
    url = STOCK_BASE.format(symbol=slug) + "financials/"
    out = {**record, "financialsUrl": url, "revenueHistory": [], "consecutiveRevenueGrowthYears": None}
    try:
        values = parse_revenue_history(fetch_text(url))
        out["revenueHistory"] = values[:6]
        out["consecutiveRevenueGrowthYears"] = revenue_streak(values) if values else None
        if not values:
            out["error"] = "Annual revenue history could not be parsed."
    except Exception as exc:
        out["error"] = str(exc)
    return out


def non_revenue_flags(record):
    pe = record.get("pe")
    roa = record.get("roaPct")
    div_years = record.get("dividendGrowthYears")
    leverage = record.get("netDebtToEbitda")
    return {
        "roa": roa is not None and roa >= MIN_ROA,
        "dividend": div_years is not None and div_years >= MIN_DIVIDEND_YEARS,
        "debt": leverage is not None and leverage < MAX_NET_DEBT_EBITDA,
        "pe": pe is not None and pe > 0 and pe < MAX_PE,
    }


def pass_flags(record):
    flags = non_revenue_flags(record)
    flags["revenue"] = (
        record.get("consecutiveRevenueGrowthYears") is not None
        and record["consecutiveRevenueGrowthYears"] >= MIN_REVENUE_YEARS
    )
    return {
        "revenue": flags["revenue"],
        "roa": flags["roa"],
        "dividend": flags["dividend"],
        "debt": flags["debt"],
        "pe": flags["pe"],
    }


def score_record(record):
    flags = pass_flags(record)
    passed = sum(flags.values())
    score = passed * 100
    if record.get("roaPct") is not None:
        score += min(max(record["roaPct"], 0), 40) * 0.5
    if record.get("pe") is not None:
        score += max(0, 30 - record["pe"])
    if record.get("netDebtToEbitda") is not None:
        score += max(0, 5 - record["netDebtToEbitda"]) * 2
    if record.get("dividendGrowthYears") is not None:
        score += min(record["dividendGrowthYears"], 60) * 0.15
    if record.get("consecutiveRevenueGrowthYears") is not None:
        score += min(record["consecutiveRevenueGrowthYears"], 8) * 2
    return flags, passed, round(score, 2)


def write_failure(message):
    OUTPUT_PATH.write_text(json.dumps({
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "error",
        "error": message,
        "method": "Doc's Formula for Buying Winning Stocks",
        "universe": "U.S.-listed stocks with 10+ consecutive years of dividend growth",
        "universeCount": 0,
        "screenedCount": 0,
        "quickPassCount": 0,
        "winnerCount": 0,
        "criteria": {
            "minConsecutiveRevenueYears": MIN_REVENUE_YEARS,
            "minRoaPct": MIN_ROA,
            "minDividendGrowthYears": MIN_DIVIDEND_YEARS,
            "maxNetDebtToEbitda": MAX_NET_DEBT_EBITDA,
            "maxPe": MAX_PE,
        },
        "winners": [],
        "nearMisses": [],
    }, indent=2))


def main():
    universe = fetch_initial_universe()
    if not universe:
        raise RuntimeError("StockAnalysis screener returned an empty stock universe.")
    print(f"Loaded {len(universe)} stocks from the market-wide screener feed")

    metric_maps = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(METRICS)) as pool:
        futures = {pool.submit(fetch_metric, endpoint): field for field, endpoint in METRICS.items()}
        for future in concurrent.futures.as_completed(futures):
            field = futures[future]
            metric_maps[field] = future.result()
            print(f"Loaded {field}: {len(metric_maps[field])} symbols")

    records = []
    for symbol, base in universe.items():
        r = dict(base)
        for field in METRICS:
            r[field] = metric_maps.get(field, {}).get(symbol)
        cash, debt, ebitda = r.get("cash"), r.get("totalDebt"), r.get("ebitda")
        r["netDebt"] = debt - cash if debt is not None and cash is not None else None
        r["netDebtToEbitda"] = (
            r["netDebt"] / ebitda
            if r["netDebt"] is not None and ebitda is not None and ebitda > 0
            else None
        )
        nr = non_revenue_flags(r)
        r["nonRevenuePassCount"] = sum(nr.values())
        records.append(r)

    dividend_universe = [r for r in records if r.get("dividendGrowthYears") is not None and r["dividendGrowthYears"] >= MIN_DIVIDEND_YEARS]
    print(f"Dividend-growth universe: {len(dividend_universe)} names with {MIN_DIVIDEND_YEARS}+ years")

    # To identify both 5/5 winners and genuine 4/5 near misses, inspect revenue history
    # for names passing at least 3 of the other 4 rules. A non-positive 3Y revenue CAGR
    # cannot satisfy three consecutive annual increases, so it is a safe pre-filter.
    deep_candidates = [
        r for r in dividend_universe
        if r.get("nonRevenuePassCount", 0) >= 3
        and (r.get("revenueGrowth3Y") is None or r["revenueGrowth3Y"] > 0)
    ]
    print(f"Revenue-history checks required for {len(deep_candidates)} candidates")

    deep = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for record in pool.map(fetch_revenue, deep_candidates):
            deep.append(record)

    evaluated = []
    for record in deep:
        flags, passed, score = score_record(record)
        record["passes"] = flags
        record["passCount"] = passed
        record["score"] = score
        record["meetsFormula"] = passed == 5
        evaluated.append(record)

    winners = [r for r in evaluated if r["meetsFormula"]]
    winners.sort(key=lambda r: (-r.get("score", 0), r.get("pe") or 999, r["symbol"]))

    near = [r for r in evaluated if r.get("passCount") == 4]
    near.sort(key=lambda r: (-r.get("score", 0), r.get("pe") or 999, r["symbol"]))

    errors = [r for r in evaluated if r.get("error")]
    output = {
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "ok",
        "method": "Doc's Formula for Buying Winning Stocks",
        "universe": f"U.S.-listed stocks with {MIN_DIVIDEND_YEARS}+ consecutive years of dividend growth",
        "marketStockCount": len(records),
        "universeCount": len(dividend_universe),
        "screenedCount": len(evaluated),
        "quickPassCount": len([r for r in evaluated if r.get("nonRevenuePassCount") == 4]),
        "winnerCount": len(winners),
        "criteria": {
            "minConsecutiveRevenueYears": MIN_REVENUE_YEARS,
            "minRoaPct": MIN_ROA,
            "minDividendGrowthYears": MIN_DIVIDEND_YEARS,
            "maxNetDebtToEbitda": MAX_NET_DEBT_EBITDA,
            "maxPe": MAX_PE,
        },
        "winners": winners[:25],
        "nearMisses": near[:20],
        "errorCount": len(errors),
        "sourceNotes": [
            "Market-wide fundamentals: public StockAnalysis screener feeds.",
            "Annual revenue history: public StockAnalysis company financial pages for pre-qualified candidates only.",
            "Net debt/EBITDA is calculated as (Total Debt - Cash & Cash Equivalents) / EBITDA.",
            "Revenue-growth streak counts consecutive annual increases from the latest completed fiscal year backward.",
        ],
    }
    OUTPUT_PATH.write_text(json.dumps(output, indent=2))
    print(f"Wrote {OUTPUT_PATH}: {len(winners)} winners, {len(near)} near misses, {len(errors)} revenue fetch/parse errors")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        write_failure(str(exc))
        print(f"Screener failed: {exc}")
        raise
