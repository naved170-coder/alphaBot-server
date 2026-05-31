"""
AlphaBot Cloud Server v3.1
============================
Handles: License validation + Remote ON/OFF + 
         Real-time buyer monitoring + Trade reporting
         + Real-time price feeds (Alpaca crypto + TwelveData forex)

Deploy to Render.com (free):
  1. Create account at render.com
  2. New Web Service
  3. Upload this file
  4. Start command: python license_server.py
  5. Add environment variable: ADMIN_SECRET = your_secret_password
  6. Deploy - get URL like https://alphabot-ns.onrender.com
"""

import json, os, hashlib, time
from datetime import datetime, date
from pathlib import Path

import aiohttp
from aiohttp import web

# ── CONFIG ─────────────────────────────────────────────────────────
DATA_FILE    = Path("licenses_data.json")
TRADES_FILE  = Path("trades_data.json")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "AlphaBot@Admin2024")
VERSION      = "3.1"

# ── PRICE FEED CONFIG (keys from Render environment variables) ──────
ALPACA_KEY     = os.environ.get("ALPACA_KEY", "")
ALPACA_SECRET  = os.environ.get("ALPACA_SECRET", "")
TWELVEDATA_KEY = os.environ.get("TWELVEDATA_KEY", "")

# Price cache so we stay within free API limits.
# All buyers share these cached prices (one fetch serves everyone).
PRICE_CACHE = {
    "crypto": {"data": {}, "ts": 0},
    "forex":  {"data": {}, "ts": 0},
}
CRYPTO_TTL = 2    # seconds - fast updates (Alpaca allows 10k/min)
FOREX_TTL  = 3    # seconds - Alpaca forex rates, fast updates

# Symbol maps
ALPACA_CRYPTO = {
    "BTC/USD": "BTC/USD", "ETH/USD": "ETH/USD", "BNB/USD": "BNB/USD",
    "SOL/USD": "SOL/USD", "XRP/USD": "XRP/USD", "LTC/USD": "LTC/USD",
    "DOGE/USD": "DOGE/USD", "AVAX/USD": "AVAX/USD"
}
TWELVE_FOREX = {
    "EUR/USD": "EUR/USD", "GBP/USD": "GBP/USD", "USD/JPY": "USD/JPY",
    "AUD/USD": "AUD/USD", "USD/CHF": "USD/CHF", "USD/CAD": "USD/CAD",
    "NZD/USD": "NZD/USD", "EUR/GBP": "EUR/GBP", "XAU/USD": "XAU/USD",
    "XAG/USD": "XAG/USD"
}

# ── STORAGE HELPERS ─────────────────────────────────────────────────
def load_licenses():
    if DATA_FILE.exists():
        try: return json.loads(DATA_FILE.read_text())
        except: pass
    return {}

def save_licenses(data):
    DATA_FILE.write_text(json.dumps(data, indent=2))

def load_trades():
    if TRADES_FILE.exists():
        try: return json.loads(TRADES_FILE.read_text())
        except: pass
    return {}

def save_trades(data):
    TRADES_FILE.write_text(json.dumps(data, indent=2))

def days_left(expiry_str):
    try:
        s = (expiry_str or "").strip()
        if not s:
            return -1
        # JavaScript toISOString() ends with 'Z' (UTC). Strip it and any timezone
        # so we compare as naive local datetimes (matches datetime.now()).
        if s.endswith("Z"):
            s = s[:-1]
        # Drop fractional seconds if present (e.g. .000)
        if "." in s:
            s = s.split(".")[0]
        # Drop explicit timezone offset like +05:00 if present
        if "+" in s[11:]:
            s = s[:11] + s[11:].split("+")[0]
        exp = datetime.fromisoformat(s)
        return (exp - datetime.now()).days
    except Exception as e:
        print("days_left parse error:", expiry_str, e)
        return -1

# ── CORS ────────────────────────────────────────────────────────────
@web.middleware
async def cors(request, handler):
    if request.method == "OPTIONS":
        return web.Response(headers={
            "Access-Control-Allow-Origin":  "*",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type,Authorization,X-Admin-Secret",
        })
    r = await handler(request)
    r.headers["Access-Control-Allow-Origin"]  = "*"
    r.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization,X-Admin-Secret"
    return r

