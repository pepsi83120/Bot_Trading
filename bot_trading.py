import telebot
import requests
import json
import os
import schedule
import time
import threading
from datetime import datetime

# ============================================================
#  CONFIGURATION
# ============================================================
BOT_TOKEN   = os.environ.get("BOT_TOKEN")
ADMIN_ID    = int(os.environ.get("ADMIN_ID", "0"))
REPORT_HOUR = os.environ.get("REPORT_HOUR", "08:00")

if not BOT_TOKEN:
    raise ValueError("❌ Variable BOT_TOKEN manquante !")

bot = telebot.TeleBot(BOT_TOKEN)

USERS_FILE  = "users.json"
ALERTS_FILE = "alerts.json"

# ── Cryptos (CoinGecko) ────────────────────────────────────
CRYPTO_ASSETS = {
    "bitcoin":     "₿ Bitcoin",
    "ethereum":    "Ξ Ethereum",
    "solana":      "◎ Solana",
    "ripple":      "✦ XRP",
    "binancecoin": "◆ BNB",
    "dogecoin":    "🐶 Dogecoin",
    "cardano":     "🔵 Cardano",
    "avalanche-2": "🔺 Avalanche",
    "chainlink":   "🔗 Chainlink",
    "polkadot":    "⚪ Polkadot",
}

CRYPTO_MAP = {
    "BTC":  "bitcoin",
    "ETH":  "ethereum",
    "SOL":  "solana",
    "XRP":  "ripple",
    "BNB":  "binancecoin",
    "DOGE": "dogecoin",
    "ADA":  "cardano",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "DOT":  "polkadot",
}

# ── Actions & Indices ─────────────────────────────────────
YAHOO_ASSETS = {
    "AAPL":   "🍎 Apple",
    "TSLA":   "🚗 Tesla",
    "NVDA":   "🖥️ NVIDIA",
    "MSFT":   "🪟 Microsoft",
    "GOOGL":  "🔍 Alphabet",
    "^GSPC":  "🇺🇸 S&P 500",
    "^IXIC":  "💻 Nasdaq",
    "^GDAXI": "🇩🇪 DAX",
}

# Alias → ticker réel
ALIAS = {
    "SP500":     "^GSPC",
    "SPX":       "^GSPC",
    "NASDAQ":    "^IXIC",
    "DAX":       "^GDAXI",
    "APPLE":     "AAPL",
    "TESLA":     "TSLA",
    "NVIDIA":    "NVDA",
    "MICROSOFT": "MSFT",
    "GOOGLE":    "GOOGL",
}

def resolve(symbol):
    s = ALIAS.get(symbol, symbol)
    return CRYPTO_MAP.get(s, s)


# ════════════════════════════════════════════════════════════
#  GESTION UTILISATEURS
# ════════════════════════════════════════════════════════════

def load_users():
    if not os.path.exists(USERS_FILE):
        save_users([])
    with open(USERS_FILE, "r") as f:
        return json.load(f).get("allowed", [])

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump({"allowed": users}, f, indent=2)

def is_admin(uid):   return uid == ADMIN_ID
def is_authorized(uid): return is_admin(uid) or uid in load_users()


# ════════════════════════════════════════════════════════════
#  GESTION ALERTES
# ════════════════════════════════════════════════════════════

def load_alerts():
    if not os.path.exists(ALERTS_FILE):
        save_alerts({})
    with open(ALERTS_FILE, "r") as f:
        return json.load(f)

def save_alerts(alerts):
    with open(ALERTS_FILE, "w") as f:
        json.dump(alerts, f, indent=2)

def add_alert(user_id, symbol, target, direction):
    alerts = load_alerts()
    key = str(user_id)
    if key not in alerts:
        alerts[key] = []
    alerts[key].append({"symbol": symbol, "target": float(target), "direction": direction, "active": True})
    save_alerts(alerts)

def get_user_alerts(user_id):
    return load_alerts().get(str(user_id), [])

def clear_user_alerts(user_id):
    alerts = load_alerts()
    alerts[str(user_id)] = []
    save_alerts(alerts)


# ════════════════════════════════════════════════════════════
#  DONNÉES DE MARCHÉ
# ════════════════════════════════════════════════════════════

# Cache pour éviter trop de requêtes CoinGecko
_cache = {}
_cache_time = {}
CACHE_DURATION = 120  # 2 minutes

