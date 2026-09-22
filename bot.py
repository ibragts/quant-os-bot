import os
import sys
import io
import time
import pickle
import logging
import sqlite3
import datetime
import requests
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yfinance as yf

try:
    from sklearn.linear_model import LogisticRegression
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

plt.rcParams['font.sans-serif'] = ['Segoe UI', 'Arial', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("quant_os.log", encoding="utf-8")]
)
log = logging.getLogger("QuantOS")

# ==========================================
# الإعدادات العامة
# ==========================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "901311116")

PORTFOLIO_CAPITAL_BASE = 100000
RISK_PER_TRADE_PCT = 0.01

TRADE_EXPIRY_DAYS = 20
MAX_CONCURRENT_TRADES = 8
MAX_TRADES_PER_SECTOR = 3

MIN_ML_CONFIDENCE = 0.55
MIN_TRAIN_SAMPLES = 30
RETRAIN_EVERY_N_NEW_TRADES = 5

BACKTEST_THRESHOLDS = [2.0, 2.5, 3.0, 3.5, 4.0]
MIN_BACKTEST_SAMPLES = 15
WEEKLY_TASK_WEEKDAY = 3  # الخميس (0=الاثنين ... 6=الأحد)

DB_FILE = "quant_os.db"
MODEL_PATH = "ml_model.pkl"

SECTORS_WATCHLIST = {
    "🇺🇸 التقنية والنمو الأمريكي": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "NFLX", "AMD", "AVGO"],
    "🇺🇸 المال والأعمال الأمريكي": ["JPM", "BAC", "GS", "MS", "V", "MA", "AXP"],
    "🇸🇦 البنوك والمالية السعودية": ["1120.SR", "1180.SR", "1010.SR", "1020.SR", "1080.SR", "1150.SR"],
    "🇸🇦 الطاقة والبتروكيماويات السعودية": ["2222.SR", "2010.SR", "2310.SR", "2350.SR", "2380.SR"]
}

SMART_ALIASES = {
    "LUCID": "LCID", "APPLE": "AAPL", "TESLA": "TSLA", "MICROSOFT": "MSFT",
    "الراجحي": "1120.SR", "أرامكو": "2222.SR", "سابك": "2010.SR", "الأهلي": "1180.SR"
}

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
})

# ==========================================
# طبقة قاعدة البيانات (SQLite بدل ملف JSON)
# ==========================================
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            sector TEXT,
            date_opened TEXT NOT NULL,
            entry REAL,
            tp REAL,
            sl REAL,
            shares INTEGER,
            z_score REAL,
            atr_pct REAL,
            sentiment_bullish INTEGER,
            status TEXT DEFAULT 'pending',
            date_closed TEXT,
            exit_price REAL,
            pnl REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    return conn

def get_meta(key, default=None):
    conn = get_db()
    cur = conn.execute("SELECT value FROM meta WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else default

def set_meta(key, value):
    conn = get_db()
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value))
    )
    conn.commit()
    conn.close()

def get_z_threshold():
    return float(get_meta("z_threshold", "2.5"))

