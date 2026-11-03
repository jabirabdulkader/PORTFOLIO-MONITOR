#!/usr/bin/env python3
"""Fetch market data for the watchlist and write market.json.

Usage:
  python fetch_data.py --fast   (prices and scores only, used during market hours)
  python fetch_data.py --eod    (full refresh: fundamentals, analysts, news, actions, delivery)

Sources (all free, none official APIs, so any of them can change or block us):
  - Yahoo Finance via yfinance: stock prices, history, fundamentals, analyst votes
  - AMFI NAVAll.txt and mfapi.in: mutual fund NAV and history
  - Google News RSS: headlines
  - NSE website: corporate actions and delivery data (best effort)
If a source fails, the previous value is kept so the app never goes blank.
"""
import csv
import datetime as dt
import email.utils
import io
import json
import os
import sys
import urllib.parse
import xml.etree.ElementTree as ET

import requests
import yfinance as yf

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = ROOT
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
FAST = "--fast" in sys.argv
NOW = dt.datetime.now(dt.timezone.utc)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- scores
def scale(v, lo, hi):
    return None if v is None else clamp((v - lo) / (hi - lo) * 100)


def fundamental_score(info):
    """0-100 from profitability, growth, debt and valuation. Missing inputs are skipped."""
    pe = info.get("trailingPE")
    de = info.get("debtToEquity")
    parts = [
        scale(info.get("returnOnEquity"), 0, 0.25),
        scale(info.get("profitMargins"), 0, 0.25),
        scale(info.get("earningsGrowth"), -0.1, 0.3),
        scale(info.get("revenueGrowth"), -0.05, 0.2),
        None if de is None else 100 - scale(de, 0, 200),
        None if not pe or pe <= 0 else 100 - scale(pe, 10, 60),
    ]
    parts = [p for p in parts if p is not None]
    return round(sum(parts) / len(parts)) if len(parts) >= 2 else None


def momentum_score(c):
    """0-100 from trend vs 50/200-day averages, 6-month return and RSI."""
    n = len(c)
    if n < 30:
        return None
    last = c[-1]
    ma50 = sum(c[-50:]) / min(50, n)
    ma200 = sum(c[-200:]) / min(200, n)
    r6 = last / c[max(0, n - 126)] - 1
    d = [c[i] - c[i - 1] for i in range(n - 14, n)]
    g = sum(x for x in d if x > 0) / 14
    l = -sum(x for x in d if x < 0) / 14
    rsi = 100 if l == 0 else 100 - 100 / (1 + g / l)
    s = (25 if last > ma50 else 0) + (25 if last > ma200 else 0)
    s += 25 * clamp((r6 + 0.1) / 0.4, 0, 1)
    s += 25 * (1 - abs(rsi - 60) / 60)
    return round(clamp(s))


POS = ("surge", "jump", "gain", "rise", "rally", "record", "beat", "upgrade", "buy", "profit", "growth", "win", "strong", "dividend", "bonus")
NEG = ("fall", "drop", "slump", "plunge", "loss", "miss", "downgrade", "sell", "probe", "fraud", "penalty", "weak", "cut", "default", "raid", "fine")


def sentiment(titles):
    """Rough keyword score, 0-100 (50 = neutral). It is a hint, not analysis."""
    if not titles:
        return None
    p = sum(any(w in t.lower() for w in POS) for t in titles)
    n = sum(any(w in t.lower() for w in NEG) for t in titles)
    return round(clamp(50 + 50 * (p - n) / max(len(titles), 4)))


# ---------------------------------------------------------------- news
def news(query):
    url = "https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en" % urllib.parse.quote(query)
    root = ET.fromstring(requests.get(url, headers=UA, timeout=25).content)
    out = []
    for it in list(root.iter("item"))[:6]:
        try:
            when = email.utils.parsedate_to_datetime(it.findtext("pubDate")).astimezone(dt.timezone.utc).isoformat()
        except Exception:
            when = None
        out.append([it.findtext("title") or "", it.findtext("source") or "News", when, it.findtext("link") or ""])
    return out