def get_from_cache(key):
    if key in _cache and time.time() - _cache_time.get(key, 0) < CACHE_DURATION:
        return _cache[key]
    return None

def set_cache(key, value):
    _cache[key] = value
    _cache_time[key] = time.time()

def get_crypto_prices():
    cached = get_from_cache("crypto_prices")
    if cached:
        return cached
    ids = ",".join(CRYPTO_ASSETS.keys())
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={
                "vs_currency": "usd",
                "ids": ids,
                "order": "market_cap_desc",
                "price_change_percentage": "1h,24h,7d"
            },
            timeout=20
        )
        r.raise_for_status()
        data = {c["id"]: c for c in r.json()}
        set_cache("crypto_prices", data)
        return data
    except Exception as e:
        print(f"Erreur CoinGecko : {e}")
        return {}

def get_crypto_history(coin_id, days=60):
    """Désactivé pour éviter le rate limit CoinGecko — on utilise les données de base"""
    return []

def get_stock_price(ticker):
    """Récupère les données via stooq.com avec plage de dates explicite"""
    stooq_map = {
        "^GSPC":  "^spx",
        "^IXIC":  "^ndx",
        "^GDAXI": "^dax",
        "AAPL":   "aapl.us",
        "TSLA":   "tsla.us",
        "NVDA":   "nvda.us",
        "MSFT":   "msft.us",
        "GOOGL":  "googl.us",
    }
    stooq_ticker = stooq_map.get(ticker, ticker.lower() + ".us")

    # Calcul des dates : 90 jours en arrière
    from datetime import timedelta
    today = datetime.now()
    d_from = (today - timedelta(days=90)).strftime("%Y%m%d")
    d_to   = today.strftime("%Y%m%d")

    try:
        r = requests.get(
            "https://stooq.com/q/d/l/",
            params={"s": stooq_ticker, "d1": d_from, "d2": d_to, "i": "d"},
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=15
        )
        r.raise_for_status()
        lines = r.text.strip().split("\n")
        print(f"Stooq {ticker} → {stooq_ticker} : {len(lines)} lignes")
        data_lines = [l for l in lines[1:] if l.strip() and "No data" not in l]
        if len(data_lines) < 2:
            return None

        def parse_line(line):
            parts = line.split(",")
            if len(parts) < 5:
                return None
            try:
                return {
                    "close": float(parts[4]),
                    "high":  float(parts[2]),
                    "low":   float(parts[3]),
                }
            except:
                return None

        parsed = [p for p in (parse_line(l) for l in data_lines) if p]
        if len(parsed) < 2:
            return None

        closes = [p["close"] for p in parsed]
        highs  = [p["high"]  for p in parsed]
        lows   = [p["low"]   for p in parsed]

        p_today = closes[-1]
        p_prev  = closes[-2]
        p_5d    = closes[max(0, len(closes)-6)]

        if p_today == 0:
            return None

        return {
            "price":     p_today,
            "change_1d": ((p_today - p_prev) / p_prev) * 100,
            "change_5d": ((p_today - p_5d)   / p_5d)   * 100,
            "high":      highs[-1],
            "low":       lows[-1],
            "closes":    closes,
            "highs":     highs,
            "lows":      lows,
        }
    except Exception as e:
        print(f"Erreur Stooq ({ticker}) : {e}")
        return None


# ════════════════════════════════════════════════════════════
#  INDICATEURS TECHNIQUES RÉELS
# ════════════════════════════════════════════════════════════

def calc_rsi(closes, period=14):
    """RSI réel sur les N derniers prix de clôture"""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[-i] - closes[-i-1]
        (gains if diff > 0 else losses).append(abs(diff))
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0.0001
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_ma(closes, period):
    """Moyenne mobile simple"""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period

def calc_support_resistance(highs, lows, closes, price):
    """
    Support = plus bas récent sur 20 jours
    Résistance = plus haut récent sur 20 jours
    Ajustés par rapport au prix actuel
    """
    window = min(20, len(highs))
    recent_highs = highs[-window:]
    recent_lows  = lows[-window:]

    resistance = max(recent_highs)
    support    = min(recent_lows)

    # Si le prix est déjà au-dessus de la résistance, on prend le 2e niveau
    if price >= resistance * 0.99:
        resistance = resistance * 1.03

    # Si le prix est en dessous du support, on ajuste
    if price <= support * 1.01:
        support = support * 0.97

    return round(support, 4), round(resistance, 4)

