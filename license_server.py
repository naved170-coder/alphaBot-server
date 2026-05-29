"""
AlphaBot Cloud Server v3.0
============================
Handles: License validation + Remote ON/OFF + 
         Real-time buyer monitoring + Trade reporting

Deploy to Render.com (free):
  1. Create account at render.com
  2. New Web Service
  3. Upload this file
  4. Start command: python license_server.py
  5. Add environment variable: ADMIN_SECRET = your_secret_password
  6. Deploy - get URL like https://alphabot-ns.onrender.com
"""

import json, os, hashlib
from datetime import datetime, date
from pathlib import Path

try:
    from aiohttp import web
except ImportError:
    os.system("pip install aiohttp -q")
    from aiohttp import web

# ── CONFIG ─────────────────────────────────────────────────────────
DATA_FILE    = Path("licenses_data.json")
TRADES_FILE  = Path("trades_data.json")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "AlphaBot@Admin2024")
VERSION      = "3.0"

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
        exp = datetime.fromisoformat(expiry_str)
        return (exp - datetime.now()).days
    except: return -1

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

# ── APP SETUP ────────────────────────────────────────────────────────
def create_app():
    app = web.Application(middlewares=[cors])

    # Public (buyer) endpoints
    app.router.add_get ("/ping",            h_ping)
    app.router.add_post("/validate",        h_validate)
    app.router.add_post("/heartbeat",       h_heartbeat)
    app.router.add_post("/trades/send",     h_send_trades)

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