def is_admin(request):
    return request.headers.get("X-Admin-Secret","") == ADMIN_SECRET

# ================================================================
# BUYER BOT ENDPOINTS
# ================================================================

async def h_ping(req):
    """Health check - bot calls this to test connection"""
    return web.json_response({
        "status":  "ok",
        "server":  "AlphaBot Cloud v" + VERSION,
        "time":    datetime.now().isoformat(),
        "version": VERSION
    })

async def h_validate(req):
    """
    Called by buyer bot on every startup.
    Checks if license is valid and active.
    """
    try:
        body    = await req.json()
        key     = body.get("key","").strip()
        machine = body.get("machine_id","")
        botVer  = body.get("version","unknown")

        licenses = load_licenses()

        if key not in licenses:
            return web.json_response({
                "status":  "invalid",
                "message": "License key not found. Please contact support.",
                "allowed": False
            })

        lic = licenses[key]

        # Check if suspended by seller
        if not lic.get("isOn", True):
            return web.json_response({
                "status":  "suspended",
                "message": "Your license has been suspended. Please contact support.",
                "allowed": False,
                "botId":   lic.get("botId","")
            })

        # Check expiry
        dl = days_left(lic.get("expiresAt","2000-01-01"))
        if dl < 0:
            return web.json_response({
                "status":  "expired",
                "message": "License expired. Please renew to continue.",
                "allowed": False,
                "botId":   lic.get("botId",""),
                "expiredOn": lic.get("expiresAt","")
            })

        # Valid - update last seen and version
        lic["lastSeen"]    = datetime.now().isoformat()
        lic["lastMachine"] = machine
        lic["botVersion"]  = botVer
        lic["isOnline"]    = True
        save_licenses(licenses)

        return web.json_response({
            "status":     "active",
            "message":    "License valid",
            "allowed":    True,
            "botId":      lic.get("botId",""),
            "plan":       lic.get("plan","standard"),
            "markets":    lic.get("markets", ["psx","fx","crypto","metals","indices","energy"]),
            "daysLeft":   dl,
            "expiresAt":  lic.get("expiresAt",""),
            "serverTime": datetime.now().isoformat()
        })

    except Exception as e:
        return web.json_response({"status":"error","message":str(e),"allowed":False}, status=500)

async def h_heartbeat(req):
    """
    Buyer bot sends this every 5 minutes while running.
    Updates live status visible in seller monitor.
    """
    try:
        body = await req.json()
        key  = body.get("key","").strip()
        if not key:
            return web.json_response({"ok": False})

        licenses = load_licenses()
        if key not in licenses:
            return web.json_response({"ok": False, "message": "Invalid key"})

        lic = licenses[key]
        botId = lic.get("botId","")

        # Update live status
        lic["isOnline"]       = True
        lic["lastSeen"]       = datetime.now().isoformat()
        lic["lastStatus"]     = body.get("status","running")
        lic["botVersion"]     = body.get("version","unknown")
        save_licenses(licenses)

        # Store trade summary
        trades = load_trades()
        today  = date.today().isoformat()

        if botId not in trades:
            trades[botId] = {"daily": {}, "summary": {}, "history": []}

        # Update today summary
        trades[botId]["daily"][today] = {
            "date":          today,
            "botId":         botId,
            "status":        body.get("status","running"),
            "openPositions": body.get("openPositions", 0),
            "todayTrades":   body.get("todayTrades", 0),
            "todayWins":     body.get("todayWins", 0),
            "todayLosses":   body.get("todayLosses", 0),
            "todayPnL":      body.get("todayPnL", 0),
            "todayCharges":  body.get("todayCharges", 0),
            "totalTrades":   body.get("totalTrades", 0),
            "totalPnL":      body.get("totalPnL", 0),
            "winRate":       body.get("winRate", 0),
            "markets":       body.get("markets", []),
            "exchange":      body.get("exchange",""),
            "direction":     body.get("direction","long"),
            "equity":        body.get("equity", 0),
            "version":       body.get("version",""),
            "updatedAt":     datetime.now().isoformat()
        }

        # Keep summary (all time)
        trades[botId]["summary"] = {
            "totalTrades":  body.get("totalTrades", 0),
            "totalPnL":     body.get("totalPnL", 0),
            "winRate":      body.get("winRate", 0),
            "equity":       body.get("equity", 0),
            "lastUpdate":   datetime.now().isoformat()
        }

        # Trade log entries
        newTrades = body.get("newTrades", [])
        if newTrades:
            existing = trades[botId].get("history", [])
            existing.extend(newTrades)
            # Keep last 10000 trades per bot
            trades[botId]["history"] = existing[-10000:]

        save_trades(trades)

        return web.json_response({
            "ok":      True,
            "isOn":    lic.get("isOn", True),
            "message": "Heartbeat received"
        })

    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