def calc_fibonacci(high, low):
    """Niveaux de Fibonacci 38.2%, 50%, 61.8%"""
    diff = high - low
    return {
        "0.382": round(high - diff * 0.382, 4),
        "0.500": round(high - diff * 0.500, 4),
        "0.618": round(high - diff * 0.618, 4),
    }

def analyse_technique(closes, highs, lows, price):
    """
    Analyse complète inspirée Trading Elite :
    - Score de confluence /10
    - RSI, MA20, MA50
    - Support/Résistance réels
    - Niveaux de Fibonacci
    - Gestion de position professionnelle (breakeven, partial TP)
    """
    rsi  = calc_rsi(closes)
    ma20 = calc_ma(closes, 20)
    ma50 = calc_ma(closes, 50)
    support, resistance = calc_support_resistance(highs, lows, closes, price)
    fib = calc_fibonacci(
        max(highs[-20:]) if len(highs) >= 20 else max(highs),
        min(lows[-20:])  if len(lows)  >= 20 else min(lows)
    )

    # ── Score de confluence sur 10 (inspiré Module 1 formation) ──
    confluence = 0
    confluences = []

    # 1. Tendance Daily (MA50) — +2 points
    if ma50:
        if price > ma50:
            confluence += 2
            confluences.append("✦ Tendance haussière Daily (prix > MA50) +2")
        else:
            confluences.append("✦ Tendance baissière Daily (prix < MA50) +0")

    # 2. Zone S/R majeure — +2 points
    dist_support    = ((price - support) / price) * 100
    dist_resistance = ((resistance - price) / price) * 100
    if dist_support < 3:
        confluence += 2
        confluences.append("✦ Prix sur zone de support majeure +2")
    elif dist_resistance < 3:
        confluences.append("✦ Prix sur zone de résistance — prudence +0")
    else:
        confluence += 1
        confluences.append("✦ Prix entre support et résistance +1")

    # 3. RSI — +1 point
    if rsi is not None:
        if rsi < 35:
            confluence += 1
            confluences.append(f"✦ RSI {rsi:.0f} — survente (achat) +1")
        elif rsi > 65:
            confluences.append(f"✦ RSI {rsi:.0f} — surachat (vente) +0")
        else:
            confluences.append(f"✦ RSI {rsi:.0f} — zone neutre +0")

    # 4. Structure MA20 confirme — +1 point
    if ma20:
        if price > ma20 and (ma50 and ma20 > ma50):
            confluence += 1
            confluences.append("✦ MA20 > MA50 — structure haussière confirmée +1")
        elif price < ma20 and (ma50 and ma20 < ma50):
            confluences.append("✦ MA20 < MA50 — structure baissière +0")
        else:
            confluences.append("✦ MAs mixtes — pas de tendance claire +0")

    # 5. Fibonacci — +1 point
    fib50 = fib.get("0.500", 0)
    fib618 = fib.get("0.618", 0)
    if fib618 and fib50:
        if fib618 <= price <= fib50:
            confluence += 1
            confluences.append("✦ Prix dans zone Fibonacci 50%-61.8% +1")
        else:
            confluences.append("✦ Prix hors zone Fibonacci +0")

    # 6. Momentum — +1 point
    if len(closes) >= 5:
        momentum = ((closes[-1] - closes[-5]) / closes[-5]) * 100
        if 0 < momentum < 5:
            confluence += 1
            confluences.append(f"✦ Momentum haussier modéré ({momentum:.1f}%) +1")
        elif momentum > 5:
            confluences.append(f"✦ Momentum fort ({momentum:.1f}%) — attention surachat +0")
        elif momentum < -5:
            confluences.append(f"✦ Momentum baissier fort ({momentum:.1f}%) +0")

    # 7. Absence de surachat/survente extrême — +1 point
    if rsi and 30 < rsi < 70:
        confluence += 1
        confluences.append("✦ RSI pas en zone extrême — entrée possible +1")

    # 8. Ratio risque contrôlable — +1 point (calculé après)
    # On l'ajoutera si R:R > 1.5

    # ── Signal basé sur le score de confluence ──
    # Biais directionnel
    bullish = (ma50 and price > ma50) and (rsi and rsi < 60)
    bearish = (ma50 and price < ma50) and (rsi and rsi > 40)

    if confluence >= 7:
        sig = "STRONG_BUY" if bullish else ("STRONG_SELL" if bearish else "BUY")
    elif confluence >= 5:
        sig = "BUY" if bullish else ("SELL" if bearish else "NEUTRE")
    elif confluence <= 3:
        sig = "STRONG_SELL" if bearish else "SELL"
    else:
        sig = "NEUTRE"

    # ── Niveaux de gestion professionnelle ──
    if "BUY" in sig:
        entry      = round(min(price, fib.get("0.618", price) or price), 6)
        stop_loss  = round(support * 0.985, 6)   # Sous le support avec marge
        tp1        = round(price + (price - stop_loss) * 1.0, 6)   # +1R (breakeven)
        tp2        = round(price + (price - stop_loss) * 1.5, 6)   # +1.5R (partial TP)
        take_profit= round(resistance * 0.99, 6)                    # Résistance (TP final)
    else:
        entry      = round(max(price, fib.get("0.382", price) or price), 6)
        stop_loss  = round(resistance * 1.015, 6)
        tp1        = round(price - (stop_loss - price) * 1.0, 6)
        tp2        = round(price - (stop_loss - price) * 1.5, 6)
        take_profit= round(support * 1.01, 6)

    risk   = abs(price - stop_loss)
    reward = abs(take_profit - price)
    rr     = round(reward / risk, 2) if risk > 0 else 0

    # Bonus confluence si R:R > 1.5
    if rr >= 1.5:
        confluence += 1
        confluences.append(f"✦ R:R {rr} ≥ 1.5 — trade valide +1")

    confluence = min(confluence, 10)

    # Probabilité du scénario
    if confluence >= 8:   proba = "75-85%"
    elif confluence >= 6: proba = "60-70%"
    elif confluence >= 4: proba = "45-55%"
    else:                 proba = "< 40%"

    # Invalidation du setup
    if "BUY" in sig:
        invalidation = f"Clôture Daily sous ${stop_loss:,.4f}"
    else:
        invalidation = f"Clôture Daily au-dessus de ${stop_loss:,.4f}"

    return {
        "signal":       sig,
        "confluence":   confluence,
        "proba":        proba,
        "rsi":          round(rsi, 1) if rsi else None,
        "ma20":         round(ma20, 4) if ma20 else None,
        "ma50":         round(ma50, 4) if ma50 else None,
        "support":      support,
        "resistance":   resistance,
        "fib":          fib,
        "entry":        entry,
        "stop_loss":    stop_loss,
        "tp1":          tp1,
        "tp2":          tp2,
        "take_profit":  take_profit,
        "rr_ratio":     rr,
        "confluences":  confluences,
        "invalidation": invalidation,
    }

