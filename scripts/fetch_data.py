#!/usr/bin/env python3
import datetime as dt
import json
import os
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


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "MarketEspresso/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
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
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def clean_dividend(d):
    return {
        "amount": d.get("amount"),
        "currency": d.get("currency") or "",
        "declarationDate": d.get("declarationDate") or d.get("declareDate"),
        "exDate": d.get("exDate"),
        "recordDate": d.get("recordDate"),
        "payDate": d.get("payDate") or d.get("paymentDate"),
        "frequency": d.get("frequency"),
        "adjustmentFactor": d.get("adjustmentFactor") or d.get("adjustFactor")
    }


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
    record = {
        "symbol": symbol,
        "name": name,
        "qualityNote": item.get("qualityNote", ""),
        "dataAvailable": False,
        "status": "Data unavailable",
        "next": None,
        "recent": [],
        "trailing12m": None,
        "trailingYieldPct": None,
        "error": None
    }
    try:
        raw = get_json(api("stock/dividend2", symbol=symbol))
        if isinstance(raw, dict):
            raw = raw.get("data") or raw.get("dividends") or []
        if not isinstance(raw, list):
            raw = []

        cleaned = [clean_dividend(d) for d in raw if isinstance(d, dict)]
        cleaned = [
            d for d in cleaned
            if d.get("amount") is not None
            and (d.get("exDate") or d.get("payDate") or d.get("declarationDate"))
        ]

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
            if ex and trailing_start <= ex <= today:
                try:
                    trailing += float(amount)
                    trailing_count += 1
                except (TypeError, ValueError):
                    pass

        price = price_by_symbol.get(symbol)
        trailing_yield = None
        try:
            if trailing_count and price and float(price) > 0:
                trailing_yield = trailing / float(price) * 100
        except (TypeError, ValueError):
            pass

        record.update({
            "dataAvailable": True,
            "status": "Confirmed" if upcoming else "Awaiting next declaration",
            "next": upcoming[0] if upcoming else None,
            "recent": recent[:6],
            "trailing12m": round(trailing, 6) if trailing_count else None,
            "trailingYieldPct": round(trailing_yield, 4) if trailing_yield is not None else None
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