async def h_send_trades(req):
    """
    Buyer bot sends completed trade records.
    Stored per bot for seller monitoring.
    """
    try:
        body  = await req.json()
        key   = body.get("key","").strip()
        trades_list = body.get("trades", [])

        licenses = load_licenses()
        if key not in licenses:
            return web.json_response({"ok": False})

        botId  = licenses[key].get("botId","")
        trades = load_trades()

        if botId not in trades:
            trades[botId] = {"daily":{}, "summary":{}, "history":[]}

        existing = trades[botId].get("history",[])
        existing.extend(trades_list)
        trades[botId]["history"] = existing[-10000:]
        save_trades(trades)

        return web.json_response({"ok": True, "received": len(trades_list)})

    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

# ================================================================
# SELLER MONITOR ENDPOINTS (admin only)
# ================================================================

async def h_admin_ping(req):
    """Verify admin access"""
    if not is_admin(req):
        return web.json_response({"ok": False, "message": "Invalid admin secret"}, status=401)
    licenses = load_licenses()
    trades   = load_trades()
    online   = sum(1 for l in licenses.values()
                   if l.get("isOnline") and
                   (datetime.now()-datetime.fromisoformat(l.get("lastSeen","2000-01-01"))).seconds < 600)
    return web.json_response({
        "ok":          True,
        "server":      "AlphaBot Cloud v" + VERSION,
        "totalBots":   len(licenses),
        "onlineBots":  online,
        "time":        datetime.now().isoformat()
    })

async def h_all_bots(req):
    """
    Get all bots with their live status.
    Used by seller monitor dashboard.
    """
    if not is_admin(req):
        return web.json_response({"ok": False}, status=401)

    licenses = load_licenses()
    trades   = load_trades()
    today    = date.today().isoformat()

    bots = []
    for key, lic in licenses.items():
        botId    = lic.get("botId","")
        lastSeen = lic.get("lastSeen","")
        isOnline = False
        if lastSeen:
            try:
                secs = (datetime.now()-datetime.fromisoformat(lastSeen)).seconds
                isOnline = secs < 600  # online if seen in last 10 min
            except: pass

        # Get today trades
        todayData = {}
        if botId in trades and today in trades[botId].get("daily",{}):
            todayData = trades[botId]["daily"][today]

        # Get all-time summary
        summary = {}
        if botId in trades:
            summary = trades[botId].get("summary",{})

        bots.append({
            "key":          key,
            "botId":        botId,
            "buyerName":    lic.get("buyerName",""),
            "buyerPhone":   lic.get("buyerPhone",""),
            "plan":         lic.get("plan",""),
            "isOn":         lic.get("isOn", True),
            "isOnline":     isOnline,
            "lastSeen":     lastSeen,
            "expiresAt":    lic.get("expiresAt",""),
            "daysLeft":     days_left(lic.get("expiresAt","2000-01-01")),
            "markets":      lic.get("markets",[]),
            "botVersion":   lic.get("botVersion",""),
            "lastStatus":   lic.get("lastStatus",""),
            # Today
            "todayTrades":  todayData.get("todayTrades",0),
            "todayWins":    todayData.get("todayWins",0),
            "todayLosses":  todayData.get("todayLosses",0),
            "todayPnL":     todayData.get("todayPnL",0),
            "todayCharges": todayData.get("todayCharges",0),
            "openPositions":todayData.get("openPositions",0),
            "exchange":     todayData.get("exchange",""),
            "direction":    todayData.get("direction",""),
            "equity":       todayData.get("equity",0),
            # All time
            "totalTrades":  summary.get("totalTrades",0),
            "totalPnL":     summary.get("totalPnL",0),
            "winRate":      summary.get("winRate",0),
        })

    # Sort: online first then by last seen
    bots.sort(key=lambda x: (not x["isOnline"], x.get("lastSeen","") or ""), reverse=False)
    return web.json_response({"ok": True, "bots": bots, "total": len(bots)})

