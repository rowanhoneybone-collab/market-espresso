#!/usr/bin/env python3
import concurrent.futures
import datetime as dt
import json
import math
import os
import re
import time
import urllib.parse
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
MAX_WORKERS = int(os.environ.get("OPPORTUNITY_WORKERS", "6"))

ACHIEVERS_URL = "https://dividendhistory.org/tags/dividend-achiever/"
STOCK_BASE = "https://stockanalysis.com/stocks/{symbol}/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_text(url, timeout=25, retries=3):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            last = exc
            time.sleep(0.8 * (attempt + 1))
    raise last


def parse_number(value):
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"n/a", "na", "-", "—", "none"}:
        return None
    neg = text.startswith("-")
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


def table_metric(soup, label):
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        first = " ".join(cells[0].stripped_strings).strip()
        if first == label:
            return " ".join(cells[-1].stripped_strings).strip()
    return None


def fetch_achievers():
    html = fetch_text(ACHIEVERS_URL)
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 5:
            continue
        texts = [" ".join(cell.stripped_strings).strip() for cell in cells]
        market = texts[-2].upper() if len(texts) >= 2 else ""
        symbol = texts[-1].upper().strip() if texts else ""
        if market != "US" or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol):
            continue
        rows.append({
            "symbol": symbol,
            "name": texts[1] if len(texts) > 1 else symbol,
            "listedYield": parse_number(texts[2]) if len(texts) > 2 else None,
            "listedExDate": texts[3] if len(texts) > 3 else None,
        })
    # Preserve order while de-duplicating.
    seen = set()
    out = []
    for item in rows:
        if item["symbol"] not in seen:
            seen.add(item["symbol"])
            out.append(item)
    return out


def stockanalysis_symbol(symbol):
    return symbol.lower().replace(".", "-")


def fetch_statistics(candidate):
    symbol = candidate["symbol"]
    slug = stockanalysis_symbol(symbol)
    record = {
        **candidate,
        "pe": None,
        "roaPct": None,
        "dividendGrowthYears": None,
        "cash": None,
        "totalDebt": None,
        "ebitda": None,
        "netDebt": None,
        "netDebtToEbitda": None,
        "quickPass": False,
        "statisticsUrl": STOCK_BASE.format(symbol=slug) + "statistics/",
        "error": None,
    }
    try:
        html = fetch_text(record["statisticsUrl"])
        soup = BeautifulSoup(html, "html.parser")
        record["pe"] = parse_number(table_metric(soup, "PE Ratio"))
        record["roaPct"] = parse_number(table_metric(soup, "Return on Assets (ROA)"))
        record["dividendGrowthYears"] = parse_number(table_metric(soup, "Years of Dividend Growth"))
        record["cash"] = parse_number(table_metric(soup, "Cash & Cash Equivalents"))
        record["totalDebt"] = parse_number(table_metric(soup, "Total Debt"))
        record["ebitda"] = parse_number(table_metric(soup, "EBITDA"))

        if record["totalDebt"] is not None and record["cash"] is not None:
            record["netDebt"] = record["totalDebt"] - record["cash"]
        if record["netDebt"] is not None and record["ebitda"] not in (None, 0):
            record["netDebtToEbitda"] = record["netDebt"] / record["ebitda"]

        record["quickPass"] = all([
            record["pe"] is not None and record["pe"] < MAX_PE,
            record["roaPct"] is not None and record["roaPct"] >= MIN_ROA,
            record["dividendGrowthYears"] is not None and record["dividendGrowthYears"] >= MIN_DIVIDEND_YEARS,
            record["netDebtToEbitda"] is not None and record["netDebtToEbitda"] < MAX_NET_DEBT_EBITDA,
        ])
    except Exception as exc:
        record["error"] = str(exc)
    return record


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
                # Financials overview is ordered TTM/current first, then fiscal years newest -> oldest.
                # Drop the TTM/current value and use annual fiscal-year values.
                annual = nums[1:] if len(nums) >= 5 else nums
                return annual
    return []


