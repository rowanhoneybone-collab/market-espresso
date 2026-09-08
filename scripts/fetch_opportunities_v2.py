#!/usr/bin/env python3
import datetime as dt
import json
import os
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

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
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "").strip()
REQUEST_DELAY = float(os.environ.get("OPPORTUNITY_REQUEST_DELAY", "1.05"))

VIG_HOLDINGS_URL = "https://companiesmarketcap.com/vanguard-dividend-appreciation-index-fund-etf-shares/holdings/"
FINNHUB_METRIC_URL = "https://finnhub.io/api/v1/stock/metric"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": "MarketEspresso/1.0 dividend research dashboard (contact: rowanhoneybone@gmail.com)",
    "Accept-Language": "en-US,en;q=0.9",
})
REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
]


def safe_num(value):
    try:
        if value is None or isinstance(value, bool):
            return None
        value = float(value)
        return value if value == value and abs(value) != float("inf") else None
    except (TypeError, ValueError):
        return None


def first_metric(metrics, *keys):
    for key in keys:
        value = safe_num(metrics.get(key))
        if value is not None:
            return value
    return None


def get_json(url, *, params=None, timeout=30):
    response = HTTP.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def get_text(url, *, timeout=30):
    response = HTTP.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def normalize_symbol(symbol):
    return str(symbol or "").upper().strip().replace("/", ".")


def fetch_dividend_growth_universe():
    """Current VIG holdings: a broad U.S. universe that already clears 10 years of dividend growth."""
    soup = BeautifulSoup(get_text(VIG_HOLDINGS_URL), "html.parser")
    universe = []
    for table in soup.find_all("table"):
        headers = [" ".join(x.stripped_strings).strip().lower() for x in table.find_all("th")]
        if not any("ticker" in h for h in headers):
            continue
        for tr in table.find_all("tr"):
            cells = [" ".join(c.stripped_strings).strip() for c in tr.find_all("td")]
            if len(cells) < 3:
                continue
            ticker = normalize_symbol(cells[2])
            name = cells[1].strip()
            if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}", ticker) and ticker not in {"USD", "VIG"}:
                universe.append({"symbol": ticker, "name": name or ticker})
        if len(universe) >= 100:
            break
    deduped = {item["symbol"]: item for item in universe}
    out = list(deduped.values())
    if len(out) < 100:
        raise RuntimeError(f"Dividend-growth holdings source returned only {len(out)} usable stocks.")
    return out


def fetch_finnhub_metric(symbol):
    payload = get_json(
        FINNHUB_METRIC_URL,
        params={"symbol": symbol.replace(".", "-"), "metric": "all", "token": FINNHUB_API_KEY},
        timeout=25,
    )
    metrics = payload.get("metric") or {}
    if not metrics:
        return {"metricError": "Finnhub returned no basic financial metrics."}

    pe = first_metric(metrics, "peBasicExclExtraTTM", "peTTM", "peExclExtraTTM", "peAnnual")
    roa = first_metric(metrics, "roaTTM", "roaRfy", "roa5Y")
    market_cap = first_metric(metrics, "marketCapitalization")
    enterprise_value = first_metric(metrics, "enterpriseValue")
    net_debt = first_metric(metrics, "netDebtInterim", "netDebtAnnual")
    ebitda_per_share = first_metric(metrics, "ebitdPerShareTTM", "ebitdPerShareAnnual")
    eps = first_metric(metrics, "epsBasicExclExtraItemsTTM", "epsExclExtraItemsTTM", "epsTTM")
    dividend_yield = first_metric(metrics, "dividendYieldIndicatedAnnual", "currentDividendYieldTTM")

    implied_price = pe * eps if pe and eps and pe > 0 and eps > 0 else None
    shares_millions = market_cap / implied_price if market_cap is not None and implied_price else None
    ebitda_millions = ebitda_per_share * shares_millions if ebitda_per_share is not None and shares_millions else None
    if net_debt is None and enterprise_value is not None and market_cap is not None:
        net_debt = enterprise_value - market_cap
    leverage = net_debt / ebitda_millions if net_debt is not None and ebitda_millions and ebitda_millions > 0 else None

    return {
        "pe": pe,
        "roaPct": roa,
        "marketCap": market_cap,
        "enterpriseValue": enterprise_value,
        "netDebt": net_debt,
        "ebitdaMillions": ebitda_millions,
        "netDebtToEbitda": leverage,
        "dividendYieldPct": dividend_yield,
        "revenueGrowth3Y": first_metric(metrics, "revenueGrowth3Y"),
        "metricError": None,
    }


