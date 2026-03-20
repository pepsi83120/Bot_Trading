import telebot
import requests
import yfinance as yf
import json
import os
import schedule
import time
import threading
from datetime import datetime

# ============================================================
#  CONFIGURATION — variables d'environnement
# ============================================================
BOT_TOKEN   = os.environ.get("BOT_TOKEN")
ADMIN_ID    = int(os.environ.get("ADMIN_ID", "0"))
REPORT_HOUR = os.environ.get("REPORT_HOUR", "08:00")

if not BOT_TOKEN:
    raise ValueError("❌ Variable d'environnement BOT_TOKEN manquante !")

bot = telebot.TeleBot(BOT_TOKEN)

USERS_FILE  = "users.json"
ALERTS_FILE = "alerts.json"

# ── Actifs crypto (CoinGecko) ──────────────────────────────
CRYPTO_ASSETS = {
    "bitcoin":       "₿ BTC",
    "ethereum":      "Ξ ETH",
    "solana":        "◎ SOL",
    "ripple":        "✦ XRP",
    "binancecoin":   "◆ BNB",
    "dogecoin":      "🐶 DOGE",
    "cardano":       "🔵 ADA",
    "avalanche-2":   "🔺 AVAX",
    "chainlink":     "🔗 LINK",
    "polkadot":      "⚪ DOT",
}

# Alias courts → id CoinGecko
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

# ── Actifs Yahoo Finance (actions Europe + US + indices) ───
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

# Alias actions
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

def resolve(symbol):
    """Convertit un alias en identifiant réel"""
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

def is_admin(user_id):
    return user_id == ADMIN_ID

def is_authorized(user_id):
    if is_admin(user_id):
        return True
    return user_id in load_users()


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

def add_alert(user_id, symbol, target_price, direction):
    alerts = load_alerts()
    key = str(user_id)
    if key not in alerts:
        alerts[key] = []
    alerts[key].append({
        "symbol":    symbol,
        "target":    float(target_price),
        "direction": direction,
        "active":    True
    })
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

def get_yahoo_price(ticker):
    """Récupère les données Yahoo Finance avec User-Agent pour éviter les blocages"""
    try:
        t = yf.Ticker(ticker)
        # Forcer un User-Agent navigateur pour éviter le blocage
        t._session = requests.Session()
        t._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        hist = t.history(period="7d", interval="1d")
        if hist.empty or len(hist) < 2:
            return None
        p_today = float(hist["Close"].iloc[-1])
        p_prev  = float(hist["Close"].iloc[-2])
        p_5d    = float(hist["Close"].iloc[0])
        return {
            "price":     p_today,
            "change_1d": ((p_today - p_prev) / p_prev) * 100,
            "change_5d": ((p_today - p_5d)   / p_5d)   * 100,
            "high":      float(hist["High"].iloc[-1]),
            "low":       float(hist["Low"].iloc[-1]),
        }
    except Exception as e:
        print(f"Erreur Yahoo ({ticker}) : {e}")
        return None


# ════════════════════════════════════════════════════════════
#  SIGNAUX, RISQUE, OBJECTIFS
# ════════════════════════════════════════════════════════════

def signal_crypto(c1h, c24h, c7d):
    s = c1h * 0.5 + c24h * 0.3 + c7d * 0.2
    if s >= 2.5:  return "🟢 STRONG BUY"
    if s >= 0.8:  return "🟩 BUY"
    if s <= -2.5: return "🔴 STRONG SELL"
    if s <= -0.8: return "🟥 SELL"
    return "🟡 NEUTRE"

def signal_stock(c1d, c5d):
    s = c1d * 0.6 + c5d * 0.4
    if s >= 2.0:  return "🟢 STRONG BUY"
    if s >= 0.5:  return "🟩 BUY"
    if s <= -2.0: return "🔴 STRONG SELL"
    if s <= -0.5: return "🟥 SELL"
    return "🟡 NEUTRE"

def risque_crypto(c1h, c24h, c7d):
    v = abs(c1h) + abs(c24h) * 0.5 + abs(c7d) * 0.3
    if v >= 8: return "🔴 ÉLEVÉ"
    if v >= 4: return "🟡 MODÉRÉ"
    return "🟢 FAIBLE"

def risque_stock(c1d, c5d):
    v = abs(c1d) * 0.7 + abs(c5d) * 0.3
    if v >= 4: return "🔴 ÉLEVÉ"
    if v >= 2: return "🟡 MODÉRÉ"
    return "🟢 FAIBLE"

def entree_crypto(price, c24h):
    return price * 0.98 if c24h >= 0 else price * 1.01

