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

def get_crypto_prices():
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
            timeout=10
        )
        r.raise_for_status()
        return {c["id"]: c for c in r.json()}
    except Exception as e:
        print(f"Erreur CoinGecko : {e}")
        return {}

def get_crypto_history(coin_id, days=60):
    """Historique des prix pour les indicateurs techniques"""
    try:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart",
            params={"vs_currency": "usd", "days": days, "interval": "daily"},
            timeout=10
        )
        r.raise_for_status()
        prices = [p[1] for p in r.json().get("prices", [])]
        return prices
    except Exception as e:
        print(f"Erreur historique CoinGecko ({coin_id}): {e}")
        return []

def get_stock_price(ticker):
    """Récupère les données + historique 60j via stooq.com"""
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
    try:
        r = requests.get(
            "https://stooq.com/q/d/l/",
            params={"s": stooq_ticker, "i": "d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15
        )
        r.raise_for_status()
        lines = r.text.strip().split("\n")
        print(f"Stooq {ticker} → {stooq_ticker} : {len(lines)} lignes")
        data_lines = [l for l in lines[1:] if l.strip()]
        if len(data_lines) < 2:
            return None

        def parse_line(line):
            parts = line.split(",")
            return {
                "close": float(parts[4]),
                "high":  float(parts[2]),
                "low":   float(parts[3]),
            }

        parsed = [parse_line(l) for l in data_lines]
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
    Analyse complète : RSI + MA + Support/Résistance + Signal
    Retourne un dict avec tous les niveaux calculés
    """
    rsi  = calc_rsi(closes)
    ma20 = calc_ma(closes, 20)
    ma50 = calc_ma(closes, 50)
    support, resistance = calc_support_resistance(highs, lows, closes, price)
    fib = calc_fibonacci(max(highs[-20:]) if len(highs) >= 20 else max(highs),
                         min(lows[-20:])  if len(lows)  >= 20 else min(lows))

    # Signal basé sur RSI + position par rapport aux MAs
    score = 0
    raisons = []

    if rsi is not None:
        if rsi < 30:
            score += 2
            raisons.append(f"RSI {rsi:.0f} — zone de survente (signal achat)")
        elif rsi < 45:
            score += 1
            raisons.append(f"RSI {rsi:.0f} — faiblesse, rebond possible")
        elif rsi > 70:
            score -= 2
            raisons.append(f"RSI {rsi:.0f} — zone de surachat (signal vente)")
        elif rsi > 55:
            score -= 1
            raisons.append(f"RSI {rsi:.0f} — momentum haussier")
        else:
            raisons.append(f"RSI {rsi:.0f} — zone neutre")

    if ma20 and ma50:
        if price > ma20 > ma50:
            score += 2
            raisons.append(f"Prix au-dessus MA20 & MA50 — tendance haussière")
        elif price < ma20 < ma50:
            score -= 2
            raisons.append(f"Prix sous MA20 & MA50 — tendance baissière")
        elif price > ma20:
            score += 1
            raisons.append(f"Prix au-dessus MA20 — court terme positif")
        else:
            score -= 1
            raisons.append(f"Prix sous MA20 — court terme négatif")

    # Distance au support/résistance
    dist_support    = ((price - support) / price) * 100
    dist_resistance = ((resistance - price) / price) * 100

    if dist_support < 2:
        score += 1
        raisons.append(f"Prix proche du support — opportunité d'achat")
    if dist_resistance < 2:
        score -= 1
        raisons.append(f"Prix proche de la résistance — prudence")

    # Signal final
    if score >= 3:    sig = "STRONG_BUY"
    elif score >= 1:  sig = "BUY"
    elif score <= -3: sig = "STRONG_SELL"
    elif score <= -1: sig = "SELL"
    else:             sig = "NEUTRE"

    # Stop-loss = sous le support (-1% de marge)
    stop_loss  = round(support * 0.99, 4)
    # Take profit = vers la résistance
    take_profit = round(resistance * 0.99, 4)
    # Zone d'entrée = prix actuel ou légèrement sous
    entry = round(price * 0.99 if "BUY" in sig else price * 1.01, 4)

    # Ratio risque/rendement
    risk   = abs(price - stop_loss)
    reward = abs(take_profit - price)
    rr     = round(reward / risk, 2) if risk > 0 else 0

    return {
        "signal":      sig,
        "rsi":         round(rsi, 1) if rsi else None,
        "ma20":        round(ma20, 4) if ma20 else None,
        "ma50":        round(ma50, 4) if ma50 else None,
        "support":     support,
        "resistance":  resistance,
        "fib":         fib,
        "entry":       entry,
        "stop_loss":   stop_loss,
        "take_profit": take_profit,
        "rr_ratio":    rr,
        "raisons":     raisons,
        "score":       score,
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

def format_crypto_card(label, d, history=None):
    c1h  = d.get("price_change_percentage_1h_in_currency") or 0
    c24h = d.get("price_change_percentage_24h_in_currency") or 0
    c7d  = d.get("price_change_percentage_7d_in_currency") or 0
    price = d["current_price"]
    high24 = d.get("high_24h", price)
    low24  = d.get("low_24h", price)

    if history and len(history) >= 20:
        closes = history
        highs  = closes  # CoinGecko ne donne que les closes en daily
        lows   = closes
        ana = analyse_technique(closes, highs, lows, price)
    else:
        # Fallback si pas d'historique
        ana = {
            "signal": "NEUTRE", "rsi": None, "ma20": None, "ma50": None,
            "support": low24, "resistance": high24,
            "entry": price * 0.99, "stop_loss": price * 0.95,
            "take_profit": price * 1.08, "rr_ratio": 0,
            "raisons": [], "fib": {}
        }

    sig = ana["signal"]
    dist_support = ((price - ana["support"]) / price) * 100

    lines = [
        f"*{label}* — ${price:,.4f}",
        f"📊 1h {arrow(c1h)}{fmt(c1h)} | 24h {arrow(c24h)}{fmt(c24h)} | 7j {arrow(c7d)}{fmt(c7d)}",
        f"",
        f"🎯 *Signal : {format_signal(sig)}*",
        f"⚠️ Risque : {risque_label(ana['rsi'], dist_support)}",
    ]
    if ana["rsi"]:
        lines.append(f"📉 RSI({14}) : {ana['rsi']}")
    if ana["ma20"]:
        lines.append(f"📈 MA20 : ${ana['ma20']:,.4f}" + (" ✅" if price > ana["ma20"] else " ❌"))
    lines += [
        f"",
        f"🔴 Support : ${ana['support']:,.4f}",
        f"🟢 Résistance : ${ana['resistance']:,.4f}",
        f"",
        f"📥 *Zone d'entrée : ${ana['entry']:,.4f}*",
        f"🏹 *Take Profit : ${ana['take_profit']:,.4f}* ({fmt_pct(price, ana['take_profit'])})",
        f"🛑 *Stop-Loss : ${ana['stop_loss']:,.4f}* ({fmt_pct(price, ana['stop_loss'])})",
        f"⚖️ Ratio R/R : {ana['rr_ratio']}",
    ]
    if ana["raisons"]:
        lines.append(f"\n💡 " + " | ".join(ana["raisons"][:2]))
    return "\n".join(lines)

def format_stock_card(label, ticker, d):
    c1d   = d["change_1d"]
    c5d   = d["change_5d"]
    price = d["price"]
    idx   = ticker.startswith("^")
    closes = d.get("closes", [])
    highs  = d.get("highs", [])
    lows   = d.get("lows", [])

    if len(closes) >= 20:
        ana = analyse_technique(closes, highs, lows, price)
    else:
        ana = {
            "signal": "NEUTRE", "rsi": None, "ma20": None, "ma50": None,
            "support": d["low"], "resistance": d["high"],
            "entry": price * 0.99, "stop_loss": price * 0.95,
            "take_profit": price * 1.08, "rr_ratio": 0,
            "raisons": [], "fib": {}
        }

    sig = ana["signal"]
    dist_support = ((price - ana["support"]) / price) * 100
    fp = lambda v: fmt_price(v, idx)

    lines = [
        f"*{label}* — {fp(price)}",
        f"📊 Auj. {arrow(c1d)}{fmt(c1d)} | Sem. {arrow(c5d)}{fmt(c5d)}",
        f"📈 Haut: {fp(d['high'])} | Bas: {fp(d['low'])}",
        f"",
        f"🎯 *Signal : {format_signal(sig)}*",
        f"⚠️ Risque : {risque_label(ana['rsi'], dist_support)}",
    ]
    if ana["rsi"]:
        lines.append(f"📉 RSI(14) : {ana['rsi']}")
    if ana["ma20"]:
        lines.append(f"📈 MA20 : {fp(ana['ma20'])}" + (" ✅" if price > ana["ma20"] else " ❌"))
    if ana["ma50"]:
        lines.append(f"📈 MA50 : {fp(ana['ma50'])}" + (" ✅" if price > ana["ma50"] else " ❌"))
    lines += [
        f"",
        f"🔴 Support : {fp(ana['support'])}",
        f"🟢 Résistance : {fp(ana['resistance'])}",
        f"",
        f"📥 *Zone d'entrée : {fp(ana['entry'])}*",
        f"🏹 *Take Profit : {fp(ana['take_profit'])}* ({fmt_pct(price, ana['take_profit'])})",
        f"🛑 *Stop-Loss : {fp(ana['stop_loss'])}* ({fmt_pct(price, ana['stop_loss'])})",
        f"⚖️ Ratio R/R : {ana['rr_ratio']}",
    ]
    if ana["raisons"]:
        lines.append(f"\n💡 " + " | ".join(ana["raisons"][:2]))
    return "\n".join(lines)

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
        d = get_crypto_prices().get(symbol)
        if not d:
            bot.reply_to(message, "❌ Données indisponibles."); return
        history = get_crypto_history(symbol, days=60)
        bot.reply_to(message, format_crypto_card(CRYPTO_ASSETS[symbol], d, history), parse_mode="Markdown"); return

    # Action / Indice
    if symbol in YAHOO_ASSETS or symbol.startswith("^"):
        bot.reply_to(message, "⏳ Récupération...")
        d = get_stock_price(symbol)
        if not d:
            bot.reply_to(message, "❌ Données indisponibles."); return
        label = YAHOO_ASSETS.get(symbol, symbol)
        bot.reply_to(message, format_stock_card(label, symbol, d), parse_mode="Markdown"); return

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