def format_signal(sig):
    return {
        "STRONG_BUY":  "🟢 FORT ACHAT",
        "BUY":         "🟩 ACHAT",
        "STRONG_SELL": "🔴 FORT VENTE",
        "SELL":        "🟥 VENTE",
        "NEUTRE":      "🟡 NEUTRE",
    }.get(sig, "🟡 NEUTRE")

def risque_label(rsi, dist_support):
    if rsi and (rsi > 70 or rsi < 30): return "🔴 ÉLEVÉ"
    if dist_support < 3: return "🟢 FAIBLE"
    return "🟡 MODÉRÉ"

def fmt_price(v, is_index=False):
    return f"{v:,.2f} pts" if is_index else f"${v:,.2f}"

def fmt_pct(a, b):
    p = ((b - a) / a) * 100
    return f"{'+' if p >= 0 else ''}{p:.1f}%"

def arrow(v): return "↑" if v >= 0 else "↓"
def fmt(v):   return f"{'+'if v>=0 else ''}{v:.2f}%"


# ════════════════════════════════════════════════════════════
#  CONSTRUCTION DES MESSAGES
# ════════════════════════════════════════════════════════════

def session_actuelle():
    h = datetime.now().hour
    if 8 <= h < 12:  return "🇬🇧 Kill Zone Londres — Haute probabilité"
    if 14 <= h < 17: return "🇺🇸 Kill Zone New York — Haute probabilité"
    if 17 <= h < 22: return "🌆 Session NY PM — Continuation"
    if 1 <= h < 9:   return "🌏 Session Asie — Attendre Kill Zone"
    return "🌙 Hors session — Pas de nouveau trade"

