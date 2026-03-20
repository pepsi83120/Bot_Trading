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
FMP_KEY     = os.environ.get("FMP_KEY", "demo")

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

# ── Actions & Indices (FMP) ────────────────────────────────
YAHOO_ASSETS = {
    "AAPL":   "🍎 Apple",
    "TSLA":   "🚗 Tesla",
    "NVDA":   "🖥️ NVIDIA",
    "MSFT":   "🪟 Microsoft",
    "GOOGL":  "🔍 Alphabet",
    "MC.PA":  "👜 LVMH",
    "AIR.PA": "✈️ Airbus",
    "TTE.PA": "🛢️ TotalEnergies",
    "BNP.PA": "🏦 BNP Paribas",
    "SU.PA":  "⚡ Schneider Electric",
    "^FCHI":  "🇫🇷 CAC 40",
    "^GSPC":  "🇺🇸 S&P 500",
    "^IXIC":  "💻 Nasdaq",
    "^GDAXI": "🇩🇪 DAX",
}

# Alias → ticker réel
ALIAS = {
    "CAC":       "^FCHI",
    "CAC40":     "^FCHI",
    "SP500":     "^GSPC",
    "SPX":       "^GSPC",
    "NASDAQ":    "^IXIC",
    "DAX":       "^GDAXI",
    "APPLE":     "AAPL",
    "TESLA":     "TSLA",
    "NVIDIA":    "NVDA",
    "MICROSOFT": "MSFT",
    "GOOGLE":    "GOOGL",
    "LVMH":      "MC.PA",
    "AIRBUS":    "AIR.PA",
    "TOTAL":     "TTE.PA",
    "BNP":       "BNP.PA",
    "SCHNEIDER": "SU.PA",
}

