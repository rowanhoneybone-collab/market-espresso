#!/usr/bin/env python3
import datetime as dt
import html as html_lib
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
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


def get_text(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "MarketEspresso/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def get_json(url):
    return json.loads(get_text(url))


def api(path, **params):
    params["token"] = TOKEN
    return "https://finnhub.io/api/v1/" + path + "?" + urllib.parse.urlencode(params)


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def parse_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def treasury_snapshot():
    year = dt.date.today().year
    url = (
        "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
        f"?data=daily_treasury_yield_curve&field_tdr_date_value={year}"
    )
    fields = {
        "1M": "BC_1MONTH",
        "3M": "BC_3MONTH",
        "6M": "BC_6MONTH",
        "1Y": "BC_1YEAR",
        "2Y": "BC_2YEAR",
        "3Y": "BC_3YEAR",
        "5Y": "BC_5YEAR",
        "7Y": "BC_7YEAR",
        "10Y": "BC_10YEAR",
        "20Y": "BC_20YEAR",
        "30Y": "BC_30YEAR",
    }
    try:
        root = ET.fromstring(get_text(url, timeout=30))
        records = []
        for entry in root.iter():
            if local_name(entry.tag) != "entry":
                continue
            values = {}
            for child in entry.iter():
                values[local_name(child.tag)] = (child.text or "").strip()
            date_value = (values.get("NEW_DATE") or "")[:10]
            if not date_value:
                continue
            yields = {label: parse_float(values.get(key)) for label, key in fields.items()}
            records.append({"date": date_value, "yields": yields})

        records.sort(key=lambda x: x["date"])
        if not records:
            raise ValueError("Treasury feed returned no yield records")

        latest = records[-1]
        previous = records[-2] if len(records) > 1 else {"date": None, "yields": {}}
        changes_bps = {}
        for label, value in latest["yields"].items():
            prev = previous.get("yields", {}).get(label)
            changes_bps[label] = round((value - prev) * 100, 1) if value is not None and prev is not None else None

        two = latest["yields"].get("2Y")
        ten = latest["yields"].get("10Y")
        spread = round((ten - two) * 100, 1) if ten is not None and two is not None else None

        return {
            "date": latest["date"],
            "previousDate": previous.get("date"),
            "yields": latest["yields"],
            "changesBps": changes_bps,
            "twoTenSpreadBps": spread,
            "source": "U.S. Department of the Treasury",
            "sourceUrl": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve",
        }
    except Exception as exc:
        return {"error": str(exc), "source": "U.S. Department of the Treasury", "sourceUrl": url}


def strip_html(value):
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def rss_items(xml_text):
    root = ET.fromstring(xml_text)
    items = []
    for node in root.iter():
        if local_name(node.tag) != "item":
            continue
        values = {}
        for child in list(node):
            key = local_name(child.tag)
            values[key] = (child.text or "").strip()
        if not values.get("title"):
            continue
        items.append({
            "headline": strip_html(values.get("title")),
            "summary": strip_html(values.get("description")),
            "url": values.get("link") or "",
            "date": values.get("pubDate") or "",
            "source": "Federal Reserve",
            "label": "Fed",
        })
    return items


def mixed_fraction_to_float(value):
    normalized = (value or "").strip()
    for dash in ("‑", "–", "—", "−"):
        normalized = normalized.replace(dash, "-")
    if "-" in normalized and "/" in normalized:
        whole, frac = normalized.split("-", 1)
        num, den = frac.split("/", 1)
        return float(whole) + float(num) / float(den)
    return float(normalized)


def extract_target_range(page_text):
    plain = strip_html(page_text)
    for dash in ("‑", "–", "—", "−"):
        plain = plain.replace(dash, "-")
    pattern = re.compile(
        r"target range for the federal funds rate at\s+"
        r"([0-9]+(?:-[0-9]+/[0-9]+)?(?:\.[0-9]+)?)\s+to\s+"
        r"([0-9]+(?:-[0-9]+/[0-9]+)?(?:\.[0-9]+)?)\s+percent",
        re.IGNORECASE,
    )
    match = pattern.search(plain)
    if not match:
        return None, None
    return mixed_fraction_to_float(match.group(1)), mixed_fraction_to_float(match.group(2))


def fed_snapshot():
    feed_url = "https://www.federalreserve.gov/feeds/press_monetary.xml"
    try:
        items = rss_items(get_text(feed_url, timeout=30))
        decision = next((item for item in items if "fomc statement" in item["headline"].lower()), None)
        target_low = target_high = None
        if decision and decision.get("url"):
            try:
                target_low, target_high = extract_target_range(get_text(decision["url"], timeout=30))
            except Exception as exc:
                print(f"Could not parse current Fed target range: {exc}")

        return {
            "targetLow": target_low,
            "targetHigh": target_high,
            "lastDecision": decision,
            "headlines": items[:8],
            "source": "Board of Governors of the Federal Reserve System",
            "sourceUrl": feed_url,
        }
    except Exception as exc:
        return {
            "targetLow": None,
            "targetHigh": None,
            "lastDecision": None,
            "headlines": [],
            "error": str(exc),
            "source": "Board of Governors of the Federal Reserve System",
            "sourceUrl": feed_url,
        }


def clean_article(a, label):
    return {
        "label": label,
        "headline": a.get("headline") or "",
        "summary": a.get("summary") or "",
        "source": a.get("source") or "",
        "url": a.get("url") or "",
        "datetime": a.get("datetime")
    }


def rates_market_headlines(news):
    keywords = (
        "federal reserve", "fomc", "treasury yield", "treasury yields", "bond yield",
        "bond yields", "interest rate", "rate cut", "rate hike", "inflation", "cpi",
        "pce", "payroll", "jobs report", "yield curve"
    )
    results = []
    for item in news:
        haystack = f"{item.get('headline', '')} {item.get('summary', '')}".lower()
        if any(keyword in haystack for keyword in keywords):
            results.append(item)
    return results[:8]


treasury = treasury_snapshot()
fed = fed_snapshot()

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
        "rates": {
            "treasury": treasury,
            "fed": fed,
            "headlines": fed.get("headlines", []),
        },
    })
    existing.setdefault("generatedAt", None)
    existing.setdefault("market", [])
    existing.setdefault("news", [])
    existing.setdefault("portfolioNews", [])
    DATA_PATH.write_text(json.dumps(existing, indent=2))
    print("FINNHUB_API_KEY is not configured; publishing official Fed/Treasury data with placeholder market data.")
    raise SystemExit(0)


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
    news = [clean_article(a, "Markets") for a in general[:40] if a.get("headline")]
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

fed_headlines = fed.get("headlines", [])
market_rate_headlines = rates_market_headlines(news)
seen_urls = set()
rates_headlines = []
for item in fed_headlines[:6] + market_rate_headlines:
    key = item.get("url") or item.get("headline")
    if not key or key in seen_urls:
        continue
    seen_urls.add(key)
    rates_headlines.append(item)

edition = {
    "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
    "market": market,
    "news": news,
    "portfolioNews": portfolio_news,
    "dividends": dividend_snapshot(),
    "rates": {
        "treasury": treasury,
        "fed": fed,
        "headlines": rates_headlines[:12],
    },
    "config": config
}

DATA_PATH.write_text(json.dumps(edition, indent=2))
print(
    f"Wrote data/edition.json with {len(watch_cfg)} quotes, "
    f"{len(news_symbols)} company-news feeds, Fed/Treasury rates data, "
    f"{len(rates_headlines[:12])} rates headlines and the latest dividend snapshot"
)
