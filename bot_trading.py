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
    "bitcoin":     "₿ BTC (Bitcoin)",
    "ethereum":    "Ξ ETH (Ethereum)",
    "solana":      "◎ SOL (Solana)",
    "ripple":      "✦ XRP (Ripple)",
    "binancecoin": "◆ BNB (Binance)",
}

# Alias courts → id CoinGecko
CRYPTO_MAP = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "ripple",
    "BNB": "binancecoin",
}

# ── Actifs Yahoo Finance (actions + indices) ───────────────
YAHOO_ASSETS = {
    "AAPL":  "🍎 Apple",
    "TSLA":  "🚗 Tesla",
    "NVDA":  "🖥️ NVIDIA",
    "^FCHI": "🇫🇷 CAC 40",
}

# Alias actions
ALIAS = {
    "CAC":    "^FCHI",
    "CAC40":  "^FCHI",
    "APPLE":  "AAPL",
    "TESLA":  "TSLA",
    "NVIDIA": "NVDA",
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
#  DONNÉES DE MARCHÉ  — CORRIGÉES
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
    """
    Récupère les données Yahoo Finance.
    CORRECTIF : on utilise dropna() pour éviter les lignes vides
    dues aux weekends/jours fériés, et on prend period="10d"
    pour avoir assez de jours ouvrés.
    """
    try:
        hist = yf.Ticker(ticker).history(period="10d", interval="1d")
        if hist.empty:
            print(f"Yahoo ({ticker}) : historique vide")
            return None

        # Supprimer les lignes sans données (weekends, jours fériés)
        hist = hist.dropna(subset=["Close"])

        if len(hist) < 2:
            print(f"Yahoo ({ticker}) : pas assez de jours ouvrés")
            return None

        p_today = float(hist["Close"].iloc[-1])
        p_prev  = float(hist["Close"].iloc[-2])
        # Pour le 5j, on prend le plus ancien point disponible (jusqu'à 5j)
        p_5d    = float(hist["Close"].iloc[max(0, len(hist)-6)])

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

def risque_crypto(c1h, c24h, c7d):
    v = abs(c1h) + abs(c24h) * 0.5 + abs(c7d) * 0.3
    if v >= 8: return "ELEVE"
    if v >= 4: return "MODERE"
    return "FAIBLE"

def risque_stock(c1d, c5d):
    v = abs(c1d) * 0.7 + abs(c5d) * 0.3
    if v >= 4: return "ELEVE"
    if v >= 2: return "MODERE"
    return "FAIBLE"

def entree_crypto(price, c24h):
    return price * 0.98 if c24h >= 0 else price * 1.01

def entree_stock(price, c1d):
    return price * 0.985 if c1d >= 0 else price * 1.005

def objectif_crypto(price, signal):
    if signal == "STRONG_BUY":  return price * 1.15
    if signal == "BUY":         return price * 1.08
    if signal == "STRONG_SELL": return price * 0.88
    if signal == "SELL":        return price * 0.94
    return price * 1.03

def objectif_stock(price, signal):
    if signal == "STRONG_BUY":  return price * 1.12
    if signal == "BUY":         return price * 1.07
    if signal == "STRONG_SELL": return price * 0.90
    if signal == "SELL":        return price * 0.95
    return price * 1.03

def arrow(v):           return "↑" if v >= 0 else "↓"
def fmt(v):             return f"{'+'if v>=0 else ''}{v:.2f}%"
def fmtp(v, idx=False): return f"{v:,.0f} pts" if idx else f"${v:,.2f}"
def pct(a, b):          return ((b - a) / a) * 100


# ════════════════════════════════════════════════════════════
#  FORMATAGE DÉBUTANT — la grosse nouveauté !
# ════════════════════════════════════════════════════════════

SIGNAL_EMOJI = {
    "STRONG_BUY":  "🟢",
    "BUY":         "🟩",
    "NEUTRE":      "🟡",
    "SELL":        "🟥",
    "STRONG_SELL": "🔴",
}

SIGNAL_LABEL = {
    "STRONG_BUY":  "FORT ACHAT",
    "BUY":         "ACHAT",
    "NEUTRE":      "ATTENDRE",
    "SELL":        "VENDRE",
    "STRONG_SELL": "FORT VENDRE",
}

RISQUE_EMOJI = {
    "FAIBLE":  "🟢",
    "MODERE":  "🟡",
    "ELEVE":   "🔴",
}

SIGNAL_EXPLICATION = {
    "STRONG_BUY": (
        "📈 *Que faire ?* Bonne opportunité d'ACHAT.\n"
        "Le marché est en forte hausse. C'est un bon moment pour entrer, "
        "mais ne mets jamais tout ton argent d'un coup."
    ),
    "BUY": (
        "📈 *Que faire ?* Envisage d'ACHETER.\n"
        "La tendance est positive. Tu peux investir une petite partie, "
        "et attendre pour compléter si ça monte encore."
    ),
    "NEUTRE": (
        "⏸️ *Que faire ?* ATTENDS avant d'agir.\n"
        "Le marché hésite. Ni vraiment haussier, ni baissier. "
        "Mieux vaut observer encore quelques jours."
    ),
    "SELL": (
        "📉 *Que faire ?* Envisage de RÉDUIRE ta position.\n"
        "La tendance se retourne. Si tu es déjà investi, "
        "tu peux vendre une partie pour sécuriser tes gains."
    ),
    "STRONG_SELL": (
        "📉 *Que faire ?* ÉVITE d'acheter maintenant.\n"
        "Le marché est en forte baisse. Si tu es investi, "
        "envisage de vendre pour limiter les pertes."
    ),
}

RISQUE_EXPLICATION = {
    "FAIBLE": "🟢 *Risque FAIBLE* — L'actif est stable, peu de fortes variations. Adapté aux débutants.",
    "MODERE": "🟡 *Risque MODÉRÉ* — Des variations notables. N'investis que ce que tu peux te permettre de perdre.",
    "ELEVE":  "🔴 *Risque ÉLEVÉ* — Très volatile ! Ne mets jamais plus de 5% de ton épargne dessus.",
}

def conseil_montant(signal, risque, prix, is_crypto=False):
    """
    Donne un conseil de montant adapté au signal et au risque.
    Basé sur une hypothèse de budget de 1000€ (ajustable).
    """
    # % du budget conseillé selon signal + risque
    table = {
        ("STRONG_BUY",  "FAIBLE"):  (15, 25),
        ("STRONG_BUY",  "MODERE"):  (10, 15),
        ("STRONG_BUY",  "ELEVE"):   (3,  7),
        ("BUY",         "faible"):  (10, 20),
        ("BUY",         "MODERE"):  (5,  10),
        ("BUY",         "ELEVE"):   (2,  5),
        ("NEUTRE",      "FAIBLE"):  (0,  5),
        ("NEUTRE",      "MODERE"):  (0,  3),
        ("NEUTRE",      "ELEVE"):   (0,  0),
        ("SELL",        "FAIBLE"):  (0,  0),
        ("SELL",        "MODERE"):  (0,  0),
        ("SELL",        "ELEVE"):   (0,  0),
        ("STRONG_SELL", "FAIBLE"):  (0,  0),
        ("STRONG_SELL", "MODERE"):  (0,  0),
        ("STRONG_SELL", "ELEVE"):   (0,  0),
    }
    pct_min, pct_max = table.get((signal, risque), (0, 5))

    if pct_min == 0 and pct_max == 0:
        return (
            "💸 *Combien investir ?* 0€ — Ce n'est pas le bon moment.\n"
            "_Attends un meilleur signal avant de te positionner._"
        )

    # Calcul pour 1000€ de budget exemple
    montant_min = pct_min * 10   # 1000 * pct / 100
    montant_max = pct_max * 10

    if is_crypto:
        fractions_min = montant_min / prix
        fractions_max = montant_max / prix
        return (
            f"💸 *Combien investir ?* (pour un budget de 1 000€)\n"
            f"➡️ Entre *{montant_min}€* et *{montant_max}€* (~{pct_min}-{pct_max}% de ton budget)\n"
            f"   soit ≈ {fractions_min:.6f} à {fractions_max:.6f} unités\n"
            f"_📌 Règle d'or : ne mets jamais plus de 5% sur un seul actif risqué !_"
        )
    else:
        nb_min = montant_min / prix if prix > 0 else 0
        nb_max = montant_max / prix if prix > 0 else 0
        return (
            f"💸 *Combien investir ?* (pour un budget de 1 000€)\n"
            f"➡️ Entre *{montant_min}€* et *{montant_max}€* (~{pct_min}-{pct_max}% de ton budget)\n"
            f"   soit ≈ {nb_min:.2f} à {nb_max:.2f} action(s)\n"
            f"_📌 Règle d'or : ne mets jamais plus de 10% sur une seule action !_"
        )

def stop_loss(price, signal, is_crypto=False):
    """Calcule un stop-loss simple pour limiter les pertes."""
    if signal in ("SELL", "STRONG_SELL", "NEUTRE"):
        return None
    pct = 0.07 if is_crypto else 0.05   # -7% crypto, -5% actions
    sl = price * (1 - pct)
    return sl


# ════════════════════════════════════════════════════════════
#  CONSTRUCTION DES MESSAGES
# ════════════════════════════════════════════════════════════

def get_macro_context():
    now = datetime.now().strftime("%d/%m/%Y")
    return (
        f"🌍 *CONTEXTE MACRO — {now}*\n\n"
        f"🏦 *Fed (USA)* — Taux d'intérêt : 4.25–4.50%\n"
        f"  _(Des taux élevés = crédit cher = marchés moins euphoriques)_\n\n"
        f"🏦 *BCE (Europe)* — Taux : 2.65% en baisse progressive\n"
        f"  _(Baisse des taux = argent moins cher = favorable aux marchés)_\n\n"
        f"📊 *Résumé simple* : Contexte globalement favorable aux investissements.\n"
        f"  L'IA (NVIDIA, etc.) continue de tirer les marchés vers le haut.\n"
        f"  Reste prudent : la volatilité peut revenir rapidement."
    )

def format_crypto_detail(cid, d, mode="full"):
    """Formate les données d'une crypto de façon pédagogique."""
    c1h  = d.get("price_change_percentage_1h_in_currency") or 0
    c24h = d.get("price_change_percentage_24h_in_currency") or 0
    c7d  = d.get("price_change_percentage_7d_in_currency") or 0
    price = d["current_price"]
    label = CRYPTO_ASSETS[cid]

    sig   = signal_crypto(c1h, c24h, c7d)
    risq  = risque_crypto(c1h, c24h, c7d)
    obj   = objectif_crypto(price, sig)
    entree = entree_crypto(price, c24h)
    sl    = stop_loss(price, sig, is_crypto=True)

    sig_emoji  = SIGNAL_EMOJI[sig]
    sig_label  = SIGNAL_LABEL[sig]
    risq_emoji = RISQUE_EMOJI[risq]

    if mode == "compact":
        return f"{sig_emoji} *{label}* — ${price:,.2f}  |  {fmt(c24h)} (24h)  →  {sig_label}"

    lines = [
        f"\n{'━'*22}",
        f"🪙 *{label}*",
        f"",
        f"💰 *Prix actuel :* ${price:,.2f}",
        f"",
        f"📊 *Évolution :*",
        f"  • Dernière heure  : {arrow(c1h)} {fmt(c1h)}",
        f"  • Dernières 24h   : {arrow(c24h)} {fmt(c24h)}",
        f"  • Dernière semaine: {arrow(c7d)} {fmt(c7d)}",
        f"  • Haut 24h : ${d.get('high_24h', 0):,.2f}  |  Bas 24h : ${d.get('low_24h', 0):,.2f}",
        f"",
        f"{sig_emoji} *Signal : {sig_label}*",
        f"{SIGNAL_EXPLICATION[sig]}",
        f"",
        f"{RISQUE_EXPLICATION[risq]}",
        f"",
        f"📥 *Prix d'entrée conseillé :* ~${entree:,.2f}",
        f"  _(Attends ce prix pour passer ton ordre d'achat)_",
        f"🏹 *Objectif de prix :* ~${obj:,.2f}  ({pct(price, obj):+.1f}%)",
        f"  _(C'est là que tu peux envisager de revendre pour prendre tes gains)_",
    ]

    if sl:
        lines += [
            f"🛑 *Stop-loss conseillé :* ~${sl:,.2f}  (-7%)",
            f"  _(Si le prix descend jusque là, vends pour limiter ta perte)_",
        ]

    lines += [
        f"",
        conseil_montant(sig, risq, price, is_crypto=True),
    ]

    return "\n".join(lines)

def format_stock_detail(ticker, d, mode="full"):
    """Formate les données d'une action de façon pédagogique."""
    idx   = ticker.startswith("^")
    c1d   = d["change_1d"]
    c5d   = d["change_5d"]
    price = d["price"]
    label = YAHOO_ASSETS.get(ticker, ticker)

    sig   = signal_stock(c1d, c5d)
    risq  = risque_stock(c1d, c5d)
    obj   = objectif_stock(price, sig)
    entree = entree_stock(price, c1d)
    sl    = stop_loss(price, sig, is_crypto=False)

    sig_emoji  = SIGNAL_EMOJI[sig]
    sig_label  = SIGNAL_LABEL[sig]

    if mode == "compact":
        return f"{sig_emoji} *{label}* — {fmtp(price, idx)}  |  {fmt(c1d)} (1j)  →  {sig_label}"

    lines = [
        f"\n{'━'*22}",
        f"📈 *{label}*",
        f"",
        f"💰 *Prix actuel :* {fmtp(price, idx)}",
        f"",
        f"📊 *Évolution :*",
        f"  • Hier           : {arrow(c1d)} {fmt(c1d)}",
        f"  • Semaine        : {arrow(c5d)} {fmt(c5d)}",
        f"  • Haut du jour   : {fmtp(d['high'], idx)}",
        f"  • Bas du jour    : {fmtp(d['low'], idx)}",
        f"",
        f"{sig_emoji} *Signal : {sig_label}*",
        f"{SIGNAL_EXPLICATION[sig]}",
        f"",
        f"{RISQUE_EXPLICATION[risq]}",
        f"",
        f"📥 *Prix d'entrée conseillé :* ~{fmtp(entree, idx)}",
        f"  _(Attends ce prix pour passer ton ordre)_",
        f"🏹 *Objectif de prix :* ~{fmtp(obj, idx)}  ({pct(price, obj):+.1f}%)",
        f"  _(Objectif raisonnable pour prendre tes bénéfices)_",
    ]

    if sl and not idx:
        lines += [
            f"🛑 *Stop-loss conseillé :* ~{fmtp(sl, idx)}  (-5%)",
            f"  _(Si ça descend jusqu'ici, vends pour couper ta perte)_",
        ]

    if not idx:
        lines += [
            f"",
            conseil_montant(sig, risq, price, is_crypto=False),
        ]

    return "\n".join(lines)

def build_market_msg(cp, yp):
    now = datetime.now().strftime("%d/%m/%Y à %Hh%M")
    lines = [
        f"📊 *RAPPORT DE MARCHÉ COMPLET*",
        f"_{now}_",
        f"",
        get_macro_context(),
        f"",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"🪙 *CRYPTOMONNAIES*",
        f"_Ces actifs sont très volatils — idéal pour débuter avec de petites sommes_",
    ]

    for cid in CRYPTO_ASSETS:
        d = cp.get(cid)
        if d:
            lines.append(format_crypto_detail(cid, d, mode="full"))

    lines += [
        f"",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"📈 *ACTIONS & INDICES*",
        f"_Les actions sont plus stables que les cryptos_",
    ]

    for ticker in YAHOO_ASSETS:
        d = yp.get(ticker)
        if d:
            lines.append(format_stock_detail(ticker, d, mode="full"))
        else:
            lines.append(f"\n❌ *{YAHOO_ASSETS[ticker]}* — données indisponibles (marché fermé ?)")

    lines += [
        f"",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"📚 *GUIDE RAPIDE DÉBUTANT*",
        f"",
        f"1️⃣  *Ne jamais investir plus que ce qu'on peut perdre*",
        f"2️⃣  *Diversifier* : ne pas tout mettre sur un seul actif",
        f"3️⃣  *Le stop-loss* protège tes pertes — utilise-le toujours",
        f"4️⃣  *L'objectif* c'est ton prix de vente cible pour gagner",
        f"5️⃣  *Signal NEUTRE* = tu attends, tu n'agis pas",
        f"",
        f"⚠️ _Document informatif uniquement. Pas de conseil en investissement._",
        f"_Source : CoinGecko + Yahoo Finance_",
    ]
    return "\n".join(lines)

def build_signal_msg(cp, yp):
    now = datetime.now().strftime("%Hh%M")
    lines = [
        f"🎯 *SIGNAUX TRADING — {now}*",
        f"_(🟢 Fort achat | 🟩 Achat | 🟡 Attendre | 🟥 Vendre | 🔴 Fort vendre)_",
        f"",
        f"🪙 *Cryptos*",
    ]
    for cid in CRYPTO_ASSETS:
        d = cp.get(cid)
        if d:
            lines.append(format_crypto_detail(cid, d, mode="compact"))

    lines += [f"", f"📈 *Actions & Indices*"]
    for ticker in YAHOO_ASSETS:
        d = yp.get(ticker)
        if d:
            lines.append(format_stock_detail(ticker, d, mode="compact"))
        else:
            lines.append(f"❓ *{YAHOO_ASSETS[ticker]}* — indisponible")

    lines += [
        f"",
        f"_Pour le détail complet : /marche_",
        f"_Pour un actif précis : /prix BTC ou /prix AAPL_",
    ]
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
                        f"Prix actuel : *${price:,.2f}*\n\n"
                        f"👉 Tape /prix {a['symbol']} pour voir l'analyse complète",
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
        "👋 *Bienvenue sur le Bot Trading !*\n\n"
        "━━━━━━━━━━━━━━━━\n"
        "📊 */marche* — Rapport complet avec explications débutant\n"
        "🎯 */signaux* — Vue rapide de tous les signaux\n"
        "━━━━━━━━━━━━━━━━\n"
        "💰 */prix BTC* — Analyse détaillée du Bitcoin\n"
        "💰 */prix AAPL* — Analyse détaillée d'Apple\n"
        "💰 */prix actif* — Liste toutes les cryptos\n"
        "💰 */prix action* — Liste toutes les actions\n"
        "━━━━━━━━━━━━━━━━\n"
        "🔔 */alerte BTC 70000 above* — Alerte si BTC > 70 000$\n"
        "🔔 */alerte AAPL 200 below* — Alerte si AAPL < 200$\n"
        "📋 */mesalertes* — Voir mes alertes actives\n"
        "🗑️ */supprimeralertes* — Supprimer mes alertes\n"
        "━━━━━━━━━━━━━━━━\n"
        "🪪 */myid* — Mon ID Telegram\n\n"
        "💡 _Conseil : commence par /signaux pour un aperçu rapide,\n"
        "puis /prix BTC pour le détail complet !_",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=["marche"])