def insert_trade(ticker, sector, entry, tp, sl, shares, z_score, atr_pct, sentiment_bullish):
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    conn.execute(
        """INSERT INTO trades (ticker, sector, date_opened, entry, tp, sl, shares,
                                z_score, atr_pct, sentiment_bullish, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
        (ticker, sector, today_str, entry, tp, sl, shares, z_score, atr_pct, sentiment_bullish)
    )
    conn.commit()
    conn.close()

def get_open_tickers_today():
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    cur = conn.execute("SELECT ticker FROM trades WHERE date_opened = ?", (today_str,))
    rows = {r[0] for r in cur.fetchall()}
    conn.close()
    return rows

def count_open_trades():
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) FROM trades WHERE status = 'pending'").fetchone()[0]
    conn.close()
    return n

def count_open_trades_sector(sector):
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) FROM trades WHERE status = 'pending' AND sector = ?", (sector,)).fetchone()[0]
    conn.close()
    return n

def get_pending_trades():
    conn = get_db()
    cur = conn.execute("SELECT id, ticker, date_opened, entry, tp, sl, shares FROM trades WHERE status = 'pending'")
    rows = cur.fetchall()
    conn.close()
    cols = ["id", "ticker", "date_opened", "entry", "tp", "sl", "shares"]
    return [dict(zip(cols, r)) for r in rows]

def close_trade(trade_id, status, exit_price, entry, shares):
    pnl = round((exit_price - entry) * shares, 2) if shares else 0.0
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    conn.execute(
        "UPDATE trades SET status = ?, date_closed = ?, exit_price = ?, pnl = ? WHERE id = ?",
        (status, today_str, exit_price, pnl, trade_id)
    )
    conn.commit()
    conn.close()
    return pnl

def get_realized_pnl():
    conn = get_db()
    total = conn.execute("SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE status IN ('win','loss','expired')").fetchone()[0]
    conn.close()
    return float(total or 0.0)

def get_current_capital():
    return PORTFOLIO_CAPITAL_BASE + get_realized_pnl()

def count_closed_trades():
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) FROM trades WHERE status IN ('win','loss')").fetchone()[0]
    conn.close()
    return n

def get_training_rows():
    conn = get_db()
    cur = conn.execute(
        "SELECT z_score, atr_pct, sentiment_bullish, status FROM trades "
        "WHERE status IN ('win','loss') AND z_score IS NOT NULL AND atr_pct IS NOT NULL"
    )
    rows = cur.fetchall()
    conn.close()
    return rows

# ==========================================
# أدوات الشبكة: إعادة محاولة تلقائية عند الفشل
# ==========================================
def http_get(url, retries=2, timeout=10, **kwargs):
    last_err = None
    for attempt in range(retries + 1):
        try:
            return SESSION.get(url, timeout=timeout, **kwargs)
        except requests.exceptions.RequestException as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    log.debug(f"فشلت كل محاولات الاتصال بـ {url}: {last_err}")
    return None

# ==========================================
# جلب بيانات الأسهم (يعيد دائماً أعمدة مفرّدة المستوى)
# ==========================================
def get_stock_data(ticker_symbol):
    ticker_symbol = ticker_symbol.upper().strip()

    # 1. Stooq
    try:
        stooq_symbol = ticker_symbol.lower()
        if stooq_symbol.endswith(".sr"):
            stooq_symbol = stooq_symbol.replace(".sr", ".sa")
        elif not stooq_symbol.endswith(".sa") and "." not in stooq_symbol:
            stooq_symbol = f"{stooq_symbol}.us"

        stooq_url = f"https://stooq.com/q/d/l/?s={stooq_symbol}&i=d"
        resp = http_get(stooq_url)
        if resp is not None and resp.status_code == 200 and "Date" in resp.text:
            df = pd.read_csv(io.StringIO(resp.text))
            if not df.empty and 'Close' in df.columns and len(df) >= 20:
                df['Date'] = pd.to_datetime(df['Date'])
                df = df.sort_values('Date').set_index('Date')
                df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna(subset=['Close'])
                if len(df) >= 20:
                    return df
    except Exception as e:
        log.debug(f"Stooq فشل لـ {ticker_symbol}: {e}")

    # 2. Yahoo Finance Query API v8
    try:
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker_symbol}?range=6mo&interval=1d"
        resp = http_get(url)
        if resp is not None and resp.status_code == 200:
            json_data = resp.json()
            result = json_data.get('chart', {}).get('result', [])
            if result:
                quote = result[0]['indicators']['quote'][0]
                timestamps = result[0]['timestamp']
                df = pd.DataFrame({
                    'Open': quote.get('open', []),
                    'High': quote.get('high', []),
                    'Low': quote.get('low', []),
                    'Close': quote.get('close', []),
                    'Volume': quote.get('volume', [])
                }, index=pd.to_datetime(timestamps, unit='s'))
                df = df.dropna(subset=['Close'])
                if not df.empty and len(df) >= 20:
                    return df
    except Exception as e:
        log.debug(f"Yahoo فشل لـ {ticker_symbol}: {e}")

    # 3. yfinance كخيار احتياطي أخير
    try:
        df = yf.download(ticker_symbol, period="6mo", progress=False, auto_adjust=True)
        if df is not None and not df.empty and len(df) >= 20:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return df
    except Exception as e:
        log.debug(f"yfinance فشل لـ {ticker_symbol}: {e}")

    return pd.DataFrame()

def fetch_all(tickers):
    """جلب متوازٍ لعدة أسهم دفعة واحدة بدل التسلسل - يسرّع الفحص بشكل كبير."""
    results = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(get_stock_data, t): t for t in tickers}
        for future in as_completed(future_map):
            t = future_map[future]
            try:
                results[t] = future.result()
            except Exception as e:
                log.debug(f"فشل جلب {t} أثناء التنفيذ المتوازي: {e}")
                results[t] = pd.DataFrame()
    return results

def build_ticker_sector_map():
    mapping = {}
    for sector, tickers in SECTORS_WATCHLIST.items():
        for t in tickers:
            mapping.setdefault(t, sector)
    return mapping

# ==========================================
# مؤشرات فنية مشتركة
# ==========================================
def compute_z_score(volume_series):
    vol_mean = volume_series.rolling(20).mean()
    vol_std = volume_series.rolling(20).std()
    return (volume_series - vol_mean) / vol_std.replace(0, np.nan)

def compute_atr(df, period=14):
    high, low, close = df['High'], df['Low'], df['Close']
    high_low = high - low
    high_close = np.abs(high - close.shift(1))
    low_close = np.abs(low - close.shift(1))
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def safe_float(value, default=0.0):
    try:
        if pd.isna(value) or not np.isfinite(value):
            return default
    except TypeError:
        pass
    return float(value)

# ==========================================
# إدارة المخاطر
# ==========================================
def risk_check(sector):
    if count_open_trades() >= MAX_CONCURRENT_TRADES:
        return False, f"تم الوصول للحد الأقصى للصفقات المفتوحة ({MAX_CONCURRENT_TRADES})"
    if sector and count_open_trades_sector(sector) >= MAX_TRADES_PER_SECTOR:
        return False, f"تم الوصول للحد الأقصى لصفقات قطاع {sector} ({MAX_TRADES_PER_SECTOR})"
    return True, ""

def calculate_position_sizing(close_price, stop_loss):
    risk_per_share = abs(close_price - stop_loss)
    if risk_per_share == 0:
        return 0, 0
    capital = get_current_capital()
    shares = int((capital * RISK_PER_TRADE_PCT) / risk_per_share)
    return shares, shares * close_price

# ==========================================
# النموذج الذكي: تعلّم آلي فعلي بدل قاعدة ثابتة
# ==========================================
def train_model():
    if not SKLEARN_AVAILABLE:
        return None
    rows = get_training_rows()
    if len(rows) < MIN_TRAIN_SAMPLES:
        return None
    X = np.array([[r[0], r[1], r[2]] for r in rows], dtype=float)
    y = np.array([1 if r[3] == "win" else 0 for r in rows], dtype=int)
    if len(set(y.tolist())) < 2:
        return None
    model = LogisticRegression(max_iter=1000)
    model.fit(X, y)
    try:
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(model, f)
    except Exception as e:
        log.warning(f"تعذر حفظ النموذج: {e}")
    return model

def load_model():
    if not SKLEARN_AVAILABLE or not os.path.exists(MODEL_PATH):
        return None
    try:
        with open(MODEL_PATH, "rb") as f:
            return pickle.load(f)
    except Exception as e:
        log.warning(f"تعذر تحميل النموذج: {e}")
        return None

def predict_win_probability(model, z_score, atr_pct, sentiment_bullish):
    if model is None:
        return None
    try:
        proba = model.predict_proba([[z_score, atr_pct, sentiment_bullish]])[0]
        classes = list(model.classes_)
        if 1 not in classes:
            return None
        return float(proba[classes.index(1)])
    except Exception as e:
        log.debug(f"فشل تنبؤ النموذج: {e}")
        return None

def maybe_retrain_model():
    if not SKLEARN_AVAILABLE:
        return
    total_closed = count_closed_trades()
    last_trained = int(get_meta("last_train_count", "0"))
    if total_closed >= MIN_TRAIN_SAMPLES and (total_closed - last_trained) >= RETRAIN_EVERY_N_NEW_TRADES:
        model = train_model()
        if model is not None:
            set_meta("last_train_count", total_closed)
            log.info(f"🤖 تم إعادة تدريب النموذج الذكي على {total_closed} صفقة مغلقة.")
            send_telegram_message(f"🤖 <b>تحديث النموذج الذكي:</b> تم إعادة التدريب على {total_closed} صفقة مغلقة.")

# ==========================================
# تيليجرام والشارتات
# ==========================================
def send_telegram_message(message):
    if not TELEGRAM_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN غير معرّف! لن يتم إرسال الرسالة.")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        SESSION.post(url, json=payload, timeout=10)
    except Exception as e:
        log.error(f"خطأ في إرسال رسالة تليجرام: {e}")

def send_telegram_photo(photo_path, caption):
    if not TELEGRAM_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN غير معرّف! لن يتم إرسال الصورة.")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        with open(photo_path, 'rb') as photo:
            payload = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"}
            SESSION.post(url, data=payload, files={"photo": photo}, timeout=15)
    except Exception as e:
        log.error(f"خطأ في إرسال الصورة لتليجرام: {e}")

def generate_stock_chart(df, ticker_symbol, stop_loss, take_profit, ma20_series):
    try:
        plt.figure(figsize=(10, 5))
        plt.plot(df.index, df['Close'], label='Close Price', color='#1f77b4', linewidth=2)
        plt.plot(df.index, ma20_series, label='MA20', color='#ff7f0e', linestyle='--', alpha=0.8)
        plt.axhline(y=stop_loss, color='red', linestyle=':', label=f'Stop Loss ({stop_loss:.2f})')
        plt.axhline(y=take_profit, color='green', linestyle=':', label=f'Take Profit ({take_profit:.2f})')
        plt.title(f"Quant OS Alert: {ticker_symbol}", fontsize=14, fontweight='bold', color='#333333')
        plt.legend(loc='upper left')
        plt.grid(True, linestyle='--', alpha=0.5)
        plt.tight_layout()
        chart_path = f"temp_alert_{ticker_symbol.replace('.', '_')}.png"
        plt.savefig(chart_path, dpi=150)
        plt.close()
        return chart_path
    except Exception as e:
        log.error(f"فشل توليد الشارت لـ {ticker_symbol}: {e}")
        plt.close()
        return None

def check_market_sentiment(is_saudi=False):
    try:
        market_ticker = "^TASI.SR" if is_saudi else "SPY"
        df = get_stock_data(market_ticker)
        if not df.empty:
            ma20 = df['Close'].rolling(20).mean().iloc[-1]
            current_price = float(df['Close'].iloc[-1])
            if pd.notna(ma20):
                return "🟢 صاعد (إيجابي)" if current_price >= ma20 else "🔴 هابط (حذر مطلوب)"
    except Exception as e:
        log.debug(f"تعذر تحديد اتجاه السوق: {e}")
    return "⚪ مستقر"

# ==========================================
# تحليل سهم وتوليد إشارة (يدوي أو تلقائي)
# ==========================================
def resolve_ticker(raw_symbol):
    symbol = str(raw_symbol).upper().strip()
    if symbol in SMART_ALIASES:
        symbol = SMART_ALIASES[symbol]
    if symbol.isdigit():
        symbol = f"{symbol}.SR"
    return symbol

def generate_signal(raw_symbol, sector=None, auto_open=False, prefetched_df=None):
    ticker_symbol = resolve_ticker(raw_symbol)
    sector_label = sector or "استعلام يدوي"

    try:
        df = prefetched_df if prefetched_df is not None else get_stock_data(ticker_symbol)

        if df is None or df.empty or len(df) < 20:
            return f"⚠️ <b>تعذر جلب بيانات كافية للسهم:</b> <code>\u200e{ticker_symbol}</code>", None, False

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        close_price = float(df['Close'].iloc[-1])

        z_series = compute_z_score(df['Volume'])
        atr_series = compute_atr(df)
        ma20_series = df['Close'].rolling(20).mean()

        z_score = safe_float(z_series.iloc[-1], 0.0)
        latest_atr = safe_float(atr_series.iloc[-1], close_price * 0.02)
        atr_pct = latest_atr / close_price if close_price else 0.0

        recent_low = float(df['Low'].tail(20).min())
        atr_stop = close_price - (latest_atr * 2.0)
        stop_loss = min(recent_low, atr_stop)
        take_profit = close_price + ((close_price - stop_loss) * 2.0)

        shares, _ = calculate_position_sizing(close_price, stop_loss)
        is_saudi = ".SR" in ticker_symbol
        currency = "SAR" if is_saudi else "$"

        sentiment = check_market_sentiment(is_saudi)
        sentiment_bullish = 1 if "🟢" in sentiment else 0

        model = load_model()
        ml_prob = predict_win_probability(model, z_score, atr_pct, sentiment_bullish)

        if ml_prob is not None:
            ml_line = f"🤖 <b>ثقة النموذج الذكي:</b> {ml_prob * 100:.0f}%"
        else:
            ml_line = f"🤖 <b>النموذج الذكي:</b> غير مدرّب بعد (يحتاج ≥{MIN_TRAIN_SAMPLES} صفقة مغلقة)"

        opened = False
        decision_line = ""
        if auto_open:
            ok, reason = risk_check(sector_label)
            min_conf = MIN_ML_CONFIDENCE + (0.0 if sentiment_bullish else 0.10)
            if ok and ml_prob is not None and ml_prob < min_conf:
                ok, reason = False, f"ثقة النموذج ({ml_prob*100:.0f}%) أقل من الحد المطلوب ({min_conf*100:.0f}%)"
            elif ok and ml_prob is None and not sentiment_bullish:
                ok, reason = False, "السوق هابط ولا يوجد نموذج ذكي مدرّب بعد للحسم"

            if ok:
                insert_trade(ticker_symbol, sector_label, close_price, take_profit, stop_loss,
                              shares, z_score, atr_pct, sentiment_bullish)
                opened = True
                decision_line = "✅ <b>تم تسجيل الصفقة تلقائياً</b>"
            else:
                decision_line = f"⏭️ <b>لم يتم فتح صفقة تلقائية:</b> {reason}"

        chart_path = generate_stock_chart(df, ticker_symbol, stop_loss, take_profit, ma20_series)

        msg_lines = [
            "🚨 <b>تنبيه سيولة | Quant OS V3.0</b>",
            "───────────────────",
            f"📌 <b>السهم:</b> <code>\u200e{ticker_symbol}</code> ({sector_label})",
            f"💰 <b>السعر:</b> {currency} {close_price:.2f}",
            f"📊 <b>Z-Score:</b> {z_score:.2f}",
            f"🛑 <b>وقف الخسارة:</b> {currency} {stop_loss:.2f}",
            f"🎯 <b>الهدف (1:2):</b> {currency} {take_profit:.2f}",
            f"⚖️ <b>الكمية المقترحة:</b> {shares} سهم (رأس المال الحالي {currency}{get_current_capital():,.0f})",
            f"🌐 <b>اتجاه السوق:</b> {sentiment}",
            ml_line,
        ]
        if decision_line:
            msg_lines.append(decision_line)

        return "\n".join(msg_lines), chart_path, opened

    except Exception as e:
        log.error(f"خطأ أثناء تحليل {ticker_symbol}: {e}")
        return f"⚠️ خطأ أثناء تحليل <code>\u200e{ticker_symbol}</code>: {e}", None, False

# ==========================================
# التقييم الذاتي والتكيّف اليومي (مُقيَّد وآمن)
# ==========================================
def evaluate_pending_trades():
    pending = get_pending_trades()
    if not pending:
        return

    log.info(f"🧠 جاري تقييم {len(pending)} صفقة معلّقة...")
    wins, losses, evaluated = 0, 0, 0

    for trade in pending:
        try:
            df = get_stock_data(trade["ticker"])
            if df.empty:
                continue

            trade_date = pd.to_datetime(trade["date_opened"])
            df_after = df[df.index > trade_date]
            if df_after.empty:
                continue

            tp_idx = df_after['High'][df_after['High'] >= trade["tp"]].index.min()
            sl_idx = df_after['Low'][df_after['Low'] <= trade["sl"]].index.min()
            tp_hit = pd.notna(tp_idx)
            sl_hit = pd.notna(sl_idx)

            if tp_hit and (not sl_hit or tp_idx <= sl_idx):
                pnl = close_trade(trade["id"], "win", trade["tp"], trade["entry"], trade["shares"])
                wins += 1
                evaluated += 1
                log.info(f"✅ {trade['ticker']} أغلقت رابحة بربح {pnl:.2f}")
            elif sl_hit:
                pnl = close_trade(trade["id"], "loss", trade["sl"], trade["entry"], trade["shares"])
                losses += 1
                evaluated += 1
                log.info(f"❌ {trade['ticker']} أغلقت خاسرة بخسارة {pnl:.2f}")
            elif len(df_after) >= TRADE_EXPIRY_DAYS:
                last_close = float(df_after['Close'].iloc[-1])
                close_trade(trade["id"], "expired", last_close, trade["entry"], trade["shares"])
                log.info(f"⌛ {trade['ticker']} انتهت صلاحيتها بدون تحقيق الهدف أو الوقف")
        except Exception as e:
            log.debug(f"تعذر تقييم صفقة {trade.get('ticker')}: {e}")

    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    if evaluated >= 5 and get_meta("last_eval_date", "") != today_str:
        win_rate = (wins / evaluated) * 100
        current = get_z_threshold()
        if win_rate < 50.0:
            new_threshold = min(4.0, current + 0.05)
            adapt_msg = "الرصد أصبح أكثر صرامة وحذراً 🛡️"
        elif win_rate > 65.0:
            new_threshold = max(2.0, current - 0.05)
            adapt_msg = "الرصد أصبح مرناً لاقتناص فرص أكثر 🏹"
        else:
            new_threshold = current
            adapt_msg = "تم الحفاظ على مستوى الرصد الحالي ⚖️"

        set_meta("z_threshold", round(new_threshold, 2))
        set_meta("last_eval_date", today_str)

        report_msg = (
            f"🧠 <b>تقرير التقييم الذاتي اليومي</b>\n"
            f"───────────────────\n"
            f"📊 تم تقييم <b>{evaluated}</b> صفقة\n"
            f"✅ رابحة: {wins} | ❌ خاسرة: {losses}\n"
            f"🎯 نسبة النجاح: {win_rate:.1f}%\n"
            f"💵 رأس المال الحالي: {get_current_capital():,.0f}\n"
            f"⚙️ {adapt_msg}\n"
            f"• العتبة الجديدة (Z-Score): <b>{round(new_threshold, 2)}</b>"
        )
        send_telegram_message(report_msg)

    maybe_retrain_model()

# ==========================================
# اختبار رجعي (Backtest) لمعايرة العتبة أسبوعياً
# ==========================================
def backtest_ticker(df, thresholds):
    results = {th: {"wins": 0, "losses": 0} for th in thresholds}
    if df.empty or len(df) < 40:
        return results
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    close, high, low, open_ = df['Close'], df['High'], df['Low'], df['Open']
    z_series = compute_z_score(df['Volume'])
    atr_series = compute_atr(df)

    n = len(df)
    for i in range(20, n - 3):
        zi = z_series.iloc[i]
        if pd.isna(zi) or not np.isfinite(zi):
            continue
        if close.iloc[i] < open_.iloc[i]:
            continue

        atr_i = atr_series.iloc[i]
        if pd.isna(atr_i):
            continue

        entry = float(close.iloc[i])
        recent_low = float(low.iloc[max(0, i - 19):i + 1].min())
        stop = min(recent_low, entry - atr_i * 2.0)
        target = entry + (entry - stop) * 2.0

        future_high = high.iloc[i + 1:]
        future_low = low.iloc[i + 1:]
        tp_idx = future_high[future_high >= target].index.min()
        sl_idx = future_low[future_low <= stop].index.min()
        tp_hit = pd.notna(tp_idx)
        sl_hit = pd.notna(sl_idx)
        if not tp_hit and not sl_hit:
            continue

        outcome_win = tp_hit and (not sl_hit or tp_idx <= sl_idx)
        for th in thresholds:
            if zi >= th:
                if outcome_win:
                    results[th]["wins"] += 1
                else:
                    results[th]["losses"] += 1
    return results

def weekly_recalibration():
    log.info("📐 بدء إعادة المعايرة الأسبوعية عبر الاختبار الرجعي...")
    ticker_sector = build_ticker_sector_map()
    data = fetch_all(list(ticker_sector.keys()))

    agg = {th: {"wins": 0, "losses": 0} for th in BACKTEST_THRESHOLDS}
    for ticker, df in data.items():
        r = backtest_ticker(df, BACKTEST_THRESHOLDS)
        for th in BACKTEST_THRESHOLDS:
            agg[th]["wins"] += r[th]["wins"]
            agg[th]["losses"] += r[th]["losses"]

    best_th, best_rate = None, -1
    lines = []
    for th in BACKTEST_THRESHOLDS:
        total = agg[th]["wins"] + agg[th]["losses"]
        rate = (agg[th]["wins"] / total * 100) if total else 0
        lines.append(f"Z≥{th}: {agg[th]['wins']}/{total} فوز ({rate:.0f}%)")
        if total >= MIN_BACKTEST_SAMPLES and rate > best_rate:
            best_rate, best_th = rate, th

    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    set_meta("last_backtest_date", today_str)

    header = "📐 <b>معايرة أسبوعية (Backtest تاريخي 6 أشهر)</b>\n───────────────────\n"
    if best_th is not None:
        set_meta("z_threshold", best_th)
        msg = header + "\n".join(lines) + f"\n\n✅ تم اعتماد عتبة جديدة: <b>{best_th}</b> (نجاح تاريخي {best_rate:.0f}%)"
    else:
        msg = header + "\n".join(lines) + "\n\n⚠️ لا توجد عينة كافية بعد، تم الإبقاء على العتبة الحالية."
    send_telegram_message(msg)

# ==========================================
# تقرير أداء أسبوعي
# ==========================================
def weekly_report():
    conn = get_db()
    cur = conn.execute(
        "SELECT status, COUNT(*), COALESCE(SUM(pnl),0) FROM trades WHERE status IN ('win','loss','expired') GROUP BY status"
    )
    stats = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

    cur2 = conn.execute(
        "SELECT sector, SUM(CASE WHEN status='win' THEN 1 ELSE 0 END), SUM(CASE WHEN status='loss' THEN 1 ELSE 0 END) "
        "FROM trades WHERE status IN ('win','loss') GROUP BY sector"
    )
    sector_rows = cur2.fetchall()
    conn.close()

    wins, win_pnl = stats.get("win", (0, 0))
    losses, loss_pnl = stats.get("loss", (0, 0))
    expired, expired_pnl = stats.get("expired", (0, 0))
    total_closed = wins + losses
    win_rate = (wins / total_closed * 100) if total_closed else 0
    total_pnl = win_pnl + loss_pnl + expired_pnl

    sector_lines = [f"• {s}: {w} رابحة / {l} خاسرة" for s, w, l in sector_rows] or ["لا توجد صفقات مغلقة بعد"]

    msg = (
        "📅 <b>التقرير الأسبوعي | Quant OS</b>\n"
        "───────────────────\n"
        f"✅ صفقات رابحة: {wins}\n"
        f"❌ صفقات خاسرة: {losses}\n"
        f"⌛ صفقات منتهية: {expired}\n"
        f"🎯 نسبة النجاح: {win_rate:.1f}%\n"
        f"💰 صافي الربح/الخسارة: {total_pnl:,.2f}\n"
        f"💵 رأس المال الحالي: {get_current_capital():,.2f}\n"
        f"📂 <b>الأداء حسب القطاع:</b>\n" + "\n".join(sector_lines)
    )
    send_telegram_message(msg)
    set_meta("last_report_date", datetime.datetime.now().strftime("%Y-%m-%d"))

def maybe_weekly_tasks():
    today = datetime.datetime.now()
    if today.weekday() != WEEKLY_TASK_WEEKDAY:
        return
    today_str = today.strftime("%Y-%m-%d")
    if get_meta("last_report_date", "") != today_str:
        weekly_report()
    if get_meta("last_backtest_date", "") != today_str:
        weekly_recalibration()

# ==========================================
# الفحص الدوري الشامل للسوق
# ==========================================
def run_realtime_scanner():
    current_z_threshold = get_z_threshold()
    log.info(f"🔍 بدء فحص السوق (عتبة Z-Score الحالية: {current_z_threshold})...")

    ticker_sector = build_ticker_sector_map()
    already_open = get_open_tickers_today()
    tickers_to_scan = [t for t in ticker_sector if t not in already_open]

    data = fetch_all(tickers_to_scan)

    for ticker, df in data.items():
        sector = ticker_sector[ticker]
        if df.empty or len(df) < 20:
            continue
        try:
            z_series = compute_z_score(df['Volume'])
            z_score = safe_float(z_series.iloc[-1], 0.0)
            last_close = float(df['Close'].iloc[-1])
            last_open = float(df['Open'].iloc[-1])

            if z_score >= current_z_threshold and last_close >= last_open:
                msg, chart_path, opened = generate_signal(ticker, sector=sector, auto_open=True, prefetched_df=df)
                if chart_path:
                    send_telegram_photo(chart_path, msg)
                    if os.path.exists(chart_path):
                        os.remove(chart_path)
                else:
                    send_telegram_message(msg)
                time.sleep(1)
        except Exception as e:
            log.debug(f"تعذر فحص {ticker}: {e}")

# ==========================================
# معالجة أوامر تيليجرام
# ==========================================
def process_telegram_updates():
    if not TELEGRAM_TOKEN:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        resp = http_get(url)
        if resp is None:
            return
        data = resp.json()
        if not data.get("ok"):
            return

        results = data.get("result", [])
        if not results:
            return

        last_update_id = 0
        for update in results:
            update_id = update["update_id"]
            last_update_id = max(last_update_id, update_id)

            message = update.get("message", {})
            text = message.get("text", "").strip()
            chat_id = str(message.get("chat", {}).get("id", ""))

            if TELEGRAM_CHAT_ID and chat_id != str(TELEGRAM_CHAT_ID):
                continue
            if not text:
                continue

            if text in ["/start", "فحص"]:
                send_telegram_message("⏳ جاري تنفيذ فحص السوق الآن...")
                run_realtime_scanner()
                send_telegram_message("✅ تم الانتهاء من الفحص اليدوي.")
            elif text == "تقرير":
                weekly_report()
            elif text in ["الحالة", "status"]:
                send_telegram_message(
                    "📊 <b>حالة النظام</b>\n───────────────────\n"
                    f"📌 صفقات مفتوحة: {count_open_trades()}/{MAX_CONCURRENT_TRADES}\n"
                    f"💵 رأس المال الحالي: {get_current_capital():,.2f}\n"
                    f"📈 عتبة Z-Score الحالية: {get_z_threshold()}"
                )
            elif len(text) <= 12 and not text.startswith("/"):
                symbol = text.upper()
                send_telegram_message(f"⏳ جاري التحليل الفوري للسهم: {symbol}...")
                msg, chart_path, _ = generate_signal(symbol, auto_open=False)
                if chart_path:
                    send_telegram_photo(chart_path, msg)
                    if os.path.exists(chart_path):
                        os.remove(chart_path)
                else:
                    send_telegram_message(msg)

        if last_update_id > 0:
            http_get(f"{url}?offset={last_update_id + 1}", timeout=5)

    except Exception as e:
        log.error(f"خطأ أثناء معالجة رسائل تليجرام: {e}")

if __name__ == "__main__":
    log.info("🚀 بدء تنفيذ دورة Quant OS V3.0 (ذكاء اصطناعي + إدارة مخاطر)...")
    try:
        get_db().close()  # يضمن إنشاء قاعدة البيانات والجداول قبل أي استخدام
        process_telegram_updates()
        evaluate_pending_trades()
        run_realtime_scanner()
        maybe_weekly_tasks()
        log.info("✅ اكتملت الدورة بنجاح.")
    except Exception as e:
        log.critical(f"❌ فشل تنفيذ الدورة بالكامل: {e}")
        send_telegram_message(f"❌ فشل تشغيل Quant OS في هذه الدورة:\n<code>{e}</code>")