def emoji_confluence(score):
    if score >= 8: return "🟢🟢"
    if score >= 6: return "🟢"
    if score >= 4: return "🟡"
    return "🔴"

def format_card(label, price, d_pct, w_pct, ana, is_index=False, extra_lines=None):
    fp = lambda v: f"{v:,.2f} pts" if is_index else (f"${v:,.4f}" if price < 10 else f"${v:,.2f}")
    c = ana.get("confluence", 0)
    verdict = "🟢 Fiable" if c >= 7 else ("🟡 Moyen" if c >= 5 else "🔴 Faible")

    lines = [
        f"*{label}* — {fp(price)}",
        f"📊 24h {arrow(d_pct)}{fmt(d_pct)} | 7j {arrow(w_pct)}{fmt(w_pct)}",
        f"",
        f"🎯 Fiabilité du setup : {verdict} ({c}/10)",
        f"",
        f"📥 Zone d'entrée : *{fp(ana['entry'])}*",
        f"🛑 Stop-Loss : *{fp(ana['stop_loss'])}* ({fmt_pct(price, ana['stop_loss'])})",
        f"🏹 Take Profit : *{fp(ana['take_profit'])}* ({fmt_pct(price, ana['take_profit'])})",
        f"⚖️ R/R : *{ana['rr_ratio']}x*",
    ]
    if extra_lines:
        lines += extra_lines
    return "\n".join(lines)

def format_crypto_card(label, d, history=None):
    c1h  = d.get("price_change_percentage_1h_in_currency") or 0
    c24h = d.get("price_change_percentage_24h_in_currency") or 0
    c7d  = d.get("price_change_percentage_7d_in_currency") or 0
    price  = d["current_price"]
    high24 = d.get("high_24h", price)
    low24  = d.get("low_24h", price)

    if history and len(history) >= 20:
        ana = analyse_technique(history, history, history, price)
    else:
        ana = {
            "signal": "NEUTRE", "confluence": 0, "proba": "N/A",
            "rsi": None, "ma20": None, "ma50": None,
            "support": low24, "resistance": high24,
            "entry": price, "stop_loss": price * 0.95,
            "tp1": price * 1.03, "tp2": price * 1.05,
            "take_profit": high24, "rr_ratio": 0,
            "fib": {"0.618": low24}, "invalidation": "N/A"
        }
    return format_card(label, price, c24h, c7d, ana, extra_lines=[f"📊 1h : {arrow(c1h)}{fmt(c1h)}"])

def format_stock_card(label, ticker, d):
    c1d    = d["change_1d"]
    c5d    = d["change_5d"]
    price  = d["price"]
    idx    = ticker.startswith("^")
    closes = d.get("closes", [])
    highs  = d.get("highs", [])
    lows   = d.get("lows", [])

    if len(closes) >= 20:
        ana = analyse_technique(closes, highs, lows, price)
    else:
        ana = {
            "signal": "NEUTRE", "confluence": 0, "proba": "N/A",
            "rsi": None, "ma20": None, "ma50": None,
            "support": d["low"], "resistance": d["high"],
            "entry": price, "stop_loss": price * 0.95,
            "tp1": price * 1.03, "tp2": price * 1.05,
            "take_profit": d["high"], "rr_ratio": 0,
            "fib": {"0.618": d["low"]}, "invalidation": "N/A"
        }
    return format_card(label, price, c1d, c5d, ana, is_index=idx)

def build_market_msg(cp, yp):
    now = datetime.now().strftime("%d/%m/%Y à %Hh%M")
    parts = [f"📊 *RAPPORT DE MARCHÉ*\n_{now}_"]
    parts.append(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🌍 *MACRO*\n"
        "🏦 Fed : 4.25–4.50% · Statu quo attendu\n"
        "🏦 BCE : 2.65% · Baisse progressive\n"
        "📊 Contexte : favorable aux actifs risqués"
    )
    parts.append("━━━━━━━━━━━━━━━━━━━━━━\n🪙 *CRYPTO*")
    for cid, label in CRYPTO_ASSETS.items():
        d = cp.get(cid)
        if not d:
            continue
        history = get_crypto_history(cid, days=60)
        parts.append(format_crypto_card(label, d, history))
    parts.append("━━━━━━━━━━━━━━━━━━━━━━\n📈 *ACTIONS & INDICES*")
    for ticker, label in YAHOO_ASSETS.items():
        d = yp.get(ticker)
        if d:
            parts.append(format_stock_card(label, ticker, d))
        else:
            parts.append(f"*{label}* — ⚠️ indisponible")
    parts.append(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ _Pas de conseil en investissement. Capitaux à risque._"
    )
    return "\n\n".join(parts)

