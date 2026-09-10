#!/usr/bin/env python3
import datetime as dt
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN = os.environ.get("FINNHUB_API_KEY", "").strip()
DATA_PATH = Path("data/edition.json")
DIVIDEND_PATH = Path("data/dividends.json")
CONFIG_PATH = Path("config.json")
DATA_PATH.parent.mkdir(exist_ok=True)

config = json.loads(CONFIG_PATH.read_text())
markets_cfg = config.get("markets", [])
holdings_cfg = config.get("holdings", [])
sector_cfg = config.get("sectorCoverage", {})


def dedupe_items(items):
    seen = set()
    result = []
    for item in items:
        symbol = item.get("symbol")
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        result.append(item)
    return result


sector_items = []
for companies in sector_cfg.values():
    sector_items.extend(companies or [])

watch_cfg = dedupe_items(markets_cfg + holdings_cfg + sector_items)
news_items = dedupe_items(
    [x for x in holdings_cfg if x.get("news")]
    + [x for x in sector_items if x.get("news")]
)
news_symbols = [x["symbol"] for x in news_items]


def dividend_snapshot():
    if not DIVIDEND_PATH.exists():
        return []
    try:
        payload = json.loads(DIVIDEND_PATH.read_text())
        return payload.get("records", []) if isinstance(payload, dict) else []
    except Exception as exc:
        print(f"Could not read dividend snapshot: {exc}")
        return []


if not TOKEN:
    existing = {}
    if DATA_PATH.exists():
        try:
            existing = json.loads(DATA_PATH.read_text())
        except Exception:
            existing = {}
    existing.update({
        "config": config,
        "dividends": dividend_snapshot(),
    })
    existing.setdefault("generatedAt", None)
    existing.setdefault("market", [])
    existing.setdefault("news", [])
    existing.setdefault("portfolioNews", [])
    DATA_PATH.write_text(json.dumps(existing, indent=2))
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
        portfolio_news.extend(clean_article(a, symbol) for a in arr[:3] if a.get("headline"))
    except Exception:
        pass

edition = {
    "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
    "market": market,
    "news": news,
    "portfolioNews": portfolio_news,
    "dividends": dividend_snapshot(),
    "config": config
}

DATA_PATH.write_text(json.dumps(edition, indent=2))
print(
    f"Wrote data/edition.json with {len(watch_cfg)} quotes, "
    f"{len(news_symbols)} company-news feeds and the latest dividend snapshot"
)
