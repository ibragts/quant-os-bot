import os
import sys
import time
import json
import requests
import datetime
import pandas as pd
import numpy as np
import yfinance as yf
import matplotlib.pyplot as plt

# إصلاح ترميز النصوص العربية عند التشغيل
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

plt.rcParams['font.sans-serif'] = ['Segoe UI', 'Arial', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# ==========================================
# إعدادات النظام والبوت وإدارة المخاطر
# ==========================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = "901311116"

PORTFOLIO_CAPITAL = 100000
RISK_PER_TRADE_PCT = 0.01

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

HISTORY_FILE = "trade_history.json"

# ==========================================
# دالة جلب البيانات المباشرة (تتخطى الحظر)
# ==========================================
def get_stock_data(ticker_symbol):
    """جلب مباشر لبيانات السهم لتفادي حظر السيرفرات ومشاكل yfinance"""
    ticker_symbol = ticker_symbol.upper().strip()
    
    # محاولة 1: طلب مباشر لـ Yahoo API بـ User-Agent متصفح
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker_symbol}?range=6mo&interval=1d"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            json_data = resp.json()
            result = json_data.get('chart', {}).get('result', [])
            if result and len(result) > 0:
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
    except Exception:
        pass

    # محاولة 2: مكتبة yfinance كخيار احتياطي
    try:
        df = yf.download(ticker_symbol, period="6mo", progress=False, auto_adjust=True)
        if df is not None and not df.empty and len(df) >= 20:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            return df
    except Exception:
        pass

    return pd.DataFrame()

# ==========================================
# دوال التخزين والتعلم الآلي والتقييم الذاتي
# ==========================================
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"trades": [], "z_threshold": 2.5, "last_eval_date": ""}

def save_history(history_data):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history_data, f, ensure_ascii=False, indent=4)

def evaluate_and_adapt():
    history = load_history()
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    
    if history.get("last_eval_date") == today_str:
        return history.get("z_threshold", 2.5)
        
    trades = history.get("trades", [])
    pending_trades = [t for t in trades if t.get("status") == "pending"]
    
    if not pending_trades:
        return history.get("z_threshold", 2.5)

    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🧠 جاري تقييم أداء الصفقات السابقة للتعلم الذاتي...")
    
    wins = 0
    losses = 0
    evaluated_count = 0
    
    for trade in pending_trades:
        try:
            df = get_stock_data(trade["ticker"])
            if df.empty or len(df) < 2:
                continue
                
            max_high = float(df['High'].max())
            min_low = float(df['Low'].min())
            
            if max_high >= trade["tp"]:
                trade["status"] = "win"
                wins += 1
                evaluated_count += 1
            elif min_low <= trade["sl"]:
                trade["status"] = "loss"
                losses += 1
                evaluated_count += 1
        except Exception:
            pass

    if evaluated_count > 0:
        win_rate = (wins / evaluated_count) * 100
        current_threshold = history.get("z_threshold", 2.5)
        
        if win_rate < 50.0:
            current_threshold = min(4.0, current_threshold + 0.1)
            adapt_msg = "الرصد أصبح أكثر صرامة وحذراً 🛡️"
        elif win_rate > 65.0:
            current_threshold = max(2.0, current_threshold - 0.1)
            adapt_msg = "الرصد أصبح مرناً لاقتناص فرص أكثر 🏹"
        else:
            adapt_msg = "تم الحفاظ على مستوى الرصد الحالي ⚖️"
            
        history["z_threshold"] = round(current_threshold, 2)
        history["last_eval_date"] = today_str
        save_history(history)
        
        report_msg = (
            f"🧠 <b>تقرير التقييم الذاتي لـ Quant OS</b>\n"
            f"───────────────────\n"
            f"📊 تم تقييم <b>{evaluated_count}</b> صفقات سابقة\n"
            f"✅ <b>الصفقات الناجحة:</b> {wins}\n"
            f"❌ <b>الصفقات الخاسرة:</b> {losses}\n"
            f"🎯 <b>نسبة النجاح الفعلية:</b> {win_rate:.1f}%\n"
            f"⚙️ <b>رد فعل النظام الآلي:</b>\n"
            f"• {adapt_msg}\n"
            f"• معامل السيولة الجديد المطلوب: <b>{history['z_threshold']}</b>"
        )
        send_telegram_message(report_msg)
        
    return history.get("z_threshold", 2.5)