def build_signal_msg(cp, yp):
    now = datetime.now().strftime("%Hh%M")
    lines = [f"🎯 *SIGNAUX — {now}*\n"]
    lines.append("🪙 *Crypto*")
    for cid, label in CRYPTO_ASSETS.items():
        d = cp.get(cid)
        if not d: continue
        c1h  = d.get("price_change_percentage_1h_in_currency") or 0
        c24h = d.get("price_change_percentage_24h_in_currency") or 0
        c7d  = d.get("price_change_percentage_7d_in_currency") or 0
        sig = signal_crypto(c1h, c24h, c7d)
        lines.append(f"{format_signal(sig)} *{label}* — ${d['current_price']:,.4f}")
    lines.append("\n📈 *Actions & Indices*")
    for ticker, label in YAHOO_ASSETS.items():
        d = yp.get(ticker)
        if not d: continue
        idx = ticker.startswith("^")
        sig = signal_stock(d["change_1d"], d["change_5d"])
        lines.append(f"{format_signal(sig)} *{label}* — {fmt_price(d['price'], idx)}")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════
#  ALERTES & RAPPORT AUTO
# ════════════════════════════════════════════════════════════

def current_price(symbol):
    if symbol in CRYPTO_ASSETS:
        d = get_crypto_prices().get(symbol)
        return d["current_price"] if d else None
    d = get_stock_price(symbol)
    return d["price"] if d else None

def check_alerts():
    alerts  = load_alerts()
    changed = False
    for uid_str, user_alerts in alerts.items():
        for a in user_alerts:
            if not a.get("active"): continue
            price = current_price(a["symbol"])
            if price is None: continue
            hit = (
                (a["direction"] == "above" and price >= a["target"]) or
                (a["direction"] == "below" and price <= a["target"])
            )
            if hit:
                dir_txt = "dépassé" if a["direction"] == "above" else "descendu sous"
                try:
                    bot.send_message(int(uid_str),
                        f"🔔 *ALERTE !*\n\n"
                        f"*{a['symbol']}* a {dir_txt} *${a['target']:,.2f}*\n"
                        f"Prix actuel : *${price:,.2f}*",
                        parse_mode="Markdown")
                except: pass
                a["active"] = False
                changed = True
    if changed: save_alerts(alerts)

def send_daily_report():
    recipients = list(set(load_users() + [ADMIN_ID]))
    cp = get_crypto_prices()
    yp = {t: get_stock_price(t) for t in YAHOO_ASSETS}
    yp = {k: v for k, v in yp.items() if v}
    if not cp and not yp: return
    msg = build_market_msg(cp, yp)
    for uid in recipients:
        try: send_long(uid, msg)
        except Exception as e: print(f"Erreur envoi {uid}: {e}")
    print(f"[{datetime.now().strftime('%H:%M')}] Rapport envoyé à {len(recipients)} utilisateurs")


# ════════════════════════════════════════════════════════════
#  COMMANDES
# ════════════════════════════════════════════════════════════

def send_long(chat_id, text, reply_to=None):
    """Envoie un message long découpé en morceaux de 4000 chars max"""
    MAX = 4000
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > MAX:
            if current:
                chunks.append(current)
            current = line
        else:
            current += ("\n" if current else "") + line
    if current:
        chunks.append(current)
    for i, chunk in enumerate(chunks):
        try:
            if i == 0 and reply_to:
                bot.reply_to(reply_to, chunk, parse_mode="Markdown")
            else:
                bot.send_message(chat_id, chunk, parse_mode="Markdown")
        except Exception as e:
            print(f"Erreur envoi chunk {i}: {e}")