async def h_bot_trades(req):
    """Get full trade history for one bot with date filtering"""
    if not is_admin(req):
        return web.json_response({"ok": False}, status=401)

    botId     = req.rel_url.query.get("botId","")
    dateFrom  = req.rel_url.query.get("from","")
    dateTo    = req.rel_url.query.get("to","")
    market    = req.rel_url.query.get("market","")
    limit     = int(req.rel_url.query.get("limit","500"))

    trades = load_trades()
    if botId not in trades:
        return web.json_response({"ok": True, "trades": [], "total": 0})

    history = trades[botId].get("history",[])

    # Filter by date
    if dateFrom:
        history = [t for t in history if (t.get("time","") or t.get("entry_time",""))[:10] >= dateFrom]
    if dateTo:
        history = [t for t in history if (t.get("time","") or t.get("entry_time",""))[:10] <= dateTo]
    if market:
        history = [t for t in history if t.get("market","") == market]

    total = len(history)
    history = history[-limit:]  # most recent first

    return web.json_response({
        "ok":     True,
        "botId":  botId,
        "trades": history,
        "total":  total
    })

async def h_bot_daily(req):
    """Get daily summary for one bot"""
    if not is_admin(req):
        return web.json_response({"ok": False}, status=401)

    botId    = req.rel_url.query.get("botId","")
    dateFrom = req.rel_url.query.get("from","")
    dateTo   = req.rel_url.query.get("to",date.today().isoformat())

    trades = load_trades()
    if botId not in trades:
        return web.json_response({"ok": True, "daily": []})

    daily = trades[botId].get("daily",{})
    result = []
    for d, data in sorted(daily.items()):
        if dateFrom and d < dateFrom: continue
        if dateTo   and d > dateTo:   continue
        result.append(data)

    return web.json_response({"ok": True, "daily": result})

async def h_toggle(req):
    """Turn one bot ON or OFF remotely"""
    if not is_admin(req):
        return web.json_response({"ok": False}, status=401)
    try:
        body = await req.json()
        key  = body.get("key","")
        isOn = body.get("isOn", True)
        licenses = load_licenses()
        if key not in licenses:
            return web.json_response({"ok": False, "message": "Key not found"})
        licenses[key]["isOn"]       = isOn
        licenses[key]["toggledAt"]  = datetime.now().isoformat()
        licenses[key]["toggledBy"]  = "seller"
        save_licenses(licenses)
        botId = licenses[key].get("botId","")
        return web.json_response({
            "ok":    True,
            "botId": botId,
            "isOn":  isOn,
            "message": "Bot "+botId+" turned "+("ON" if isOn else "OFF")
        })
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

async def h_issue_license(req):
    """Issue a new license key"""
    if not is_admin(req):
        return web.json_response({"ok": False}, status=401)
    try:
        body     = await req.json()
        licenses = load_licenses()

        import random, string
        def gen_key():
            parts = ["AB"]
            for _ in range(4):
                parts.append("".join(random.choices(string.ascii_uppercase+string.digits, k=5)))
            return "-".join(parts)

        def gen_bot_id():
            return "BOT-" + "".join(random.choices(string.digits, k=6))

        key   = gen_key()
        botId = gen_bot_id()

        licenses[key] = {
            "botId":      botId,
            "key":        key,
            "buyerName":  body.get("buyerName",""),
            "buyerPhone": body.get("buyerPhone",""),
            "buyerEmail": body.get("buyerEmail",""),
            "plan":       body.get("plan","monthly"),
            "markets":    body.get("markets",["psx","fx","crypto","metals","indices","energy"]),
            "isOn":       True,
            "isOnline":   False,
            "expiresAt":  body.get("expiresAt",""),
            "amount":     body.get("amount",0),
            "currency":   body.get("currency","PKR"),
            "notes":      body.get("notes",""),
            "issuedAt":   datetime.now().isoformat(),
            "lastSeen":   "",
            "botVersion": ""
        }
        save_licenses(licenses)
        return web.json_response({"ok": True, "key": key, "botId": botId})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

