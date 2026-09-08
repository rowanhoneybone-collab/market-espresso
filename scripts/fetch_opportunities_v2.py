#!/usr/bin/env python3
import datetime as dt
import io
import json
import math
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

CONFIG_PATH = Path("config.json")
OUTPUT_PATH = Path("data/opportunities.json")
OUTPUT_PATH.parent.mkdir(exist_ok=True)

config = json.loads(CONFIG_PATH.read_text())
criteria = config.get("dividendOpportunityScreen", {}).get("criteria", {})
MIN_REVENUE_YEARS = int(criteria.get("minConsecutiveRevenueYears", 3))
MIN_ROA = float(criteria.get("minRoaPct", 10))
MIN_DIVIDEND_YEARS = int(criteria.get("minDividendGrowthYears", 10))
MAX_NET_DEBT_EBITDA = float(criteria.get("maxNetDebtToEbitda", 4))
MAX_PE = float(criteria.get("maxPe", 25))

NASDAQ_ACHIEVERS_PDF = "https://www.nasdaq.com/docs/index/DAATR"
FALLBACK_ACHIEVERS_URL = "https://dividendhistory.org/tags/dividend-achiever/"
TRADINGVIEW_SCAN_URL = "https://scanner.tradingview.com/america/scan"

HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/152 Safari/537.36 MarketEspresso/1.0",
    "Accept-Language": "en-US,en;q=0.9",
})

TV_COLUMNS = [
    "market_cap_basic",
    "price_earnings_current",
    "return_on_assets",
    "net_debt",
    "ebitda_ttm",
    "total_revenue_fy",
    "total_revenue_fy_h",
    "dividends_yield_current",
    "dividend_ex_date_upcoming",
    "dividend_amount_upcoming",
    "dividend_payment_date_upcoming",
]


def safe_num(value):
    try:
        if value is None or isinstance(value, bool):
            return None
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def normalize_symbol(symbol):
    return str(symbol or "").upper().strip().replace("/", ".").replace(" ", "")


def get_bytes(url, timeout=35):
    response = HTTP.get(url, timeout=timeout)
    response.raise_for_status()
    return response.content


def get_text(url, timeout=35):
    return get_bytes(url, timeout=timeout).decode("utf-8", errors="replace")


def parse_nasdaq_achievers_pdf(raw):
    reader = PdfReader(io.BytesIO(raw))
    rows = []
    # Nasdaq rows extract as: COMPANY NAME TICKER WEIGHT.
    pattern = re.compile(r"^(.*?)\s+([A-Z][A-Z0-9.\-]{0,11})\s+([0-9]+(?:\.[0-9]+)?)$")
    for page in reader.pages:
        text = page.extract_text() or ""
        for raw_line in text.splitlines():
            line = " ".join(raw_line.split())
            match = pattern.match(line)
            if not match:
                continue
            name, symbol, weight = match.groups()
            symbol = normalize_symbol(symbol)
            if name.lower().startswith("name symbol") or symbol in {"DAA", "DAATR"}:
                continue
            if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}", symbol):
                rows.append({"symbol": symbol, "name": name.strip() or symbol, "indexWeightPct": safe_num(weight)})
    deduped = {}
    for row in rows:
        deduped[row["symbol"]] = row
    return list(deduped.values())


def parse_fallback_achievers(html):
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        cells = [" ".join(c.stripped_strings).strip() for c in tr.find_all(["td", "th"])]
        if len(cells) < 5:
            continue
        market = cells[-2].upper()
        symbol = normalize_symbol(cells[-1])
        if market == "US" and re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}", symbol):
            rows.append({"symbol": symbol, "name": cells[1] if len(cells) > 1 else symbol, "indexWeightPct": None})
    deduped = {row["symbol"]: row for row in rows}
    return list(deduped.values())


def fetch_dividend_growth_universe():
    errors = []
    try:
        universe = parse_nasdaq_achievers_pdf(get_bytes(NASDAQ_ACHIEVERS_PDF))
        if len(universe) >= 300:
            return universe, "Nasdaq US Broad Dividend Achievers Index"
        errors.append(f"Nasdaq PDF returned only {len(universe)} usable constituents")
    except Exception as exc:
        errors.append(f"Nasdaq PDF: {exc}")

    try:
        universe = parse_fallback_achievers(get_text(FALLBACK_ACHIEVERS_URL))
        if len(universe) >= 100:
            return universe, "Dividend Achievers fallback list"
        errors.append(f"fallback list returned only {len(universe)} usable constituents")
    except Exception as exc:
        errors.append(f"fallback list: {exc}")

    raise RuntimeError("Dividend-growth universe unavailable: " + "; ".join(errors))