def entree_stock(price, c1d):
    return price * 0.985 if c1d >= 0 else price * 1.005

def objectif_crypto(price, signal):
    if "STRONG BUY"  in signal: return price * 1.15
    if "BUY"         in signal: return price * 1.08
    if "STRONG SELL" in signal: return price * 0.88
    if "SELL"        in signal: return price * 0.94
    return price * 1.03

def objectif_stock(price, signal):
    if "STRONG BUY"  in signal: return price * 1.12
    if "BUY"         in signal: return price * 1.07
    if "STRONG SELL" in signal: return price * 0.90
    if "SELL"        in signal: return price * 0.95
    return price * 1.03

def arrow(v):          return "↑" if v >= 0 else "↓"
def fmt(v):            return f"{'+'if v>=0 else ''}{v:.2f}%"
def fmtp(v, idx=False): return f"{v:,.0f} pts" if idx else f"${v:,.2f}"
def pct(a, b):         return ((b - a) / a) * 100


# ════════════════════════════════════════════════════════════
#  CONSTRUCTION DES MESSAGES
# ════════════════════════════════════════════════════════════

def get_macro_context():
    now = datetime.now().strftime("%d/%m/%Y")
    return (
        f"🌍 *CONTEXTE MACRO — {now}*\n\n"
        f"🏦 *Fed* — Taux : 4.25–4.50%\n"
        f"  Prochaine réunion : 18-19 mars · Statu quo attendu\n"
        f"  Inflation PCE ~2.5% · Marché data-dépendant\n\n"
        f"🏦 *BCE* — Taux : 2.65% · Baisse progressive\n"
        f"  Inflation zone euro ~2.3% · Ton accommodant\n\n"
        f"📊 *Global* : Contexte favorable aux actifs risqués\n"
        f"  Dollar stable · IA = catalyseur majeur"
    )

def build_market_msg(cp, yp):
    now = datetime.now().strftime("%d/%m/%Y à %Hh%M")
    lines = [
        f"📊 *RAPPORT DE MARCHÉ*\n_{now}_\n",
        get_macro_context(),
        "\n━━━━━━━━━━━━━━━━━━━━━━\n🪙 *CRYPTO*"
    ]

    for cid, label in CRYPTO_ASSETS.items():
        d = cp.get(cid)
        if not d:
            continue
        c1h  = d.get("price_change_percentage_1h_in_currency") or 0
        c24h = d.get("price_change_percentage_24h_in_currency") or 0
        c7d  = d.get("price_change_percentage_7d_in_currency") or 0
        price = d["current_price"]
        sig   = signal_crypto(c1h, c24h, c7d)
        obj   = objectif_crypto(price, sig)
        lines.append(
            f"\n*{label}* — ${price:,.2f}\n"
            f"  1h {arrow(c1h)}{fmt(c1h)} | 24h {arrow(c24h)}{fmt(c24h)} | 7j {arrow(c7d)}{fmt(c7d)}\n"
            f"  🎯 Signal : {sig}\n"
            f"  ⚠️ Risque : {risque_crypto(c1h, c24h, c7d)}\n"
            f"  📥 Entrée : ~${entree_crypto(price, c24h):,.2f}\n"
            f"  🏹 Objectif : ~${obj:,.2f} ({pct(price, obj):+.1f}%)"
        )

    lines.append("\n━━━━━━━━━━━━━━━━━━━━━━\n📈 *ACTIONS & INDICES*")
    for ticker, label in YAHOO_ASSETS.items():
        d = yp.get(ticker)
        if not d:
            lines.append(f"\n*{label}* — indisponible")
            continue
        idx   = ticker.startswith("^")
        c1d   = d["change_1d"]
        c5d   = d["change_5d"]
        price = d["price"]
        sig   = signal_stock(c1d, c5d)
        obj   = objectif_stock(price, sig)
        lines.append(
            f"\n*{label}* — {fmtp(price, idx)}\n"
            f"  1j {arrow(c1d)}{fmt(c1d)} | 5j {arrow(c5d)}{fmt(c5d)}\n"
            f"  Haut {fmtp(d['high'], idx)} | Bas {fmtp(d['low'], idx)}\n"
            f"  🎯 Signal : {sig}\n"
            f"  ⚠️ Risque : {risque_stock(c1d, c5d)}\n"
            f"  📥 Entrée : ~{fmtp(entree_stock(price, c1d), idx)}\n"
            f"  🏹 Objectif : ~{fmtp(obj, idx)} ({pct(price, obj):+.1f}%)"
        )

    lines.append(
        "\n━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ _Document informatif uniquement. Pas de conseil en investissement._\n"
        "_Source : CoinGecko + Yahoo Finance_"
    )
    return "\n".join(lines)