async def h_update_license(req):
    """Update existing license (extend, change plan etc)"""
    if not is_admin(req):
        return web.json_response({"ok": False}, status=401)
    try:
        body     = await req.json()
        key      = body.get("key","")
        licenses = load_licenses()
        if key not in licenses:
            return web.json_response({"ok": False, "message": "Key not found"})
        lic = licenses[key]
        for field in ["buyerName","buyerPhone","buyerEmail","plan",
                      "markets","expiresAt","amount","notes","isOn"]:
            if field in body:
                lic[field] = body[field]
        lic["updatedAt"] = datetime.now().isoformat()
        save_licenses(licenses)
        return web.json_response({"ok": True, "key": key})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

async def h_delete_license(req):
    """Delete a license"""
    if not is_admin(req):
        return web.json_response({"ok": False}, status=401)
    try:
        body     = await req.json()
        key      = body.get("key","")
        licenses = load_licenses()
        if key in licenses:
            del licenses[key]
            save_licenses(licenses)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

async def h_stats(req):
    """Overall system statistics for seller dashboard"""
    if not is_admin(req):
        return web.json_response({"ok": False}, status=401)

    licenses = load_licenses()
    trades   = load_trades()
    today    = date.today().isoformat()

    total_bots   = len(licenses)
    active_bots  = sum(1 for l in licenses.values() if l.get("isOn",True))
    online_bots  = 0
    expiring_7   = 0
    total_revenue= 0
    today_trades = 0
    today_pnl    = 0

    for key, lic in licenses.items():
        ls = lic.get("lastSeen","")
        if ls:
            try:
                if (datetime.now()-datetime.fromisoformat(ls)).seconds < 600:
                    online_bots += 1
            except: pass
        dl = days_left(lic.get("expiresAt","2000-01-01"))
        if 0 <= dl <= 7: expiring_7 += 1
        total_revenue += float(lic.get("amount",0) or 0)

        botId = lic.get("botId","")
        if botId in trades and today in trades[botId].get("daily",{}):
            d = trades[botId]["daily"][today]
            today_trades += d.get("todayTrades",0)
            today_pnl    += d.get("todayPnL",0)

    return web.json_response({
        "ok":           True,
        "totalBots":    total_bots,
        "activeBots":   active_bots,
        "onlineBots":   online_bots,
        "expiring7":    expiring_7,
        "totalRevenue": total_revenue,
        "todayTrades":  today_trades,
        "todayPnL":     today_pnl,
        "serverTime":   datetime.now().isoformat()
    })

async def h_export_csv(req):
    """Export all trade data as CSV"""
    if not is_admin(req):
        return web.Response(text="Unauthorized", status=401)

    botId    = req.rel_url.query.get("botId","all")
    dateFrom = req.rel_url.query.get("from","")
    dateTo   = req.rel_url.query.get("to","")

    trades   = load_trades()
    licenses = load_licenses()

    all_trades = []
    bots_to_export = [botId] if botId != "all" else list(trades.keys())

    for bid in bots_to_export:
        if bid not in trades: continue
        # Find buyer name
        buyer = ""
        for k, l in licenses.items():
            if l.get("botId") == bid:
                buyer = l.get("buyerName","")
                break
        history = trades[bid].get("history",[])
        for t in history:
            t_date = (t.get("time","") or t.get("entry_time",""))[:10]
            if dateFrom and t_date < dateFrom: continue
            if dateTo   and t_date > dateTo:   continue
            t["botId"]     = bid
            t["buyerName"] = buyer
            all_trades.append(t)

    if not all_trades:
        return web.Response(text="No trades found", content_type="text/csv")

    hdrs  = ["botId","buyerName","time","market","symbol","side","qty",
             "entry","exit","grossPnl","charges","netPnl","reason","exchange"]
    lines = [",".join(hdrs)]
    for t in all_trades:
        lines.append(",".join(str(t.get(h,"")).replace(",","") for h in hdrs))

    return web.Response(
        body="\n".join(lines).encode(),
        content_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=alphabot_trades.csv"}
    )