# ==========================================
# دوال التواصل والشارتات
# ==========================================
def send_telegram_message(message):
    if not TELEGRAM_TOKEN:
        print("⚠️ TELEGRAM_BOT_TOKEN غير معرّف!")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"خطأ في إرسال رسالة تليجرام: {e}")

def send_telegram_photo(photo_path, caption):
    if not TELEGRAM_TOKEN:
        print("⚠️ TELEGRAM_BOT_TOKEN غير معرّف!")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        with open(photo_path, 'rb') as photo:
            payload = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"}
            requests.post(url, data=payload, files={"photo": photo}, timeout=15)
    except Exception as e:
        print(f"خطأ في إرسال الصورة لتليجرام: {e}")

def generate_stock_chart(df, ticker_symbol, stop_loss, take_profit):
    try:
        plt.figure(figsize=(10, 5))
        plt.plot(df.index, df['Close'], label='Close Price', color='#1f77b4', linewidth=2)
        if 'MA20' in df.columns:
            plt.plot(df.index, df['MA20'], label='MA20', color='#ff7f0e', linestyle='--', alpha=0.8)
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
    except Exception:
        return None

def check_market_sentiment(is_saudi=False):
    try:
        market_ticker = "^TASI.SR" if is_saudi else "SPY"
        df = get_stock_data(market_ticker)
        if not df.empty:
            ma20 = df['Close'].rolling(20).mean().iloc[-1]
            current_price = df['Close'].iloc[-1]
            return "🟢 صاعد (إيجابي)" if current_price >= ma20 else "🔴 هابط (حذر مطلوب)"
    except Exception:
        pass
    return "⚪ مستقر"

def calculate_position_sizing(close_price, stop_loss):
    risk_per_share = abs(close_price - stop_loss)
    if risk_per_share == 0: return 0, 0
    shares = int((PORTFOLIO_CAPITAL * RISK_PER_TRADE_PCT) / risk_per_share)
    return shares, shares * close_price

# ==========================================
# دالة تحليل سهم منفرد والرد التفاعلي
# ==========================================
def analyze_single_ticker(ticker_symbol):
    ticker_symbol = str(ticker_symbol).upper().strip()
    if ticker_symbol in SMART_ALIASES: 
        ticker_symbol = SMART_ALIASES[ticker_symbol]
    if ticker_symbol.isdigit(): 
        ticker_symbol = f"{ticker_symbol}.SR"

    try:
        df = get_stock_data(ticker_symbol)

        if df.empty or len(df) < 20:
            return f"⚠️ <b>تعذر جلب بيانات كافية للسهم:</b> <code>\u200e{ticker_symbol}</code>", None, None

        close_price = float(df['Close'].iloc[-1])
        
        df['Vol_Mean'] = df['Volume'].rolling(20).mean()
        df['Vol_Std'] = df['Volume'].rolling(20).std()
        df['Z_Score'] = (df['Volume'] - df['Vol_Mean']) / df['Vol_Std']

        df['High-Low'] = df['High'] - df['Low']
        df['High-Close'] = np.abs(df['High'] - df['Close'].shift(1))
        df['Low-Close'] = np.abs(df['Low'] - df['Close'].shift(1))
        df['ATR'] = df[['High-Low', 'High-Close', 'Low-Close']].max(axis=1).ewm(span=14, adjust=False).mean()
        df['MA20'] = df['Close'].rolling(20).mean()

        latest_z = float(df['Z_Score'].iloc[-1]) if not np.isnan(df['Z_Score'].iloc[-1]) else 0.0
        latest_atr = float(df['ATR'].iloc[-1]) if not np.isnan(df['ATR'].iloc[-1]) else (close_price * 0.02)
        
        recent_low = float(df['Low'].tail(20).min())
        atr_stop = close_price - (latest_atr * 2.0)
        stop_loss = min(recent_low, atr_stop)
        take_profit = close_price + ((close_price - stop_loss) * 2.0)
        
        shares, pos_value = calculate_position_sizing(close_price, stop_loss)
        is_saudi = ".SR" in ticker_symbol
        currency = "SAR" if is_saudi else "$"

        chart_path = generate_stock_chart(df, ticker_symbol, stop_loss, take_profit)
        
        trade_data = {
            "ticker": ticker_symbol, "date": datetime.datetime.now().strftime("%Y-%m-%d"),
            "entry": close_price, "tp": take_profit, "sl": stop_loss, "status": "pending"
        }

        msg = (
            f"🚨 <b>تنبيه سيولة فوري | Quant OS V2.0</b>\n"
            f"───────────────────\n"
            f"📌 <b>السهم:</b> <code>\u200e{ticker_symbol}</code>\n"
            f"💰 <b>السعر:</b> {currency} {close_price:.2f}\n"
            f"📊 <b>(Z-Score):</b> {latest_z:.2f}\n"
            f"🛑 <b>وقف الخسارة:</b> {currency} {stop_loss:.2f}\n"
            f"🎯 <b>الهدف (1:2):</b> {currency} {take_profit:.2f}\n"
            f"⚖️ <b>الكمية المقترحة:</b> {shares} سهم\n"
            f"🌐 <b>اتجاه السوق:</b> {check_market_sentiment(is_saudi)}"
        )
        return msg, chart_path, trade_data
    except Exception as e:
        return f"⚠️ خطأ أثناء تحليل <code>\u200e{ticker_symbol}</code>: {e}", None, None