def load_sec_ticker_map():
    raw = get_json(SEC_TICKERS_URL)
    mapping = {}
    for item in raw.values():
        ticker = normalize_symbol(item.get("ticker"))
        cik = item.get("cik_str")
        if ticker and cik is not None:
            padded = str(cik).zfill(10)
            mapping[ticker] = padded
            mapping[ticker.replace("-", ".")] = padded
            mapping[ticker.replace(".", "-")] = padded
    return mapping


def annual_values_for_tag(companyfacts, tag):
    fact = (((companyfacts.get("facts") or {}).get("us-gaap") or {}).get(tag) or {})
    rows = (fact.get("units") or {}).get("USD") or []
    candidates = []
    for row in rows:
        if row.get("form") not in {"10-K", "10-K/A"} or not row.get("start") or not row.get("end"):
            continue
        try:
            start = dt.date.fromisoformat(row["start"][:10])
            end = dt.date.fromisoformat(row["end"][:10])
        except Exception:
            continue
        if not 250 <= (end - start).days <= 440:
            continue
        value = safe_num(row.get("val"))
        if value is not None:
            candidates.append({"end": row["end"][:10], "filed": str(row.get("filed") or ""), "value": value})
    by_end = {}
    for row in candidates:
        old = by_end.get(row["end"])
        if old is None or row["filed"] > old["filed"]:
            by_end[row["end"]] = row
    return [x["value"] for x in sorted(by_end.values(), key=lambda x: x["end"], reverse=True)]


def revenue_history_from_sec(symbol, cik_map):
    cik = cik_map.get(symbol) or cik_map.get(symbol.replace(".", "-"))
    if not cik:
        return [], "No SEC CIK mapping found."
    try:
        facts = get_json(SEC_COMPANYFACTS_URL.format(cik=cik), timeout=35)
    except Exception as exc:
        return [], f"SEC companyfacts request failed: {exc}"
    best = []
    for tag in REVENUE_TAGS:
        values = annual_values_for_tag(facts, tag)
        if len(values) > len(best):
            best = values
    if len(best) < MIN_REVENUE_YEARS + 1:
        return best, f"Only {len(best)} annual revenue observations found in SEC filings."
    return best[:8], None


def revenue_streak(values):
    streak = 0
    for newer, older in zip(values, values[1:]):
        if newer > older:
            streak += 1
        else:
            break
    return streak


def non_revenue_flags(record):
    pe, roa, leverage = record.get("pe"), record.get("roaPct"), record.get("netDebtToEbitda")
    return {
        "roa": roa is not None and roa >= MIN_ROA,
        "dividend": True,
        "debt": leverage is not None and leverage < MAX_NET_DEBT_EBITDA,
        "pe": pe is not None and pe > 0 and pe < MAX_PE,
    }


def pass_flags(record):
    flags = non_revenue_flags(record)
    flags["revenue"] = record.get("consecutiveRevenueGrowthYears") is not None and record["consecutiveRevenueGrowthYears"] >= MIN_REVENUE_YEARS
    return {k: flags[k] for k in ("revenue", "roa", "dividend", "debt", "pe")}


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
        previous = None
    keep_previous = previous and previous.get("status") == "ok"
    OUTPUT_PATH.write_text(json.dumps({
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "error",
        "error": message,
        "method": "Doc's Formula for Buying Winning Stocks",
        "universe": "S&P U.S. Dividend Growers / VIG holdings (10+ consecutive years of dividend growth)",
        "universeCount": 0,
        "screenedCount": 0,
        "winnerCount": len(previous.get("winners", [])) if keep_previous else 0,
        "criteria": criteria_payload(),
        "winners": previous.get("winners", []) if keep_previous else [],
        "nearMisses": previous.get("nearMisses", []) if keep_previous else [],
        "previousGeneratedAt": previous.get("generatedAt") if keep_previous else None,
    }, indent=2))