# ================================================================
# PRICE FEED ENDPOINTS (real-time prices for all buyer bots)
# ================================================================

async def fetch_alpaca_crypto(symbols):
    """Fetch real-time crypto prices from Alpaca. Keys hidden on server."""
    if not ALPACA_KEY or not ALPACA_SECRET:
        return {}
    syms = ",".join(symbols)
    url = "https://data.alpaca.markets/v1beta3/crypto/us/latest/trades?symbols=" + syms
    headers = {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
        "accept": "application/json"
    }
    out = {}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=headers, timeout=10) as r:
                if r.status == 200:
                    d = await r.json()
                    trades = d.get("trades", {})
                    for sym, t in trades.items():
                        out[sym] = {"price": t.get("p", 0), "ts": t.get("t","")}
    except Exception as e:
        print("Alpaca fetch error:", e)
    return out

async def fetch_alpaca_forex(symbols):
    """Fetch real forex/metal rates from Alpaca currency API. Uses same Alpaca keys."""
    if not ALPACA_KEY or not ALPACA_SECRET:
        return {}
    # Alpaca forex rates: symbols like EURUSD (no slash). Metals XAU/XAG not on Alpaca forex.
    fx = [s for s in symbols if s not in ("XAU/USD", "XAG/USD")]
    pairs = ",".join([s.replace("/", "") for s in fx])
    if not pairs:
        return {}
    url = "https://data.alpaca.markets/v1beta1/forex/latest/rates?currency_pairs=" + pairs
    headers = {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
        "accept": "application/json"
    }
    out = {}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=headers, timeout=10) as r:
                if r.status == 200:
                    d = await r.json()
                    rates = d.get("rates", {})
                    for pair_nodash, info in rates.items():
                        # convert EURUSD -> EUR/USD
                        sym = pair_nodash[:3] + "/" + pair_nodash[3:]
                        mid = info.get("mp") or info.get("bp") or info.get("ap")
                        if mid:
                            out[sym] = {"price": float(mid)}
    except Exception as e:
        print("Alpaca forex fetch error:", e)
    return out

async def fetch_twelve_forex(symbols):
    """Fallback: Fetch real forex/metal prices from Twelve Data. Keys hidden on server."""
    if not TWELVEDATA_KEY:
        return {}
    syms = ",".join(symbols)
    url = "https://api.twelvedata.com/price?symbol=" + syms + "&apikey=" + TWELVEDATA_KEY
    out = {}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=10) as r:
                if r.status == 200:
                    d = await r.json()
                    # Single symbol returns {"price":"x"}, multiple returns {"EUR/USD":{"price":"x"}}
                    if "price" in d and len(symbols) == 1:
                        out[symbols[0]] = {"price": float(d["price"])}
                    else:
                        for sym in symbols:
                            if sym in d and isinstance(d[sym], dict) and "price" in d[sym]:
                                out[sym] = {"price": float(d[sym]["price"])}
    except Exception as e:
        print("TwelveData fetch error:", e)
    return out

async def h_prices_crypto(req):
    """Buyer bots call this for real-time crypto. Server-cached."""
    now = time.time()
    cache = PRICE_CACHE["crypto"]
    if now - cache["ts"] < CRYPTO_TTL and cache["data"]:
        return web.json_response({"ok": True, "prices": cache["data"], "cached": True, "source": "Alpaca"})
    symbols = list(ALPACA_CRYPTO.values())
    data = await fetch_alpaca_crypto(symbols)
    if data:
        cache["data"] = data
        cache["ts"] = now
        return web.json_response({"ok": True, "prices": data, "cached": False, "source": "Alpaca"})
    # return stale cache if fetch failed
    if cache["data"]:
        return web.json_response({"ok": True, "prices": cache["data"], "cached": True, "stale": True, "source": "Alpaca"})
    return web.json_response({"ok": False, "prices": {}, "message": "Alpaca key not set or unreachable"})

