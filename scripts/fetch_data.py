#!/usr/bin/env python3
import datetime as dt
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN = os.environ.get("FINNHUB_API_KEY", "").strip()
DATA_PATH = Path("data/edition.json")
DATA_PATH.parent.mkdir(exist_ok=True)

if not TOKEN:
    if not DATA_PATH.exists():
        DATA_PATH.write_text(json.dumps({
            "generatedAt": None,
            "market": [],
            "news": [],
            "portfolioNews": []
        }, indent=2))
    print("FINNHUB_API_KEY is not configured yet; publishing the site with placeholder data.")
    raise SystemExit(0)

WATCH = [
    ("SPY", "S&P 500"), ("QQQ", "Nasdaq"), ("DIA", "Dow"),
    ("VOO", "VOO"), ("TEM", "TEM"), ("ITW", "ITW"),
    ("BP", "BP"), ("TTE", "TTE"), ("SONY", "SONY"), ("AIQ", "AIQ")
]
COMPANY_NEWS = ["TEM", "ITW", "BP", "TTE", "SONY", "AIQ"]


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
for symbol, name in WATCH:
    try:
        q = get_json(api("quote", symbol=symbol))
        market.append({
            "symbol": symbol, "name": name,
            "price": q.get("c"), "change": q.get("d"), "changePct": q.get("dp"),
            "open": q.get("o"), "high": q.get("h"), "low": q.get("l"),
            "previousClose": q.get("pc"), "timestamp": q.get("t")
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
for symbol in COMPANY_NEWS:
    try:
        arr = get_json(api("company-news", symbol=symbol, **{"from": from_day.isoformat(), "to": today.isoformat()}))
        portfolio_news.extend(clean_article(a, symbol) for a in arr[:4] if a.get("headline"))
    except Exception:
        pass

edition = {
    "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
    "market": market,
    "news": news,
    "portfolioNews": portfolio_news
}

DATA_PATH.write_text(json.dumps(edition, indent=2))
print("Wrote data/edition.json")