def build_signal_msg(cp, yp):
    now = datetime.now().strftime("%Hh%M")
    lines = [f"🎯 *SIGNAUX TRADING — {now}*\n", "🪙 *Crypto*"]
    for cid, label in CRYPTO_ASSETS.items():
        d = cp.get(cid)
        if not d:
            continue
        c1h  = d.get("price_change_percentage_1h_in_currency") or 0
        c24h = d.get("price_change_percentage_24h_in_currency") or 0
        c7d  = d.get("price_change_percentage_7d_in_currency") or 0
        lines.append(f"{signal_crypto(c1h,c24h,c7d)}  *{label}* — ${d['current_price']:,.2f}")
    lines.append("\n📈 *Actions & Indices*")
    for ticker, label in YAHOO_ASSETS.items():
        d = yp.get(ticker)
        if not d:
            continue
        idx = ticker.startswith("^")
        lines.append(f"{signal_stock(d['change_1d'],d['change_5d'])}  *{label}* — {fmtp(d['price'],idx)}")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════
#  ALERTES & RAPPORT AUTO
# ════════════════════════════════════════════════════════════

def current_price(symbol):
    if symbol in CRYPTO_ASSETS:
        d = get_crypto_prices().get(symbol)
        return d["current_price"] if d else None
    d = get_yahoo_price(symbol)
    return d["price"] if d else None

def check_alerts():
    alerts  = load_alerts()
    changed = False
    for uid_str, user_alerts in alerts.items():
        for a in user_alerts:
            if not a.get("active"):
                continue
            price = current_price(a["symbol"])
            if price is None:
                continue
            hit = (
                (a["direction"] == "above" and price >= a["target"]) or
                (a["direction"] == "below" and price <= a["target"])
            )
            if hit:
                dir_txt = "dépassé" if a["direction"] == "above" else "descendu sous"
                try:
                    bot.send_message(
                        int(uid_str),
                        f"🔔 *ALERTE DÉCLENCHÉE !*\n\n"
                        f"*{a['symbol']}* a {dir_txt} *${a['target']:,.2f}*\n"
                        f"Prix actuel : *${price:,.2f}*",
                        parse_mode="Markdown"
                    )
                except:
                    pass
                a["active"] = False
                changed = True
    if changed:
        save_alerts(alerts)

def send_daily_report():
    recipients = list(set(load_users() + [ADMIN_ID]))
    cp = get_crypto_prices()
    yp = {t: get_yahoo_price(t) for t in YAHOO_ASSETS}
    yp = {k: v for k, v in yp.items() if v}
    if not cp and not yp:
        return
    msg = build_market_msg(cp, yp)
    for uid in recipients:
        try:
            bot.send_message(uid, msg, parse_mode="Markdown")
        except Exception as e:
            print(f"Erreur envoi {uid}: {e}")
    print(f"[{datetime.now().strftime('%H:%M')}] Rapport envoyé à {len(recipients)} utilisateurs")


# ════════════════════════════════════════════════════════════
#  COMMANDES
# ════════════════════════════════════════════════════════════