async def h_prices_forex(req):
    """Buyer bots call this for real forex/gold. Alpaca first, TwelveData fallback for metals."""
    now = time.time()
    cache = PRICE_CACHE["forex"]
    if now - cache["ts"] < FOREX_TTL and cache["data"]:
        return web.json_response({"ok": True, "prices": cache["data"], "cached": True, "source": "Alpaca"})
    symbols = list(TWELVE_FOREX.values())
    # Alpaca for currency pairs (real-time, fast)
    data = await fetch_alpaca_forex(symbols)
    src = "Alpaca"
    # TwelveData for metals (XAU/XAG) which Alpaca forex does not cover, if key set
    metals = [s for s in symbols if s in ("XAU/USD", "XAG/USD")]
    if metals and TWELVEDATA_KEY:
        td = await fetch_twelve_forex(metals)
        if td:
            data.update(td)
            src = "Alpaca+TwelveData"
    if data:
        cache["data"] = data
        cache["ts"] = now
        return web.json_response({"ok": True, "prices": data, "cached": False, "source": src})
    if cache["data"]:
        return web.json_response({"ok": True, "prices": cache["data"], "cached": True, "stale": True, "source": "Alpaca"})
    return web.json_response({"ok": False, "prices": {}, "message": "Alpaca key not set or unreachable"})

# ── HISTORICAL BARS FOR CHARTS (Alpaca) ─────────────────────────────
# Maps chart range -> (alpaca timeframe, limit, days back)
CHART_RANGES = {
    "24H": ("15Min", 96),
    "7D":  ("1Hour", 168),
    "15D": ("4Hour", 90),
    "30D": ("1Day", 30),
    "90D": ("1Day", 90),
    "6M":  ("1Day", 180),
    "1Y":  ("1Day", 365),
    "5Y":  ("1Week", 260),
}

async def fetch_alpaca_crypto_bars(symbol, rng):
    if not ALPACA_KEY or not ALPACA_SECRET:
        return None
    tf, limit = CHART_RANGES.get(rng, ("1Day", 90))
    import datetime as _dt
    import urllib.parse as _up
    # Compute a start date far enough back for the range
    days_back = {"24H": 2, "7D": 9, "15D": 18, "30D": 35, "90D": 100,
                 "6M": 200, "1Y": 400, "5Y": 2000}.get(rng, 100)
    start = (_dt.datetime.utcnow() - _dt.timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sym_enc = _up.quote(symbol, safe="")  # BTC/USD -> BTC%2FUSD
    url = ("https://data.alpaca.markets/v1beta3/crypto/us/bars?symbols=" + sym_enc +
           "&timeframe=" + tf + "&start=" + start + "&limit=" + str(limit) + "&sort=asc")
    headers = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET, "accept": "application/json"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=headers, timeout=15) as r:
                if r.status == 200:
                    d = await r.json()
                    bars = d.get("bars", {}).get(symbol, [])
                    out = []
                    for b in bars:
                        out.append({"t": b.get("t"), "o": b.get("o"), "h": b.get("h"),
                                    "l": b.get("l"), "c": b.get("c"), "v": b.get("v", 0)})
                    return out
                else:
                    print("Alpaca crypto bars status:", r.status, await r.text())
    except Exception as e:
        print("Alpaca crypto bars error:", e)
    return None

async def fetch_alpaca_forex_bars(symbol, rng):
    if not ALPACA_KEY or not ALPACA_SECRET:
        return None
    tf, limit = CHART_RANGES.get(rng, ("1Day", 90))
    import datetime as _dt
    days_back = {"24H": 2, "7D": 9, "15D": 18, "30D": 35, "90D": 100,
                 "6M": 200, "1Y": 400, "5Y": 2000}.get(rng, 100)
    start = (_dt.datetime.utcnow() - _dt.timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pair = symbol.replace("/", "")
    url = ("https://data.alpaca.markets/v1beta1/forex/rates?currency_pairs=" + pair +
           "&timeframe=" + tf + "&start=" + start + "&limit=" + str(limit) + "&sort=asc")
    headers = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET, "accept": "application/json"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=headers, timeout=15) as r:
                if r.status == 200:
                    d = await r.json()
                    rates = d.get("rates", {}).get(pair, [])
                    out = []
                    for b in rates:
                        mid = b.get("mp") or b.get("c")
                        if mid is None:
                            continue
                        o = b.get("o", mid); h = b.get("h", mid); l = b.get("l", mid)
                        out.append({"t": b.get("t"), "o": o, "h": h, "l": l, "c": mid, "v": 0})
                    return out
                else:
                    print("Alpaca forex bars status:", r.status, await r.text())
    except Exception as e:
        print("Alpaca forex bars error:", e)
    return None

