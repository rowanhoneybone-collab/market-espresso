#!/usr/bin/env python3
import datetime as dt
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN = os.environ.get("FINNHUB_API_KEY", "").strip()
DATA_PATH = Path("data/edition.json")
CONFIG_PATH = Path("config.json")
DATA_PATH.parent.mkdir(exist_ok=True)

config = json.loads(CONFIG_PATH.read_text())
markets_cfg = config.get("markets", [])
holdings_cfg = config.get("holdings", [])
dividend_cfg = config.get("dividendWatchlist", [])
watch_cfg = markets_cfg + holdings_cfg
news_symbols = [x["symbol"] for x in holdings_cfg if x.get("news")]

if not TOKEN:
    if not DATA_PATH.exists():
        DATA_PATH.write_text(json.dumps({
            "generatedAt": None,
            "market": [],
            "news": [],
            "portfolioNews": [],
            "dividends": [],
            "config": config
        }, indent=2))
    print("FINNHUB_API_KEY is not configured yet; publishing the site with placeholder data.")
    raise SystemExit(0)


def get_json(url, headers=None, timeout=25):
    req_headers = {"User-Agent": "MarketEspresso/1.0"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def api(path, **params):
    params["token"] = TOKEN
    return "https://finnhub.io/api/v1/" + path + "?" + urllib.parse.urlencode(params)


def clean_article(a, label):
    return {
        "label": label,
        "headline": a.get("headline") or "",
        "summary": a.get("summary") or "",
        "source": a.get("source") or "",
        "url": a.get("url") or "",
        "datetime": a.get("datetime")
    }


def parse_date(value):
    if not value:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "NA", "NONE"}:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return dt.datetime.strptime(text[:10], fmt).date()
        except ValueError:
            pass
    return None


def iso_date(value):
    parsed = parse_date(value)
    return parsed.isoformat() if parsed else None


def parse_number(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^0-9.\-]", "", str(value))
    if not text or text in {"-", ".", "-."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def fetch_nasdaq_dividends(symbol, asset_class):
    url = (
        f"https://api.nasdaq.com/api/quote/{urllib.parse.quote(symbol)}/dividends"
        f"?assetclass={urllib.parse.quote(asset_class)}"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/152.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/"
    }
    payload = get_json(url, headers=headers, timeout=15)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not data:
        return [], None, None

    rows = ((data.get("dividends") or {}).get("rows") or [])
    cleaned = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        amount = parse_number(row.get("amount"))
        ex_date = iso_date(row.get("exOrEffDate") or row.get("exDate"))
        pay_date = iso_date(row.get("paymentDate") or row.get("payDate"))
        declaration_date = iso_date(row.get("declarationDate") or row.get("declareDate"))
        record_date = iso_date(row.get("recordDate"))
        if amount is None or not (ex_date or pay_date or declaration_date):
            continue
        cleaned.append({
            "amount": amount,
            "currency": row.get("currency") or "USD",
            "declarationDate": declaration_date,
            "exDate": ex_date,
            "recordDate": record_date,
            "payDate": pay_date,
            "frequency": row.get("frequency"),
            "type": row.get("type") or "Cash"
        })

    published_yield = parse_number(data.get("yield"))
    annualized_dividend = parse_number(data.get("annualizedDividend"))
    return cleaned, published_yield, annualized_dividend


market = []
for item in watch_cfg:
    symbol = item["symbol"]
    name = item.get("name", symbol)
    try:
        q = get_json(api("quote", symbol=symbol))
        market.append({
            "symbol": symbol,
            "name": name,
            "price": q.get("c"),
            "change": q.get("d"),
            "changePct": q.get("dp"),
            "open": q.get("o"),
            "high": q.get("h"),
            "low": q.get("l"),
            "previousClose": q.get("pc"),
            "timestamp": q.get("t")
        })
    except Exception as e:
        market.append({"symbol": symbol, "name": name, "error": str(e)})

try:
    general = get_json(api("news", category="general"))
    news = [clean_article(a, "Markets") for a in general[:12] if a.get("headline")]
except Exception:
    news = []

portfolio_news = []
today = dt.date.today()
from_day = today - dt.timedelta(days=3)
for symbol in news_symbols:
    try:
        arr = get_json(api(
            "company-news",
            symbol=symbol,
            **{"from": from_day.isoformat(), "to": today.isoformat()}
        ))
        portfolio_news.extend(clean_article(a, symbol) for a in arr[:4] if a.get("headline"))
    except Exception:
        pass

price_by_symbol = {
    item["symbol"]: item.get("price")
    for item in market
    if item.get("symbol")
}

dividends = []
trailing_start = today - dt.timedelta(days=365)
for item in dividend_cfg:
    symbol = item["symbol"]
    name = item.get("name", symbol)
    asset_class = item.get("assetClass", "stocks")
    record = {
        "symbol": symbol,
        "name": name,
        "qualityNote": item.get("qualityNote", ""),
        "source": "Nasdaq",
        "dataAvailable": False,
        "status": "Data unavailable",
        "next": None,
        "recent": [],
        "trailing12m": None,
        "trailingYieldPct": None,
        "annualizedDividend": None,
        "error": None
    }
    try:
        cleaned, published_yield, annualized_dividend = fetch_nasdaq_dividends(symbol, asset_class)

        def event_date(d):
            ex = parse_date(d.get("exDate"))
            pay = parse_date(d.get("payDate"))
            if ex and ex >= today:
                return ex
            if pay and pay >= today:
                return pay
            return ex or pay or parse_date(d.get("declarationDate")) or dt.date.min

        upcoming = [
            d for d in cleaned
            if ((parse_date(d.get("exDate")) and parse_date(d.get("exDate")) >= today)
                or (parse_date(d.get("payDate")) and parse_date(d.get("payDate")) >= today))
        ]
        upcoming.sort(key=event_date)

        recent = [
            d for d in cleaned
            if parse_date(d.get("exDate")) and parse_date(d.get("exDate")) <= today
        ]
        recent.sort(key=lambda d: parse_date(d.get("exDate")) or dt.date.min, reverse=True)

        trailing = 0.0
        trailing_count = 0
        for d in cleaned:
            ex = parse_date(d.get("exDate"))
            amount = d.get("amount")
            if ex and trailing_start <= ex <= today and amount is not None:
                trailing += float(amount)
                trailing_count += 1

        price = price_by_symbol.get(symbol)
        trailing_yield = None
        try:
            if trailing_count and price and float(price) > 0:
                trailing_yield = trailing / float(price) * 100
        except (TypeError, ValueError):
            pass
        if trailing_yield is None:
            trailing_yield = published_yield

        record.update({
            "dataAvailable": True,
            "status": "Confirmed" if upcoming else "Awaiting next declaration",
            "next": upcoming[0] if upcoming else None,
            "recent": recent[:6],
            "trailing12m": round(trailing, 6) if trailing_count else None,
            "trailingYieldPct": round(trailing_yield, 4) if trailing_yield is not None else None,
            "annualizedDividend": annualized_dividend
        })
    except Exception as e:
        record["error"] = str(e)
    dividends.append(record)

edition = {
    "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
    "market": market,
    "news": news,
    "portfolioNews": portfolio_news,
    "dividends": dividends,
    "config": config
}

DATA_PATH.write_text(json.dumps(edition, indent=2))
print("Wrote data/edition.json from config.json")