def cmd_marche(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔ Accès non autorisé.")
        return
    bot.reply_to(message, "⏳ Récupération des données en cours...\n_Ça peut prendre 5-10 secondes_")
    cp = get_crypto_prices()
    yp = {t: get_yahoo_price(t) for t in YAHOO_ASSETS}
    yp = {k: v for k, v in yp.items() if v}
    if not cp and not yp:
        bot.reply_to(message, "❌ Erreur API. Réessaie dans quelques minutes.")
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

    if keyword == "ACTIF":
        lines = ["🪙 *Cryptos disponibles :*\n"]
        for short, cid in CRYPTO_MAP.items():
            label = CRYPTO_ASSETS.get(cid, cid)
            lines.append(f"• /prix {short} — {label}")
        lines.append("\n_Exemple : /prix BTC_")
        bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")
        return

    if keyword == "ACTION":
        lines = ["📈 *Actions & Indices disponibles :*\n"]
        for ticker, label in YAHOO_ASSETS.items():
            display = "CAC" if ticker == "^FCHI" else ticker
            lines.append(f"• /prix {display} — {label}")
        lines.append("\n_Exemple : /prix AAPL_")
        bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")
        return

    symbol = resolve(keyword)

    # Crypto
    if symbol in CRYPTO_ASSETS:
        bot.reply_to(message, "⏳ Récupération...")
        d = get_crypto_prices().get(symbol)
        if not d:
            bot.reply_to(message, "❌ Données indisponibles.")
            return
        bot.reply_to(message, format_crypto_detail(symbol, d, mode="full"), parse_mode="Markdown")
        return

    # Action / indice
    if symbol in YAHOO_ASSETS or symbol.startswith("^"):
        bot.reply_to(message, "⏳ Récupération...")
        d = get_yahoo_price(symbol)
        if not d:
            bot.reply_to(message,
                "❌ Données indisponibles.\n"
                "_Les marchés actions sont fermés le week-end et la nuit.\n"
                "Réessaie un jour de semaine entre 15h30 et 22h (heure française)._"
            )
            return
        bot.reply_to(message, format_stock_detail(symbol, d, mode="full"), parse_mode="Markdown")
        return

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
            "Exemples :\n"
            "/alerte BTC 70000 above  _(alerte si BTC dépasse 70 000$)_\n"
            "/alerte AAPL 200 below   _(alerte si Apple descend sous 200$)_\n\n"
            "above = préviens si le prix MONTE au-dessus\n"
            "below = préviens si le prix DESCEND en-dessous",
            parse_mode="Markdown"
        )
        return
    symbol    = resolve(parts[1].upper())
    direction = parts[3].lower()
    if direction not in ("above", "below"):
        bot.reply_to(message, "❓ Utilise 'above' (au-dessus) ou 'below' (en-dessous)")
        return
    try:
        target = float(parts[2])
    except:
        bot.reply_to(message, "❓ Prix invalide. Exemple : /alerte BTC 70000 above")
        return
    add_alert(message.from_user.id, symbol, target, direction)
    dir_txt = "dépasse" if direction == "above" else "descend sous"
    label = {**CRYPTO_ASSETS, **YAHOO_ASSETS}.get(symbol, symbol)
    bot.reply_to(message,
        f"✅ *Alerte créée !*\n\n"
        f"Je te préviens dès que *{label}*\n{dir_txt} *${target:,.2f}*\n\n"
        f"_Pour voir tes alertes : /mesalertes_",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=["mesalertes"])
