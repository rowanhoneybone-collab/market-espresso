#!/usr/bin/env python3
import concurrent.futures
import datetime as dt
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG_PATH = Path("config.json")
OUTPUT_PATH = Path("data/dividends.json")
OUTPUT_PATH.parent.mkdir(exist_ok=True)

config = json.loads(CONFIG_PATH.read_text())
watchlist = config.get("dividendWatchlist", [])
tracked = {item["symbol"].upper(): item for item in watchlist}
today = dt.date.today()
lookback_days = 45
lookahead_days = 120

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/market-activity/dividends"
}


def get_json(url, headers=None, timeout=20):
    req_headers = {"User-Agent": "MarketEspresso/1.0"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def parse_date(value):
    if not value:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "NA", "NONE", "--"}:
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


def fetch_calendar_day(day):
    url = "https://api.nasdaq.com/api/calendar/dividends?" + urllib.parse.urlencode({"date": day.isoformat()})
    try:
        payload = get_json(url, headers=BROWSER_HEADERS, timeout=20)
        calendar = (((payload or {}).get("data") or {}).get("calendar") or {})
        rows = calendar.get("rows") or []
        matches = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").upper().strip()
            if symbol not in tracked:
                continue
            matches.append({
                "symbol": symbol,
                "name": row.get("companyName") or tracked[symbol].get("name", symbol),
                "amount": parse_number(row.get("dividend_Rate")),
                "currency": "USD",
                "declarationDate": iso_date(row.get("announcement_Date")),
                "exDate": iso_date(row.get("dividend_Ex_Date")) or day.isoformat(),
                "recordDate": iso_date(row.get("record_Date")),
                "payDate": iso_date(row.get("payment_Date")),
                "annualizedDividend": parse_number(row.get("indicated_Annual_Dividend")),
                "source": "Nasdaq Dividend Calendar"
            })
        return matches
    except Exception as exc:
        print(f"Nasdaq dividend calendar {day}: {exc}")
        return []


def fetch_yahoo_history(symbol):
    encoded = urllib.parse.quote(symbol)
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}"
        "?range=2y&interval=1d&events=div%2Csplits"
    )
    headers = {
        "User-Agent": BROWSER_HEADERS["User-Agent"],
        "Accept": "application/json, text/plain, */*"
    }
    payload = get_json(url, headers=headers, timeout=20)
    result = ((((payload or {}).get("chart") or {}).get("result") or [None])[0]) or {}
    meta = result.get("meta") or {}
    price = parse_number(meta.get("regularMarketPrice"))
    dividend_map = ((result.get("events") or {}).get("dividends") or {})
    history = []
    for event in dividend_map.values():
        if not isinstance(event, dict):
            continue
        amount = parse_number(event.get("amount"))
        stamp = event.get("date")
        try:
            event_date = dt.datetime.fromtimestamp(int(stamp), tz=dt.timezone.utc).date() if stamp else None
        except (TypeError, ValueError, OSError):
            event_date = None
        if amount is None or not event_date:
            continue
        history.append({
            "amount": amount,
            "currency": meta.get("currency") or "USD",
            "exDate": event_date.isoformat(),
            "payDate": None,
            "recordDate": None,
            "declarationDate": None,
            "source": "Yahoo Finance historical distributions"
        })
    history.sort(key=lambda event: event.get("exDate") or "", reverse=True)
    return history, price


calendar_dates = [
    today + dt.timedelta(days=offset)
    for offset in range(-lookback_days, lookahead_days + 1)
]
calendar_events = []
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    for matches in pool.map(fetch_calendar_day, calendar_dates):
        calendar_events.extend(matches)

calendar_by_symbol = {symbol: [] for symbol in tracked}
for event in calendar_events:
    calendar_by_symbol[event["symbol"]].append(event)
for events in calendar_by_symbol.values():
    events.sort(key=lambda event: event.get("exDate") or "9999-12-31")

records = []
trailing_start = today - dt.timedelta(days=365)
for symbol, cfg in tracked.items():
    history = []
    price = None
    history_error = None
    try:
        history, price = fetch_yahoo_history(symbol)
    except Exception as exc:
        history_error = str(exc)
        print(f"Yahoo dividend history {symbol}: {exc}")

    confirmed_events = calendar_by_symbol.get(symbol, [])
    future_ex = [
        event for event in confirmed_events
        if parse_date(event.get("exDate")) and parse_date(event.get("exDate")) >= today
    ]
    pending_payments = [
        event for event in confirmed_events
        if parse_date(event.get("payDate")) and parse_date(event.get("payDate")) >= today
        and parse_date(event.get("exDate")) and parse_date(event.get("exDate")) < today
    ]

    next_event = future_ex[0] if future_ex else (pending_payments[-1] if pending_payments else None)
    if future_ex:
        status = "Confirmed"
    elif pending_payments:
        status = "Payment scheduled"
    else:
        status = "Awaiting next declaration"

    trailing_total = 0.0
    trailing_count = 0
    for event in history:
        ex_date = parse_date(event.get("exDate"))
        amount = event.get("amount")
        if ex_date and trailing_start <= ex_date <= today and amount is not None:
            trailing_total += float(amount)
            trailing_count += 1

    trailing_yield = None
    if trailing_count and price and price > 0:
        trailing_yield = trailing_total / price * 100

    recent = history[:6]
    # Prefer richer calendar details when they overlap a recent historical event.
    for idx, event in enumerate(recent):
        match = next((c for c in confirmed_events if c.get("exDate") == event.get("exDate")), None)
        if match:
            recent[idx] = {**event, **{k: v for k, v in match.items() if v is not None}}

    records.append({
        "symbol": symbol,
        "name": cfg.get("name", symbol),
        "qualityNote": cfg.get("qualityNote", ""),
        "source": "Nasdaq Dividend Calendar + Yahoo Finance history",
        "dataAvailable": bool(history or confirmed_events),
        "status": status,
        "next": next_event,
        "upcoming": future_ex[:4],
        "pendingPayments": pending_payments[:2],
        "recent": recent,
        "trailing12m": round(trailing_total, 6) if trailing_count else None,
        "trailingYieldPct": round(trailing_yield, 4) if trailing_yield is not None else None,
        "annualizedDividend": (next_event or {}).get("annualizedDividend") if next_event else None,
        "historyPrice": price,
        "historyError": history_error
    })

output = {
    "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
    "lookbackDays": lookback_days,
    "lookaheadDays": lookahead_days,
    "records": records
}
OUTPUT_PATH.write_text(json.dumps(output, indent=2))
print(f"Wrote {OUTPUT_PATH} with {len(records)} tracked dividend names and {len(calendar_events)} matching calendar events")