def extract_history(raw):
    if raw is None:
        return []
    if isinstance(raw, dict):
        for key in ("values", "data", "series"):
            if key in raw:
                return extract_history(raw[key])
        out = []
        for value in raw.values():
            num = safe_num(value)
            if num is not None:
                out.append(num)
        return out
    if not isinstance(raw, (list, tuple)):
        num = safe_num(raw)
        return [num] if num is not None else []
    out = []
    for item in raw:
        num = safe_num(item)
        if num is not None:
            out.append(num)
            continue
        if isinstance(item, (list, tuple)):
            nums = [safe_num(x) for x in item]
            nums = [x for x in nums if x is not None]
            if nums:
                out.append(nums[-1])
        elif isinstance(item, dict):
            for key in ("value", "v", "close"):
                num = safe_num(item.get(key))
                if num is not None:
                    out.append(num)
                    break
    return out


def orient_history(values, latest):
    values = [v for v in values if v is not None and v >= 0]
    if len(values) < 2 or latest is None or latest == 0:
        return values
    first_diff = abs(values[0] - latest) / abs(latest)
    last_diff = abs(values[-1] - latest) / abs(latest)
    if last_diff < first_diff:
        values.reverse()
    return values


def revenue_streak(values):
    streak = 0
    for newer, older in zip(values, values[1:]):
        if newer > older:
            streak += 1
        else:
            break
    return streak


def fetch_tradingview_market():
    payload = {
        "filter": [],
        "options": {"lang": "en"},
        "markets": ["america"],
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": TV_COLUMNS,
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
        "range": [0, 10000],
    }
    response = HTTP.post(TRADINGVIEW_SCAN_URL, json=payload, timeout=60)
    response.raise_for_status()
    data = response.json().get("data") or []
    if len(data) < 1000:
        raise RuntimeError(f"TradingView returned only {len(data)} U.S. market rows")

    market = {}
    for item in data:
        full_symbol = str(item.get("s") or "")
        ticker = normalize_symbol(full_symbol.split(":")[-1])
        values = item.get("d") or []
        if not ticker or len(values) < len(TV_COLUMNS):
            continue
        row = dict(zip(TV_COLUMNS, values))
        row["exchangeSymbol"] = full_symbol
        old = market.get(ticker)
        old_cap = safe_num(old.get("market_cap_basic")) if old else None
        new_cap = safe_num(row.get("market_cap_basic"))
        if old is None or (new_cap or -1) > (old_cap or -1):
            market[ticker] = row
    return market


def timestamp_to_date(value):
    num = safe_num(value)
    if num is None:
        return None
    try:
        return dt.datetime.fromtimestamp(num, tz=dt.timezone.utc).date().isoformat()
    except Exception:
        return None


def make_record(base, tv):
    pe = safe_num(tv.get("price_earnings_current"))
    roa = safe_num(tv.get("return_on_assets"))
    net_debt = safe_num(tv.get("net_debt"))
    ebitda = safe_num(tv.get("ebitda_ttm"))
    leverage = net_debt / ebitda if net_debt is not None and ebitda is not None and ebitda > 0 else None
    latest_revenue = safe_num(tv.get("total_revenue_fy"))
    history = orient_history(extract_history(tv.get("total_revenue_fy_h")), latest_revenue)
    if latest_revenue is not None:
        if not history:
            history = [latest_revenue]
        elif abs(history[0] - latest_revenue) / max(abs(latest_revenue), 1) > 0.01:
            history.insert(0, latest_revenue)
    return {
        **base,
        "exchangeSymbol": tv.get("exchangeSymbol"),
        "pe": pe,
        "roaPct": roa,
        "marketCap": safe_num(tv.get("market_cap_basic")),
        "netDebt": net_debt,
        "ebitda": ebitda,
        "netDebtToEbitda": leverage,
        "dividendGrowthYears": MIN_DIVIDEND_YEARS,
        "dividendGrowthYearsIsFloor": True,
        "dividendYieldPct": safe_num(tv.get("dividends_yield_current")),
        "nextExDate": timestamp_to_date(tv.get("dividend_ex_date_upcoming")),
        "nextDividendAmount": safe_num(tv.get("dividend_amount_upcoming")),
        "nextPaymentDate": timestamp_to_date(tv.get("dividend_payment_date_upcoming")),
        "revenueHistory": history[:8],
        "consecutiveRevenueGrowthYears": revenue_streak(history) if len(history) >= 2 else None,
    }