async def h_chart(req):
    """Return real historical bars for a symbol+range. crypto+forex via Alpaca. Others: not available."""
    symbol = req.query.get("symbol", "")
    rng = req.query.get("range", "30D")
    if symbol in ALPACA_CRYPTO:
        bars = await fetch_alpaca_crypto_bars(symbol, rng)
        if bars:
            return web.json_response({"ok": True, "symbol": symbol, "range": rng, "bars": bars, "source": "Alpaca"})
        return web.json_response({"ok": False, "symbol": symbol, "message": "No real chart data available for this range."})
    fx_no_metal = symbol in TWELVE_FOREX and symbol not in ("XAU/USD", "XAG/USD")
    if fx_no_metal:
        bars = await fetch_alpaca_forex_bars(symbol, rng)
        if bars:
            return web.json_response({"ok": True, "symbol": symbol, "range": rng, "bars": bars, "source": "Alpaca"})
        return web.json_response({"ok": False, "symbol": symbol, "message": "No real chart data available for this range."})
    # Metals + PSX + indices: no real source
    return web.json_response({"ok": False, "symbol": symbol, "message": "Real chart data not available for this symbol."})



async def h_prices_status(req):
    """Check which price feeds are configured."""
    return web.json_response({
        "ok": True,
        "alpaca_configured": bool(ALPACA_KEY and ALPACA_SECRET),
        "twelvedata_configured": bool(TWELVEDATA_KEY),
        "forex_via_alpaca": bool(ALPACA_KEY and ALPACA_SECRET),
        "crypto_symbols": list(ALPACA_CRYPTO.keys()),
        "forex_symbols": list(TWELVE_FOREX.keys())
    })

# ── APP SETUP ────────────────────────────────────────────────────────
def create_app():
    app = web.Application(middlewares=[cors])

    # Public (buyer) endpoints
    app.router.add_get ("/ping",            h_ping)
    app.router.add_post("/validate",        h_validate)
    app.router.add_post("/heartbeat",       h_heartbeat)
    app.router.add_post("/trades/send",     h_send_trades)

    # Price feed endpoints (real-time, server-cached, keys hidden)
    app.router.add_get ("/prices/crypto",   h_prices_crypto)
    app.router.add_get ("/prices/forex",    h_prices_forex)
    app.router.add_get ("/prices/status",   h_prices_status)
    app.router.add_get ("/chart",           h_chart)

    # Admin (seller) endpoints
    app.router.add_get ("/admin/ping",      h_admin_ping)
    app.router.add_get ("/admin/bots",      h_all_bots)
    app.router.add_get ("/admin/trades",    h_bot_trades)
    app.router.add_get ("/admin/daily",     h_bot_daily)
    app.router.add_get ("/admin/stats",     h_stats)
    app.router.add_get ("/admin/export",    h_export_csv)
    app.router.add_post("/admin/toggle",    h_toggle)
    app.router.add_post("/admin/issue",     h_issue_license)
    app.router.add_post("/admin/update",    h_update_license)
    app.router.add_post("/admin/delete",    h_delete_license)

    # OPTIONS for all routes
    app.router.add_options("/{path:.*}", lambda r: web.Response())
    return app

import asyncio

async def main():
    port = int(os.environ.get("PORT", 10000))
    app  = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print("AlphaBot Cloud Server v" + VERSION + " running on port " + str(port))
    print("Admin secret: " + ("SET" if ADMIN_SECRET != "AlphaBot@Admin2024" else "DEFAULT - CHANGE THIS"))
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