def revenue_streak(values):
    # values are newest -> oldest. Count consecutive YoY increases from the latest fiscal year backward.
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
        html = fetch_text(url)
        values = parse_revenue_history(html)
        out["revenueHistory"] = values[:6]
        out["consecutiveRevenueGrowthYears"] = revenue_streak(values) if values else None
    except Exception as exc:
        out["error"] = (out.get("error") + "; " if out.get("error") else "") + str(exc)
    return out


def pass_flags(record):
    return {
        "revenue": record.get("consecutiveRevenueGrowthYears") is not None and record["consecutiveRevenueGrowthYears"] >= MIN_REVENUE_YEARS,
        "roa": record.get("roaPct") is not None and record["roaPct"] >= MIN_ROA,
        "dividend": record.get("dividendGrowthYears") is not None and record["dividendGrowthYears"] >= MIN_DIVIDEND_YEARS,
        "debt": record.get("netDebtToEbitda") is not None and record["netDebtToEbitda"] < MAX_NET_DEBT_EBITDA,
        "pe": record.get("pe") is not None and record["pe"] < MAX_PE,
    }


def score_record(record):
    flags = pass_flags(record)
    passed = sum(flags.values())
    # Tie-breaker rewards stronger profitability, lower valuation/leverage and longer streaks.
    score = passed * 100
    if record.get("roaPct") is not None:
        score += min(record["roaPct"], 40) * 0.5
    if record.get("pe") is not None:
        score += max(0, 30 - record["pe"])
    if record.get("netDebtToEbitda") is not None:
        score += max(0, 5 - record["netDebtToEbitda"]) * 2
    if record.get("dividendGrowthYears") is not None:
        score += min(record["dividendGrowthYears"], 60) * 0.15
    if record.get("consecutiveRevenueGrowthYears") is not None:
        score += min(record["consecutiveRevenueGrowthYears"], 8) * 2
    return flags, passed, round(score, 2)


def main():
    achievers = fetch_achievers()
    print(f"Loaded {len(achievers)} US dividend achiever candidates")

    stats = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for idx, record in enumerate(pool.map(fetch_statistics, achievers), 1):
            stats.append(record)
            if idx % 50 == 0:
                print(f"Statistics checked: {idx}/{len(achievers)}")

    # Only fetch annual revenue history for names that pass the other four requirements.
    quick = [r for r in stats if r.get("quickPass")]
    print(f"{len(quick)} candidates passed ROA, dividend streak, leverage and P/E prefilter")

    deep = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for record in pool.map(fetch_revenue, quick):
            deep.append(record)

    deep_by_symbol = {r["symbol"]: r for r in deep}
    combined = []
    for record in stats:
        full = deep_by_symbol.get(record["symbol"], record)
        flags, passed, score = score_record(full)
        full["passes"] = flags
        full["passCount"] = passed
        full["score"] = score
        full["meetsFormula"] = passed == 5
        combined.append(full)

    winners = [r for r in combined if r.get("meetsFormula")]
    winners.sort(key=lambda r: (-r.get("score", 0), r.get("pe") or 999, r["symbol"]))

    near = [r for r in combined if r.get("passCount") == 4]
    # A quick-pass name that has not yet passed revenue is the most useful near miss.
    near.sort(key=lambda r: (-r.get("score", 0), r.get("pe") or 999, r["symbol"]))

    errors = [r for r in combined if r.get("error")]
    output = {
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "method": "Doc's Formula for Buying Winning Stocks",
        "universe": "US Dividend Achievers (10+ consecutive years of dividend growth)",
        "universeCount": len(achievers),
        "screenedCount": len(combined),
        "quickPassCount": len(quick),
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
            "Dividend-growth universe: DividendHistory.org Dividend Achievers tag.",
            "Fundamental metrics and annual financials: public StockAnalysis.com company pages.",
            "Net debt/EBITDA is calculated as (Total Debt - Cash & Cash Equivalents) / EBITDA.",
            "Revenue-growth streak counts consecutive annual increases from the latest completed fiscal year backward.",
        ],
    }
    OUTPUT_PATH.write_text(json.dumps(output, indent=2))
    print(f"Wrote {OUTPUT_PATH}: {len(winners)} winners, {len(near)} near misses, {len(errors)} fetch errors")


if __name__ == "__main__":
    main()