# FMP utilise des tickers légèrement différents pour les indices/actions EU
FMP_TICKER_MAP = {
    "^FCHI":  "FCHI",
    "^GSPC":  "GSPC",
    "^IXIC":  "IXIC",
    "^GDAXI": "GDAXI",
    "MC.PA":  "MC.PA",
    "AIR.PA": "AIR.PA",
    "TTE.PA": "TTE.PA",
    "BNP.PA": "BNP.PA",
    "SU.PA":  "SU.PA",
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

def get_stock_price(ticker):
    """Récupère les données via Financial Modeling Prep"""
    fmp_ticker = FMP_TICKER_MAP.get(ticker, ticker)
    try:
        # Prix actuel
        r = requests.get(
            f"https://financialmodelingprep.com/api/v3/quote/{fmp_ticker}",
            params={"apikey": FMP_KEY},
            timeout=10
        )
        r.raise_for_status()
        data = r.json()
        if not data or not isinstance(data, list):
            return None
        d = data[0]
        price    = float(d.get("price", 0))
        change1d = float(d.get("changesPercentage", 0))
        prev     = float(d.get("previousClose", price))
        high     = float(d.get("dayHigh", price))
        low      = float(d.get("dayLow", price))

        # Variation 5j via historical
        r2 = requests.get(
            f"https://financialmodelingprep.com/api/v3/historical-price-full/{fmp_ticker}",
            params={"apikey": FMP_KEY, "timeseries": 6},
            timeout=10
        )
        change5d = 0
        if r2.ok:
            hist = r2.json().get("historical", [])
            if len(hist) >= 5:
                p5 = float(hist[-1]["close"])
                change5d = ((price - p5) / p5) * 100

        if price == 0:
            return None
        return {
            "price":     price,
            "change_1d": change1d,
            "change_5d": change5d,
            "high":      high,
            "low":       low,
        }
    except Exception as e:
        print(f"Erreur FMP ({ticker}) : {e}")
        return None


# ════════════════════════════════════════════════════════════
#  SIGNAUX & ANALYSE
# ════════════════════════════════════════════════════════════

def signal_crypto(c1h, c24h, c7d):
    s = c1h * 0.5 + c24h * 0.3 + c7d * 0.2
    if s >= 2.5:  return "STRONG_BUY"
    if s >= 0.8:  return "BUY"
    if s <= -2.5: return "STRONG_SELL"
    if s <= -0.8: return "SELL"
    return "NEUTRE"

def signal_stock(c1d, c5d):
    s = c1d * 0.6 + c5d * 0.4
    if s >= 2.0:  return "STRONG_BUY"
    if s >= 0.5:  return "BUY"
    if s <= -2.0: return "STRONG_SELL"
    if s <= -0.5: return "SELL"
    return "NEUTRE"

def format_signal(sig):
    return {
        "STRONG_BUY":  "🟢 ACHETER FORT",
        "BUY":         "🟩 ACHETER",
        "STRONG_SELL": "🔴 VENDRE FORT",
        "SELL":        "🟥 VENDRE",
        "NEUTRE":      "🟡 ATTENDRE",
    }.get(sig, "🟡 ATTENDRE")

def risque(volatility):
    if volatility >= 6: return "🔴 ÉLEVÉ"
    if volatility >= 3: return "🟡 MODÉRÉ"
    return "🟢 FAIBLE"

def conseil_invest(sig, price, is_index=False):
    """Retourne une phrase de conseil simple"""
    if "BUY" in sig:
        if is_index: return "📥 Bon moment pour entrer progressivement"
        return "📥 Achetez en plusieurs fois pour lisser le risque"
    if "SELL" in sig:
        return "📤 Réduisez votre position ou attendez un rebond"
    return "⏳ Attendez une confirmation avant d'entrer"

def calcul_niveaux(price, sig):
    """Calcule entrée, objectif et stop-loss"""
    if "STRONG_BUY" in sig:
        entry = price * 0.99
        obj   = price * 1.15
        stop  = price * 0.93
    elif "BUY" in sig:
        entry = price * 0.985
        obj   = price * 1.08
        stop  = price * 0.95
    elif "STRONG_SELL" in sig:
        entry = price * 1.01
        obj   = price * 0.88
        stop  = price * 1.07
    elif "SELL" in sig:
        entry = price * 1.005
        obj   = price * 0.94
        stop  = price * 1.04
    else:
        entry = price * 0.985
        obj   = price * 1.03
        stop  = price * 0.96
    return entry, obj, stop

def fmt_price(v, is_index=False):
    return f"{v:,.0f} pts" if is_index else f"${v:,.2f}"

def fmt_pct(a, b):
    p = ((b - a) / a) * 100
    return f"{'+' if p >= 0 else ''}{p:.1f}%"

def arrow(v): return "↑" if v >= 0 else "↓"
def fmt(v):   return f"{'+'if v>=0 else ''}{v:.2f}%"


# ════════════════════════════════════════════════════════════
#  CONSTRUCTION DES MESSAGES
# ════════════════════════════════════════════════════════════

def format_crypto_card(label, d):
    c1h  = d.get("price_change_percentage_1h_in_currency") or 0
    c24h = d.get("price_change_percentage_24h_in_currency") or 0
    c7d  = d.get("price_change_percentage_7d_in_currency") or 0
    price = d["current_price"]
    sig   = signal_crypto(c1h, c24h, c7d)
    entry, obj, stop = calcul_niveaux(price, sig)
    vol = abs(c1h) + abs(c24h) * 0.5 + abs(c7d) * 0.3

    return (
        f"*{label}* — ${price:,.4f}\n"
        f"📊 1h {arrow(c1h)}{fmt(c1h)} | 24h {arrow(c24h)}{fmt(c24h)} | 7j {arrow(c7d)}{fmt(c7d)}\n"
        f"🎯 *{format_signal(sig)}*\n"
        f"⚠️ Risque : {risque(vol)}\n"
        f"💡 {conseil_invest(sig, price)}\n"
        f"📥 Acheter à : ~${entry:,.4f}\n"
        f"🏹 Objectif : ~${obj:,.4f} ({fmt_pct(price, obj)})\n"
        f"🛑 Stop-loss : ~${stop:,.4f} ({fmt_pct(price, stop)})"
    )

def format_stock_card(label, ticker, d):
    c1d   = d["change_1d"]
    c5d   = d["change_5d"]
    price = d["price"]
    idx   = ticker.startswith("^") or ticker in ("FCHI","GSPC","IXIC","GDAXI")
    sig   = signal_stock(c1d, c5d)
    entry, obj, stop = calcul_niveaux(price, sig)
    vol = abs(c1d) * 0.7 + abs(c5d) * 0.3

    return (
        f"*{label}* — {fmt_price(price, idx)}\n"
        f"📊 Auj. {arrow(c1d)}{fmt(c1d)} | Sem. {arrow(c5d)}{fmt(c5d)}\n"
        f"📈 Haut: {fmt_price(d['high'], idx)} | Bas: {fmt_price(d['low'], idx)}\n"
        f"🎯 *{format_signal(sig)}*\n"
        f"⚠️ Risque : {risque(vol)}\n"
        f"💡 {conseil_invest(sig, price, idx)}\n"
        f"📥 Acheter à : ~{fmt_price(entry, idx)}\n"
        f"🏹 Objectif : ~{fmt_price(obj, idx)} ({fmt_pct(price, obj)})\n"
        f"🛑 Stop-loss : ~{fmt_price(stop, idx)} ({fmt_pct(price, stop)})"
    )

def build_market_msg(cp, yp):
    now = datetime.now().strftime("%d/%m/%Y à %Hh%M")
    parts = [f"📊 *RAPPORT DE MARCHÉ*\n_{now}_"]

    # Macro
    parts.append(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🌍 *MACRO*\n"
        "🏦 Fed : 4.25–4.50% · Statu quo attendu\n"
        "🏦 BCE : 2.65% · Baisse progressive\n"
        "📊 Contexte : favorable aux actifs risqués"
    )

    # Crypto
    parts.append("━━━━━━━━━━━━━━━━━━━━━━\n🪙 *CRYPTO*")
    for cid, label in CRYPTO_ASSETS.items():
        d = cp.get(cid)
        if d:
            parts.append(format_crypto_card(label, d))

    # Actions
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
        try: bot.send_message(uid, msg, parse_mode="Markdown")
        except Exception as e: print(f"Erreur envoi {uid}: {e}")
    print(f"[{datetime.now().strftime('%H:%M')}] Rapport envoyé à {len(recipients)} utilisateurs")


# ════════════════════════════════════════════════════════════
#  COMMANDES
# ════════════════════════════════════════════════════════════

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
    bot.reply_to(message, build_market_msg(cp, yp), parse_mode="Markdown")

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
        bot.reply_to(message, format_crypto_card(CRYPTO_ASSETS[symbol], d), parse_mode="Markdown"); return

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
    schedule.every().day.at(REPORT_HOUR).do(send_daily_report)
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