@bot.message_handler(commands=["start", "help"])
def cmd_start(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔ Accès non autorisé.\nContacte l'admin.")
        return
    bot.reply_to(message,
        "👋 *Bot Trading — Commandes*\n\n"
        "📊 */marche* — Rapport approfondi (macro + signaux + objectifs)\n"
        "🎯 */signaux* — Signaux buy/sell rapides\n\n"
        "💰 */prix actif* — Liste toutes les cryptos\n"
        "💰 */prix action* — Liste toutes les actions\n"
        "💰 */prix BTC* — Prix + analyse détaillée\n\n"
        "🔔 */alerte BTC 70000 above* — Alerte si BTC > 70 000$\n"
        "🔔 */alerte AAPL 200 below* — Alerte si AAPL < 200$\n"
        "📋 */mesalertes* — Voir mes alertes actives\n"
        "🗑️ */supprimeralertes* — Supprimer mes alertes\n"
        "🪪 */myid* — Voir mon ID Telegram",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=["marche"])
def cmd_marche(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔ Accès non autorisé.")
        return
    bot.reply_to(message, "⏳ Récupération des données...")
    cp = get_crypto_prices()
    yp = {t: get_yahoo_price(t) for t in YAHOO_ASSETS}
    yp = {k: v for k, v in yp.items() if v}
    if not cp and not yp:
        bot.reply_to(message, "❌ Erreur API. Réessaie.")
        return
    bot.reply_to(message, build_market_msg(cp, yp), parse_mode="Markdown")

@bot.message_handler(commands=["signaux"])
def cmd_signaux(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔ Accès non autorisé.")
        return
    bot.reply_to(message, "⏳ Calcul des signaux...")
    cp = get_crypto_prices()
    yp = {t: get_yahoo_price(t) for t in YAHOO_ASSETS}
    yp = {k: v for k, v in yp.items() if v}
    bot.reply_to(message, build_signal_msg(cp, yp), parse_mode="Markdown")

@bot.message_handler(commands=["prix"])
def cmd_prix(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔ Accès non autorisé.")
        return
    parts = message.text.split()

    # /prix seul → menu
    if len(parts) < 2:
        bot.reply_to(message,
            "💰 *Commande /prix*\n\n"
            "🪙 */prix actif* — Liste toutes les cryptos\n"
            "📈 */prix action* — Liste toutes les actions\n\n"
            "Ou directement : */prix BTC* · */prix AAPL* · */prix CAC*",
            parse_mode="Markdown"
        )
        return

    keyword = parts[1].upper()

    # /prix actif → liste cryptos
    if keyword == "ACTIF":
        lines = ["🪙 *Cryptos disponibles :*\n"]
        for short, cid in CRYPTO_MAP.items():
            label = CRYPTO_ASSETS.get(cid, cid)
            lines.append(f"• /prix {short} — {label}")
        lines.append("\n_Exemple : /prix BTC_")
        bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")
        return

    # /prix action → liste actions
    if keyword == "ACTION":
        lines = ["📈 *Actions & Indices disponibles :*\n"]
        for ticker, label in YAHOO_ASSETS.items():
            display = "CAC" if ticker == "^FCHI" else ticker
            lines.append(f"• /prix {display} — {label}")
        lines.append("\n_Exemple : /prix AAPL_")
        bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")
        return

    # /prix SYMBOLE → données d'un actif
    symbol = resolve(keyword)

    # Crypto
    if symbol in CRYPTO_ASSETS:
        bot.reply_to(message, "⏳ Récupération...")
        d = get_crypto_prices().get(symbol)
        if not d:
            bot.reply_to(message, "❌ Données indisponibles.")
            return
        c1h  = d.get("price_change_percentage_1h_in_currency") or 0
        c24h = d.get("price_change_percentage_24h_in_currency") or 0
        c7d  = d.get("price_change_percentage_7d_in_currency") or 0
        price = d["current_price"]
        sig   = signal_crypto(c1h, c24h, c7d)
        obj   = objectif_crypto(price, sig)
        bot.reply_to(message,
            f"💰 *{CRYPTO_ASSETS[symbol]}*\n\n"
            f"Prix : *${price:,.2f}*\n"
            f"1h  : {arrow(c1h)} {fmt(c1h)}\n"
            f"24h : {arrow(c24h)} {fmt(c24h)}\n"
            f"7j  : {arrow(c7d)} {fmt(c7d)}\n"
            f"Haut 24h : ${d.get('high_24h', 0):,.2f}\n"
            f"Bas 24h  : ${d.get('low_24h', 0):,.2f}\n\n"
            f"🎯 Signal : {sig}\n"
            f"⚠️ Risque : {risque_crypto(c1h, c24h, c7d)}\n"
            f"📥 Entrée : ~${entree_crypto(price, c24h):,.2f}\n"
            f"🏹 Objectif : ~${obj:,.2f} ({pct(price, obj):+.1f}%)",
            parse_mode="Markdown"
        )
        return

    # Action / indice
    if symbol in YAHOO_ASSETS or symbol.startswith("^"):
        bot.reply_to(message, "⏳ Récupération...")
        d = get_yahoo_price(symbol)
        if not d:
            bot.reply_to(message, "❌ Données indisponibles.")
            return
        idx   = symbol.startswith("^")
        label = YAHOO_ASSETS.get(symbol, symbol)
        c1d   = d["change_1d"]
        c5d   = d["change_5d"]
        price = d["price"]
        sig   = signal_stock(c1d, c5d)
        obj   = objectif_stock(price, sig)
        bot.reply_to(message,
            f"💰 *{label}*\n\n"
            f"Prix : *{fmtp(price, idx)}*\n"
            f"1j  : {arrow(c1d)} {fmt(c1d)}\n"
            f"5j  : {arrow(c5d)} {fmt(c5d)}\n"
            f"Haut : {fmtp(d['high'], idx)}\n"
            f"Bas  : {fmtp(d['low'], idx)}\n\n"
            f"🎯 Signal : {sig}\n"
            f"⚠️ Risque : {risque_stock(c1d, c5d)}\n"
            f"📥 Entrée : ~{fmtp(entree_stock(price, c1d), idx)}\n"
            f"🏹 Objectif : ~{fmtp(obj, idx)} ({pct(price, obj):+.1f}%)",
            parse_mode="Markdown"
        )
        return

    # Inconnu
    bot.reply_to(message,
        f"❓ Actif inconnu : *{keyword}*\n\n"
        f"👉 /prix actif — voir les cryptos\n"
        f"👉 /prix action — voir les actions",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=["alerte"])
def cmd_alerte(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔ Accès non autorisé.")
        return
    parts = message.text.split()
    if len(parts) != 4:
        bot.reply_to(message,
            "Usage : /alerte SYMBOLE PRIX DIRECTION\n\n"
            "Exemples :\n/alerte BTC 70000 above\n/alerte AAPL 200 below\n\n"
            "above = si prix dépasse\nbelow = si prix descend sous"
        )
        return
    symbol    = resolve(parts[1].upper())
    direction = parts[3].lower()
    if direction not in ("above", "below"):
        bot.reply_to(message, "❓ Utilise 'above' ou 'below'")
        return
    try:
        target = float(parts[2])
    except:
        bot.reply_to(message, "❓ Prix invalide.")
        return
    add_alert(message.from_user.id, symbol, target, direction)
    dir_txt = "dépasse" if direction == "above" else "descend sous"
    label = {**CRYPTO_ASSETS, **YAHOO_ASSETS}.get(symbol, symbol)
    bot.reply_to(message,
        f"✅ Alerte créée !\n*{label}* {dir_txt} *${target:,.2f}*",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=["mesalertes"])
def cmd_mes_alertes(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔ Accès non autorisé.")
        return
    active = [a for a in get_user_alerts(message.from_user.id) if a.get("active")]
    if not active:
        bot.reply_to(message, "📋 Aucune alerte active.")
        return
    lines = ["📋 *Tes alertes actives :*\n"]
    for i, a in enumerate(active, 1):
        lines.append(f"{i}. *{a['symbol']}* {'>' if a['direction']=='above' else '<'} ${a['target']:,.2f}")
    bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")

@bot.message_handler(commands=["supprimeralertes"])
def cmd_suppr(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔ Accès non autorisé.")
        return
    clear_user_alerts(message.from_user.id)
    bot.reply_to(message, "🗑️ Alertes supprimées.")

@bot.message_handler(commands=["myid"])
def cmd_myid(message):
    bot.reply_to(message, f"🪪 Ton ID : `{message.from_user.id}`", parse_mode="Markdown")

@bot.message_handler(commands=["adduser"])
def cmd_adduser(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Admin seulement.")
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        bot.reply_to(message, "Usage : /adduser ID")
        return
    uid = int(parts[1])
    users = load_users()
    if uid in users:
        bot.reply_to(message, "ℹ️ Déjà autorisé.")
        return
    users.append(uid)
    save_users(users)
    bot.reply_to(message, f"✅ {uid} ajouté.")
    try:
        bot.send_message(uid, "✅ *Accès accordé !* Envoie /start", parse_mode="Markdown")
    except:
        pass

@bot.message_handler(commands=["removeuser"])
def cmd_removeuser(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Admin seulement.")
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        bot.reply_to(message, "Usage : /removeuser ID")
        return
    uid = int(parts[1])
    users = load_users()
    if uid not in users:
        bot.reply_to(message, "ℹ️ Pas dans la liste.")
        return
    users.remove(uid)
    save_users(users)
    bot.reply_to(message, f"🗑️ {uid} retiré.")

@bot.message_handler(commands=["listusers"])
def cmd_listusers(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Admin seulement.")
        return
    users = load_users()
    if not users:
        bot.reply_to(message, "📋 Aucun utilisateur.")
        return
    bot.reply_to(message,
        f"📋 *{len(users)} utilisateur(s) :*\n\n" + "\n".join(f"• {u}" for u in users),
        parse_mode="Markdown"
    )

@bot.message_handler(commands=["rapport"])
def cmd_rapport(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Admin seulement.")
        return
    bot.reply_to(message, "📤 Envoi du rapport...")
    send_daily_report()
    bot.reply_to(message, "✅ Rapport envoyé.")

@bot.message_handler(func=lambda m: True)
def handle_unknown(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔ Accès non autorisé.")
        return
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
    print(f"  Crypto  : BTC ETH SOL XRP BNB")
    print(f"  Actions : AAPL TSLA NVDA + CAC40")
    print(f"  Rapport : {REPORT_HOUR}")
    print("=" * 50)
    threading.Thread(target=run_scheduler, daemon=True).start()
    bot.infinity_polling()