def cmd_mes_alertes(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔ Accès non autorisé.")
        return
    active = [a for a in get_user_alerts(message.from_user.id) if a.get("active")]
    if not active:
        bot.reply_to(message,
            "📋 Aucune alerte active.\n\n"
            "_Pour créer une alerte : /alerte BTC 70000 above_"
        )
        return
    lines = [f"📋 *Tes {len(active)} alerte(s) active(s) :*\n"]
    for i, a in enumerate(active, 1):
        direction_txt = "au-dessus de" if a["direction"] == "above" else "en-dessous de"
        lines.append(f"{i}. *{a['symbol']}* — si prix passe {direction_txt} *${a['target']:,.2f}*")
    lines.append(f"\n_Pour supprimer toutes les alertes : /supprimeralertes_")
    bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")

@bot.message_handler(commands=["supprimeralertes"])
def cmd_suppr(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔ Accès non autorisé.")
        return
    clear_user_alerts(message.from_user.id)
    bot.reply_to(message, "🗑️ Toutes tes alertes ont été supprimées.")

@bot.message_handler(commands=["myid"])
def cmd_myid(message):
    bot.reply_to(message,
        f"🪪 *Ton ID Telegram :* `{message.from_user.id}`\n\n"
        f"_Donne cet ID à l'admin pour obtenir l'accès._",
        parse_mode="Markdown"
    )

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
        bot.reply_to(message, "ℹ️ Cet utilisateur est déjà autorisé.")
        return
    users.append(uid)
    save_users(users)
    bot.reply_to(message, f"✅ Utilisateur {uid} ajouté.")
    try:
        bot.send_message(uid,
            "✅ *Accès accordé !*\n\n"
            "Envoie /start pour voir toutes les commandes disponibles.",
            parse_mode="Markdown"
        )
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
        bot.reply_to(message, "ℹ️ Cet ID n'est pas dans la liste.")
        return
    users.remove(uid)
    save_users(users)
    bot.reply_to(message, f"🗑️ Utilisateur {uid} retiré.")

@bot.message_handler(commands=["listusers"])
def cmd_listusers(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Admin seulement.")
        return
    users = load_users()
    if not users:
        bot.reply_to(message, "📋 Aucun utilisateur autorisé pour l'instant.")
        return
    bot.reply_to(message,
        f"📋 *{len(users)} utilisateur(s) autorisé(s) :*\n\n" + "\n".join(f"• `{u}`" for u in users),
        parse_mode="Markdown"
    )

@bot.message_handler(commands=["rapport"])
def cmd_rapport(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Admin seulement.")
        return
    bot.reply_to(message, "📤 Envoi du rapport en cours...")
    send_daily_report()
    bot.reply_to(message, "✅ Rapport envoyé à tous les utilisateurs.")

@bot.message_handler(func=lambda m: True)
def handle_unknown(message):
    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "⛔ Accès non autorisé.")
        return
    bot.reply_to(message,
        "❓ Je ne reconnais pas cette commande.\n\n"
        "Envoie /help pour voir toutes les commandes disponibles."
    )


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