@bot.message_handler(commands=["start", "help"])
def cmd_start(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔ Accès non autorisé."); return
    bot.reply_to(message,
        "👋 *Bot Trading*\n\n"
        "📊 */marche* — Rapport complet\n"
        "🎯 */signaux* — Signaux rapides\n\n"
        "💰 */prix actif* — Liste des cryptos\n"
        "💰 */prix action* — Liste des actions\n"
        "💰 */prix BTC* — Analyse détaillée\n\n"
        "🔔 */alerte BTC 70000 above*\n"
        "🔔 */alerte AAPL 200 below*\n"
        "📋 */mesalertes*\n"
        "🗑️ */supprimeralertes*\n"
        "🪪 */myid*",
        parse_mode="Markdown")

@bot.message_handler(commands=["marche"])
def cmd_marche(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔ Accès non autorisé."); return
    bot.reply_to(message, "⏳ Récupération des données...")
    cp = get_crypto_prices()
    yp = {t: get_stock_price(t) for t in YAHOO_ASSETS}
    yp = {k: v for k, v in yp.items() if v}
    if not cp and not yp:
        bot.reply_to(message, "❌ Erreur API. Réessaie."); return
    send_long(message.chat.id, build_market_msg(cp, yp), reply_to=message)

@bot.message_handler(commands=["signaux"])
def cmd_signaux(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔ Accès non autorisé."); return
    bot.reply_to(message, "⏳ Calcul des signaux...")
    cp = get_crypto_prices()
    yp = {t: get_stock_price(t) for t in YAHOO_ASSETS}
    yp = {k: v for k, v in yp.items() if v}
    bot.reply_to(message, build_signal_msg(cp, yp), parse_mode="Markdown")

@bot.message_handler(commands=["prix"])
def cmd_prix(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔ Accès non autorisé."); return
    parts = message.text.split()

    if len(parts) < 2:
        bot.reply_to(message,
            "💰 *Commande /prix*\n\n"
            "🪙 */prix actif* — Liste des cryptos\n"
            "📈 */prix action* — Liste des actions\n"
            "Ou directement : */prix BTC* · */prix AAPL*",
            parse_mode="Markdown"); return

    keyword = parts[1].upper()

    if keyword == "ACTIF":
        lines = ["🪙 *Cryptos disponibles :*\n"]
        for short, cid in CRYPTO_MAP.items():
            lines.append(f"• /prix {short} — {CRYPTO_ASSETS[cid]}")
        bot.reply_to(message, "\n".join(lines), parse_mode="Markdown"); return

    if keyword == "ACTION":
        lines = ["📈 *Actions & Indices disponibles :*\n"]
        for ticker, label in YAHOO_ASSETS.items():
            display = ticker.replace("^","").replace(".PA","")
            if ticker == "^FCHI": display = "CAC"
            elif ticker == "^GSPC": display = "SP500"
            elif ticker == "^IXIC": display = "NASDAQ"
            elif ticker == "^GDAXI": display = "DAX"
            lines.append(f"• /prix {display} — {label}")
        bot.reply_to(message, "\n".join(lines), parse_mode="Markdown"); return

    symbol = resolve(keyword)

    # Crypto
    if symbol in CRYPTO_ASSETS:
        bot.reply_to(message, "⏳ Récupération...")
        # Essayer jusqu'à 3 fois
        d = None
        for attempt in range(3):
            cp = get_crypto_prices()
            d = cp.get(symbol)
            if d:
                break
        if not d:
            bot.reply_to(message, "❌ CoinGecko indisponible, réessaie dans 1 minute."); return
        history = get_crypto_history(symbol, days=60)
        send_long(message.chat.id, format_crypto_card(CRYPTO_ASSETS[symbol], d, history), reply_to=message); return

    # Action / Indice
    if symbol in YAHOO_ASSETS or symbol.startswith("^"):
        bot.reply_to(message, "⏳ Récupération...")
        d = get_stock_price(symbol)
        if not d:
            bot.reply_to(message, "❌ Données indisponibles, réessaie dans 1 minute."); return
        label = YAHOO_ASSETS.get(symbol, symbol)
        send_long(message.chat.id, format_stock_card(label, symbol, d), reply_to=message); return

    bot.reply_to(message,
        f"❓ Actif inconnu : *{keyword}*\n\n"
        f"👉 /prix actif — cryptos\n👉 /prix action — actions",
        parse_mode="Markdown")

@bot.message_handler(commands=["alerte"])
def cmd_alerte(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔ Accès non autorisé."); return
    parts = message.text.split()
    if len(parts) != 4:
        bot.reply_to(message,
            "Usage : /alerte SYMBOLE PRIX DIRECTION\n\n"
            "Ex : /alerte BTC 70000 above\n"
            "Ex : /alerte AAPL 200 below"); return
    symbol    = resolve(parts[1].upper())
    direction = parts[3].lower()
    if direction not in ("above", "below"):
        bot.reply_to(message, "❓ Utilise 'above' ou 'below'"); return
    try:    target = float(parts[2])
    except: bot.reply_to(message, "❓ Prix invalide."); return
    add_alert(message.from_user.id, symbol, target, direction)
    dir_txt = "dépasse" if direction == "above" else "descend sous"
    label = {**CRYPTO_ASSETS, **YAHOO_ASSETS}.get(symbol, symbol)
    bot.reply_to(message, f"✅ Alerte créée !\n*{label}* {dir_txt} *${target:,.2f}*", parse_mode="Markdown")

@bot.message_handler(commands=["mesalertes"])
def cmd_mes_alertes(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔ Accès non autorisé."); return
    active = [a for a in get_user_alerts(message.from_user.id) if a.get("active")]
    if not active:
        bot.reply_to(message, "📋 Aucune alerte active."); return
    lines = ["📋 *Tes alertes actives :*\n"]
    for i, a in enumerate(active, 1):
        lines.append(f"{i}. *{a['symbol']}* {'>' if a['direction']=='above' else '<'} ${a['target']:,.2f}")
    bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")

@bot.message_handler(commands=["supprimeralertes"])
def cmd_suppr(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔ Accès non autorisé."); return
    clear_user_alerts(message.from_user.id)
    bot.reply_to(message, "🗑️ Alertes supprimées.")

@bot.message_handler(commands=["myid"])
def cmd_myid(message):
    bot.reply_to(message, f"🪪 Ton ID : `{message.from_user.id}`", parse_mode="Markdown")

@bot.message_handler(commands=["adduser"])
def cmd_adduser(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Admin seulement."); return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        bot.reply_to(message, "Usage : /adduser ID"); return
    uid = int(parts[1])
    users = load_users()
    if uid in users:
        bot.reply_to(message, "ℹ️ Déjà autorisé."); return
    users.append(uid); save_users(users)
    bot.reply_to(message, f"✅ {uid} ajouté.")
    try: bot.send_message(uid, "✅ *Accès accordé !* Envoie /start", parse_mode="Markdown")
    except: pass

@bot.message_handler(commands=["removeuser"])
def cmd_removeuser(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Admin seulement."); return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        bot.reply_to(message, "Usage : /removeuser ID"); return
    uid = int(parts[1]); users = load_users()
    if uid not in users:
        bot.reply_to(message, "ℹ️ Pas dans la liste."); return
    users.remove(uid); save_users(users)
    bot.reply_to(message, f"🗑️ {uid} retiré.")

@bot.message_handler(commands=["listusers"])
def cmd_listusers(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Admin seulement."); return
    users = load_users()
    if not users:
        bot.reply_to(message, "📋 Aucun utilisateur."); return
    bot.reply_to(message,
        f"📋 *{len(users)} utilisateur(s) :*\n\n" + "\n".join(f"• {u}" for u in users),
        parse_mode="Markdown")

@bot.message_handler(commands=["rapport"])
def cmd_rapport(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Admin seulement."); return
    bot.reply_to(message, "📤 Envoi du rapport...")
    send_daily_report()
    bot.reply_to(message, "✅ Rapport envoyé.")

@bot.message_handler(func=lambda m: True)
def handle_unknown(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔ Accès non autorisé."); return
    bot.reply_to(message, "❓ Commande inconnue. Envoie /help.")


# ════════════════════════════════════════════════════════════
#  SCHEDULER & LANCEMENT
# ════════════════════════════════════════════════════════════

def run_scheduler():
    schedule.every(5).minutes.do(check_alerts)
    while True:
        schedule.run_pending()
        time.sleep(30)

if __name__ == "__main__":
    print("=" * 50)
    print("  BOT TRADING DÉMARRÉ")
    print(f"  Admin   : {ADMIN_ID}")
    print(f"  Rapport : {REPORT_HOUR}")
    print("=" * 50)
    threading.Thread(target=run_scheduler, daemon=True).start()
    bot.infinity_polling()