def pass_flags(record):
    return {
        "revenue": record.get("consecutiveRevenueGrowthYears") is not None and record["consecutiveRevenueGrowthYears"] >= MIN_REVENUE_YEARS,
        "roa": record.get("roaPct") is not None and record["roaPct"] >= MIN_ROA,
        "dividend": True,
        "debt": record.get("netDebtToEbitda") is not None and record["netDebtToEbitda"] < MAX_NET_DEBT_EBITDA,
        "pe": record.get("pe") is not None and record["pe"] > 0 and record["pe"] < MAX_PE,
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
    if record.get("consecutiveRevenueGrowthYears") is not None:
        score += min(record["consecutiveRevenueGrowthYears"], 8) * 2
    return flags, passed, round(score, 2)


def criteria_payload():
    return {
        "minConsecutiveRevenueYears": MIN_REVENUE_YEARS,
        "minRoaPct": MIN_ROA,
        "minDividendGrowthYears": MIN_DIVIDEND_YEARS,
        "maxNetDebtToEbitda": MAX_NET_DEBT_EBITDA,
        "maxPe": MAX_PE,
    }


def write_failure(message):
    previous = None
    try:
        previous = json.loads(OUTPUT_PATH.read_text()) if OUTPUT_PATH.exists() else None
    except Exception:
        pass
    keep_previous = previous and previous.get("status") == "ok"
    OUTPUT_PATH.write_text(json.dumps({
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "error",
        "error": message,
        "method": "Doc's Formula for Buying Winning Stocks",
        "universe": "Nasdaq US Broad Dividend Achievers (10+ consecutive years of dividend growth)",
        "universeCount": previous.get("universeCount", 0) if keep_previous else 0,
        "screenedCount": previous.get("screenedCount", 0) if keep_previous else 0,
        "winnerCount": len(previous.get("winners", [])) if keep_previous else 0,
        "criteria": criteria_payload(),
        "winners": previous.get("winners", []) if keep_previous else [],
        "nearMisses": previous.get("nearMisses", []) if keep_previous else [],
        "previousGeneratedAt": previous.get("generatedAt") if keep_previous else None,
    }, indent=2))


def main():
    universe, universe_source = fetch_dividend_growth_universe()
    print(f"Loaded {len(universe)} dividend achievers from {universe_source}")

    market = fetch_tradingview_market()
    print(f"Loaded {len(market)} U.S. market symbols from bulk fundamentals feed")

    evaluated = []
    unmatched = []
    incomplete = []
    for base in universe:
        tv = market.get(base["symbol"]) or market.get(base["symbol"].replace(".", "-"))
        if not tv:
            unmatched.append(base["symbol"])
            continue
        record = make_record(base, tv)
        flags, passed, score = score_record(record)
        record["passes"] = flags
        record["passCount"] = passed
        record["score"] = score
        record["meetsFormula"] = passed == 5
        record["dataComplete"] = all([
            record.get("pe") is not None,
            record.get("roaPct") is not None,
            record.get("netDebtToEbitda") is not None,
            record.get("consecutiveRevenueGrowthYears") is not None,
        ])
        if not record["dataComplete"]:
            incomplete.append(record["symbol"])
        evaluated.append(record)

    complete = [r for r in evaluated if r.get("dataComplete")]
    winners = sorted(
        [r for r in complete if r.get("meetsFormula")],
        key=lambda r: (-r.get("score", 0), r.get("pe") or 999, r["symbol"]),
    )
    near = sorted(
        [r for r in complete if r.get("passCount") == 4],
        key=lambda r: (-r.get("score", 0), r.get("pe") or 999, r["symbol"]),
    )

    output = {
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "ok",
        "method": "Doc's Formula for Buying Winning Stocks",
        "universe": "Nasdaq US Broad Dividend Achievers (10+ consecutive years of dividend growth)",
        "universeSource": universe_source,
        "universeCount": len(universe),
        "matchedCount": len(evaluated),
        "screenedCount": len(complete),
        "winnerCount": len(winners),
        "criteria": criteria_payload(),
        "winners": winners[:25],
        "nearMisses": near[:25],
        "unmatchedCount": len(unmatched),
        "incompleteCount": len(incomplete),
        "sourceNotes": [
            "Dividend-growth universe: Nasdaq US Broad Dividend Achievers, whose constituents have at least 10 consecutive years of increasing annual regular dividends.",
            "P/E, ROA, net debt, EBITDA, annual revenue history, dividend yield and upcoming dividend dates: TradingView U.S. stock screener fundamentals.",
            "Net debt/EBITDA is calculated as net debt divided by trailing EBITDA.",
            "Revenue-growth streak counts consecutive annual revenue increases from the latest completed fiscal year backward.",
            "The dividend-growth-years value is displayed as a 10+ year floor because index membership establishes the minimum rather than the exact streak length."
        ],
    }
    OUTPUT_PATH.write_text(json.dumps(output, indent=2))
    print(f"Wrote {OUTPUT_PATH}: {len(winners)} 5/5 winners, {len(near)} 4/5 near misses, {len(incomplete)} incomplete")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        write_failure(str(exc))
        print(f"Screener failed: {exc}")
        raise