def main():
    if not FINNHUB_API_KEY:
        raise RuntimeError("FINNHUB_API_KEY is not configured for the opportunity screen.")
    universe = fetch_dividend_growth_universe()
    print(f"Loaded {len(universe)} dividend-growth stocks")

    records, metric_errors = [], []
    for idx, base in enumerate(universe, 1):
        try:
            metric = fetch_finnhub_metric(base["symbol"])
            record = {**base, **metric}
        except Exception as exc:
            record = {**base, "metricError": str(exc)}
        record["dividendGrowthYears"] = MIN_DIVIDEND_YEARS
        record["dividendGrowthYearsIsFloor"] = True
        record["nonRevenuePassCount"] = sum(non_revenue_flags(record).values())
        records.append(record)
        if record.get("metricError"):
            metric_errors.append(record)
        if idx % 25 == 0 or idx == len(universe):
            print(f"Finnhub fundamentals: {idx}/{len(universe)}")
        time.sleep(REQUEST_DELAY)

    # Dividend is already a pass. A stock needs at least two of the remaining
    # ROA/debt/P-E rules to have a chance at being a 4/5 near miss or 5/5 pass.
    deep_candidates = [r for r in records if r.get("nonRevenuePassCount", 0) >= 3]
    print(f"SEC revenue checks required for {len(deep_candidates)} candidates")
    cik_map = load_sec_ticker_map()

    evaluated, revenue_errors = [], []
    for idx, record in enumerate(deep_candidates, 1):
        values, error = revenue_history_from_sec(record["symbol"], cik_map)
        record["revenueHistory"] = values[:6]
        record["consecutiveRevenueGrowthYears"] = revenue_streak(values) if values else None
        record["revenueError"] = error
        flags, passed, score = score_record(record)
        record["passes"] = flags
        record["passCount"] = passed
        record["score"] = score
        record["dataComplete"] = error is None
        record["meetsFormula"] = error is None and passed == 5
        evaluated.append(record)
        if error:
            revenue_errors.append(record)
        if idx % 20 == 0 or idx == len(deep_candidates):
            print(f"SEC revenue checks: {idx}/{len(deep_candidates)}")
        time.sleep(0.12)

    complete = [r for r in evaluated if r.get("dataComplete")]
    winners = sorted([r for r in complete if r.get("meetsFormula")], key=lambda r: (-r.get("score", 0), r.get("pe") or 999, r["symbol"]))
    near = sorted([r for r in complete if r.get("passCount") == 4], key=lambda r: (-r.get("score", 0), r.get("pe") or 999, r["symbol"]))

    output = {
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "ok",
        "method": "Doc's Formula for Buying Winning Stocks",
        "universe": "S&P U.S. Dividend Growers / VIG holdings (10+ consecutive years of dividend growth)",
        "marketStockCount": len(records),
        "universeCount": len(universe),
        "screenedCount": len(complete),
        "deepCandidateCount": len(deep_candidates),
        "winnerCount": len(winners),
        "criteria": criteria_payload(),
        "winners": winners[:25],
        "nearMisses": near[:25],
        "metricErrorCount": len(metric_errors),
        "revenueErrorCount": len(revenue_errors),
        "sourceNotes": [
            "Dividend-growth universe: current VIG holdings, tracking the S&P U.S. Dividend Growers Index (10+ consecutive years of dividend growth).",
            "P/E and ROA: Finnhub basic financials.",
            "Net debt/EBITDA: Finnhub net debt and EBITDA/share inputs; enterprise value minus market cap is used when direct net debt is unavailable.",
            "Revenue streak: annual revenue reported in SEC 10-K filings via SEC companyfacts.",
            "The S&P U.S. Dividend Growers Index excludes the highest-yielding 25% of otherwise eligible companies, so this is a broad quality-growth universe rather than every U.S. dividend stock."
        ],
    }
    OUTPUT_PATH.write_text(json.dumps(output, indent=2))
    print(f"Wrote {OUTPUT_PATH}: {len(winners)} winners, {len(near)} near misses")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        write_failure(str(exc))
        print(f"Screener failed: {exc}")
        raise