# ---------------------------------------------------------------- NSE
class NSE:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(UA)
        self.s.headers.update({"Accept": "application/json,text/plain,*/*", "Referer": "https://www.nseindia.com/"})
        self.ok = False
        try:
            self.s.get("https://www.nseindia.com", timeout=25)
            self.ok = True
        except Exception as e:
            log("NSE not reachable:", e)

    def actions(self, sym):
        out = []
        today = dt.date.today()
        if not self.ok:
            return out
        try:
            rows = self.s.get("https://www.nseindia.com/api/corporates-corporateActions",
                              params={"index": "equities", "symbol": sym}, timeout=25).json()
            for r in rows:
                d = dt.datetime.strptime(r.get("exDate") or r.get("recDate") or "", "%d-%b-%Y").date()
                if d >= today - dt.timedelta(days=2):
                    out.append([(r.get("subject") or "Corporate action").strip()[:70], d.isoformat()])
        except Exception as e:
            log("  NSE actions failed for", sym, e)
        try:
            rows = self.s.get("https://www.nseindia.com/api/event-calendar",
                              params={"index": "equities", "symbol": sym}, timeout=25).json()
            for r in rows:
                d = dt.datetime.strptime(r.get("date") or "", "%d-%b-%Y").date()
                if d >= today - dt.timedelta(days=2):
                    out.append([(r.get("purpose") or "Event").strip()[:70], d.isoformat()])
        except Exception as e:
            log("  NSE events failed for", sym, e)
        return sorted(out, key=lambda x: x[1])[:6]

    def delivery(self, symbols, store):
        """Download recent bhavcopy files (after close) and keep delivery % history."""
        if not self.ok:
            return
        days = store.setdefault("days", {})
        for back in range(0, 5):
            day = dt.date.today() - dt.timedelta(days=back)
            if day.weekday() > 4 or day.isoformat() in days:
                continue
            url = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_%s.csv" % day.strftime("%d%m%Y")
            try:
                r = self.s.get(url, timeout=60)
                if r.status_code != 200:
                    continue
                m = {}
                for row in csv.DictReader(io.StringIO(r.text)):
                    row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
                    if row.get("SERIES") == "EQ" and row.get("SYMBOL") in symbols:
                        try:
                            m[row["SYMBOL"]] = float(row["DELIV_PER"])
                        except ValueError:
                            pass
                days[day.isoformat()] = m
            except Exception as e:
                log("  bhavcopy failed for", day, e)
        for k in sorted(days)[:-25]:
            del days[k]


def yf_actions(t):
    """Fallback corporate events from Yahoo's calendar."""
    out = []
    try:
        cal = t.calendar or {}
        for key, label in (("Ex-Dividend Date", "Dividend ex-date"), ("Earnings Date", "Results")):
            v = cal.get(key)
            v = v if isinstance(v, (list, tuple)) else [v]
            for d in v:
                if d and d >= dt.date.today():
                    out.append([label, d.isoformat()])
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- stocks
def closes(df):
    return [round(float(x), 2) for x in df["Close"].dropna().tolist()]


def cap_bucket(mcap):
    """Approximate SEBI buckets: 0 large, 1 mid, 2 small (by market cap in rupees)."""
    if not mcap:
        return None
    return 0 if mcap >= 1.0e12 else 1 if mcap >= 3.3e11 else 2


def stock(sym, old, nse, deliv):
    t = yf.Ticker(sym + ".NS")
    d = closes(t.history(period="1y", interval="1d", auto_adjust=False))
    if len(d) < 30:
        raise ValueError("not enough price history")
    new = dict(old or {})
    new.update({"sym": sym, "price": d[-1], "prev": d[-2], "d": d[-260:], "ms": momentum_score(d)})
    if not FAST or not old or "fs" not in old:
        w = closes(t.history(period="5y", interval="1wk", auto_adjust=False))
        info = {}
        try:
            info = t.info or {}
        except Exception as e:
            log("  info failed", e)
        an = None
        try:
            r = t.recommendations.iloc[0]
            an = [int(r["strongBuy"] + r["buy"]), int(r["hold"]), int(r["sell"] + r["strongSell"])]
            if sum(an) == 0:
                an = None
        except Exception:
            pass
        try:
            items = news(sym + " share NSE")
        except Exception as e:
            log("  news failed", e)
            items = new.get("news", [])
        acts = nse.actions(sym) or yf_actions(t)
        new.update({
            "w": w[-270:],
            "name": info.get("longName") or info.get("shortName") or sym,
            "sub": "%s · NSE%s" % (sym, " · " + info["sector"] if info.get("sector") else ""),
            "sector": info.get("sector"),
            "cap": cap_bucket(info.get("marketCap")),
            "pe": info.get("trailingPE"),
            "dy": info.get("dividendYield"),
            "hi": info.get("fiftyTwoWeekHigh"),
            "lo": info.get("fiftyTwoWeekLow"),
            "fs": fundamental_score(info),
            "an": an,
            "tg": info.get("targetMeanPrice"),
            "news": items,
            "ns": sentiment([x[0] for x in items]),
            "act": acts,
        })
    return new


# ---------------------------------------------------------------- funds
def amfi_navs():
    r = requests.get("https://www.amfiindia.com/spages/NAVAll.txt", headers=UA, timeout=90)
    out = {}
    for line in r.text.splitlines():
        p = line.split(";")
        if len(p) >= 6 and p[0].strip().isdigit():
            try:
                out[p[0].strip()] = {"name": p[3].strip(), "nav": float(p[4]), "date": p[5].strip()}
            except ValueError:
                pass
    return out