# ==========================================
# دالة معالجة الأوامر الواردة من تيليجرام
# ==========================================
def process_telegram_updates():
    if not TELEGRAM_TOKEN:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
        resp = requests.get(url, timeout=10).json()
        if not resp.get("ok"):
            return
            
        results = resp.get("result", [])
        if not results:
            return

        last_update_id = 0
        for update in results:
            update_id = update["update_id"]
            if update_id > last_update_id:
                last_update_id = update_id

            message = update.get("message", {})
            text = message.get("text", "").strip()
            chat_id = str(message.get("chat", {}).get("id", ""))

            if chat_id != str(TELEGRAM_CHAT_ID) and TELEGRAM_CHAT_ID != "":
                continue

            if not text:
                continue

            if text in ["/start", "فحص"]:
                send_telegram_message("✅ تم الانتهاء من الفحص اليدوي.")
            elif len(text) <= 12 and not text.startswith("/"):
                symbol = text.upper()
                send_telegram_message(f"⏳ جاري التحليل الفوري وتوليد الشارت للسهم: {symbol}...")
                msg, chart_path, _ = analyze_single_ticker(symbol)
                if chart_path:
                    send_telegram_photo(chart_path, msg)
                    if os.path.exists(chart_path):
                        os.remove(chart_path)
                else:
                    send_telegram_message(msg)

        if last_update_id > 0:
            requests.get(f"{url}?offset={last_update_id + 1}", timeout=5)

    except Exception as e:
        print(f"خطأ أثناء معالجة رسائل تليجرام: {e}")

# ==========================================
# دالة الفحص الدوري الشامل للسوق
# ==========================================
def run_realtime_scanner():
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    current_z_threshold = evaluate_and_adapt()
    
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🔍 بدء فحص السوق (معامل السيولة المطلوبة Z-Score: {current_z_threshold})...")

    history = load_history()
    today_tickers = [t["ticker"] for t in history.get("trades", []) if t.get("date") == today_str]

    for sector_name, tickers in SECTORS_WATCHLIST.items():
        for ticker in tickers:
            if ticker in today_tickers:
                continue
            try:
                df = get_stock_data(ticker)
                if df.empty or len(df) < 20:
                    continue

                vol_series = df['Volume']
                close_series = df['Close']
                open_series = df['Open']

                vol_mean = vol_series.rolling(20).mean()
                vol_std = vol_series.rolling(20).std()
                z_series = (vol_series - vol_mean) / vol_std

                z_score = float(z_series.iloc[-1])
                last_close = float(close_series.iloc[-1])
                last_open = float(open_series.iloc[-1])
                
                if z_score >= current_z_threshold and last_close >= last_open:
                    msg, chart_path, trade_data = analyze_single_ticker(ticker)
                    if chart_path:
                        send_telegram_photo(chart_path, msg)
                        if os.path.exists(chart_path):
                            os.remove(chart_path)
                    else:
                        send_telegram_message(msg)
                    
                    if trade_data:
                        history["trades"].append(trade_data)
                        save_history(history)
                        today_tickers.append(ticker)
                    
                    time.sleep(2)
            except Exception:
                pass

if __name__ == "__main__":
    print("🚀 بدء تنفيذ دورة الفحص والتقييم الذاتي لـ Quant OS V2.0...")
    process_telegram_updates()
    run_realtime_scanner()
    print("✅ اكتمل الفحص والتقييم بنجاح. إنهاء الجلسة لتوفير الموارد.")