def nav_on(series, day):
    """Latest NAV on or before `day` from a list of (date, nav) sorted newest first."""
    for d, v in series:
        if d <= day:
            return v
    return None


def fund(code, old, amfi, meta):
    r = requests.get("https://api.mfapi.in/mf/" + code, headers=UA, timeout=60).json()
    series = []
    for row in r.get("data", []):
        try:
            series.append((dt.datetime.strptime(row["date"], "%d-%m-%Y").date(), float(row["nav"])))
        except Exception:
            pass
    series.sort(reverse=True)
    if len(series) < 5:
        raise ValueError("no NAV history")
    today = series[0][0]
    nav, prev = series[0][1], series[1][1]
    if code in amfi and amfi[code]["date"]:
        nav = amfi[code]["nav"]
    ret = []
    for yrs in (1, 3, 5):
        then = nav_on(series, today - dt.timedelta(days=365 * yrs))
        ret.append(round(((nav / then) ** (1 / yrs) - 1) * 100, 1) if then and series[-1][0] <= today - dt.timedelta(days=365 * yrs) else None)
    chron = [v for _, v in reversed(series)]
    daily = chron[-260:]
    weekly = chron[::-1][::5][::-1][-270:]
    base = ret[1] if ret[1] is not None else ret[0]
    m = r.get("meta", {})
    new = dict(old or {})
    new.update({
        "code": code,
        "name": m.get("scheme_name") or (amfi.get(code) or {}).get("name") or code,
        "sub": "%s · %s" % (m.get("fund_house", ""), m.get("scheme_category", "")),
        "cat": m.get("scheme_category"),
        "price": nav, "prev": prev, "navdate": today.isoformat(),
        "d": daily, "w": weekly, "ret": ret,
        "fs": None if base is None else round(clamp(base * 4)),
        "ms": momentum_score(daily),
        "meta": meta.get(code, {}),
    })
    if not FAST or "news" not in new:
        try:
            items = news(new["name"].split(" - ")[0] + " mutual fund")
            new["news"] = items
            new["ns"] = sentiment([x[0] for x in items])
        except Exception as e:
            log("  fund news failed", e)
    return new


# ---------------------------------------------------------------- main
def main():
    wl = read_json(os.path.join(ROOT, "watchlist.json"), {"stocks": [], "funds": []})
    meta = read_json(os.path.join(ROOT, "funds_meta.json"), {})
    old = read_json(os.path.join(DATA, "market.json"), {})
    out = {"updated": NOW.isoformat(), "bench": old.get("bench", {}), "stocks": {}, "funds": {}}
    os.makedirs(DATA, exist_ok=True)

    try:
        b = yf.Ticker("^NSEI")
        out["bench"] = {"name": "Nifty 50",
                        "d": closes(b.history(period="1y", interval="1d"))[-260:],
                        "w": closes(b.history(period="5y", interval="1wk"))[-270:]}
    except Exception as e:
        log("benchmark failed", e)

    nse = NSE() if not FAST else type("Off", (), {"ok": False, "actions": lambda s, x: [], "delivery": lambda s, a, b: None})()
    dstore = read_json(os.path.join(DATA, "delivery.json"), {})
    if not FAST:
        nse.delivery(set(wl["stocks"]), dstore)
        with open(os.path.join(DATA, "delivery.json"), "w", encoding="utf-8") as f:
            json.dump(dstore, f)

    for sym in wl["stocks"]:
        log("stock", sym)
        prev = (old.get("stocks") or {}).get(sym)
        try:
            out["stocks"][sym] = stock(sym, prev, nse, dstore)
        except Exception as e:
            log("  FAILED", sym, e)
            if prev:
                prev["stale"] = True
                out["stocks"][sym] = prev
        s = out["stocks"].get(sym)
        if s and dstore.get("days"):
            days = sorted(dstore["days"])
            vals = [dstore["days"][d].get(sym) for d in days if dstore["days"][d].get(sym) is not None]
            if vals:
                s["dl"] = vals[-1]
                s["da"] = round(sum(vals[-21:-1]) / len(vals[-21:-1]), 1) if len(vals) > 1 else None

    try:
        amfi = amfi_navs()
    except Exception as e:
        log("AMFI failed", e)
        amfi = {}
    for code in wl["funds"]:
        log("fund", code)
        prev = (old.get("funds") or {}).get(code)
        try:
            out["funds"][code] = fund(code, prev, amfi, meta)
        except Exception as e:
            log("  FAILED", code, e)
            if prev:
                prev["stale"] = True
                out["funds"][code] = prev

    with open(os.path.join(DATA, "market.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), ensure_ascii=False)
    log("wrote market.json:", len(out["stocks"]), "stocks,", len(out["funds"]), "funds")


if __name__ == "__main__":
    main()
