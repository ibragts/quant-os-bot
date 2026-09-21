import os
import sys
import time
import json
import requests
import datetime
import threading
import pandas as pd
import numpy as np
import yfinance as yf
import matplotlib.pyplot as plt

# إصلاح مشكلة ترميز النصوص العربية
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

plt.rcParams['font.sans-serif'] = ['Segoe UI', 'Arial', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# ==========================================
# إعدادات النظام والبوت وإدارة المخاطر
# ==========================================
TELEGRAM_TOKEN = "8649936966:AAEFSbOMQp-1DahaJUJYGm9rAubfkJ9uWfw"
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
alerted_cache = {}

# ==========================================
# دوال التخزين والتعلم الآلي المبسط
# ==========================================
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {"trades": [], "z_threshold": 2.5, "last_eval_date": ""}

def save_history(history_data):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history_data, f, ensure_ascii=False, indent=4)

def evaluate_and_adapt():
    history = load_history()
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    
    if history.get("last_eval_date") == today_str:
        return history.get("z_threshold", 2.5) # تم التقييم اليوم مسبقاً
        
    trades = history.get("trades", [])
    pending_trades = [t for t in trades if t["status"] == "pending"]
    
    if not pending_trades:
        return history.get("z_threshold", 2.5)

    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🧠 جاري تقييم أداء الصفقات السابقة للتعلم الذاتي...")
    
    wins = 0
    losses = 0
    evaluated_count = 0
    
    for trade in pending_trades:
        try:
            tk = yf.Ticker(trade["ticker"])
            df = tk.history(start=trade["date"])
            if df.empty or len(df) < 2:
                continue
                
            max_high = df['High'].max()
            min_low = df['Low'].min()
            
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
        
        # التكيف الذاتي (Self-Correction)
        old_threshold = current_threshold
        if win_rate < 50.0:
            current_threshold = min(4.0, current_threshold + 0.1) # كن أكثر صرامة
            adapt_msg = "الرصد أصبح أكثر صرامة وحذراً 🛡️"
        elif win_rate > 65.0:
            current_threshold = max(2.0, current_threshold - 0.1) # اقتنص فرص أكثر
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
# دوال التليجرام والشارتات
# ==========================================
def send_telegram_message(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=10)
    except:
        pass

def send_telegram_photo(photo_path, caption):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        with open(photo_path, 'rb') as photo:
            payload = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"}
            requests.post(url, data=payload, files={"photo": photo}, timeout=15)
    except:
        pass

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
    except:
        return None

def check_market_sentiment(is_saudi=False):
    try:
        market_ticker = "^TASI.SR" if is_saudi else "SPY"
        tk = yf.Ticker(market_ticker)
        df = tk.history(period="1mo")
        if not df.empty:
            ma20 = df['Close'].rolling(20).mean().iloc[-1]
            current_price = df['Close'].iloc[-1]
            return "🟢 صاعد (إيجابي)" if current_price >= ma20 else "🔴 هابط (حذر مطلوب)"
    except:
        pass
    return "⚪ مستقر"

def calculate_position_sizing(close_price, stop_loss):
    risk_per_share = abs(close_price - stop_loss)
    if risk_per_share == 0: return 0, 0
    shares = int((PORTFOLIO_CAPITAL * RISK_PER_TRADE_PCT) / risk_per_share)
    return shares, shares * close_price

def analyze_single_ticker(ticker_symbol):
    ticker_symbol = ticker_symbol.upper().strip()
    if ticker_symbol in SMART_ALIASES: ticker_symbol = SMART_ALIASES[ticker_symbol]
    if ticker_symbol.isdigit(): ticker_symbol = f"{ticker_symbol}.SR"

    try:
        tk = yf.Ticker(ticker_symbol)
        df = tk.history(period="3mo")
        if df.empty or len(df) < 20:
            return f"⚠️ <b>تعذر جلب البيانات لـ:</b> <code>{ticker_symbol}</code>", None, None

        close_price = float(df['Close'].iloc[-1])
        df['Vol_Mean'] = df['Volume'].rolling(20).mean()
        df['Vol_Std'] = df['Volume'].rolling(20).std()
        df['Z_Score'] = (df['Volume'] - df['Vol_Mean']) / df['Vol_Std']

        df['High-Low'] = df['High'] - df['Low']
        df['High-Close'] = np.abs(df['High'] - df['Close'].shift(1))
        df['Low-Close'] = np.abs(df['Low'] - df['Close'].shift(1))
        df['ATR'] = df[['High-Low', 'High-Close', 'Low-Close']].max(axis=1).ewm(span=14, adjust=False).mean()
        df['MA20'] = df['Close'].rolling(20).mean()

        latest = df.iloc[-1]
        z_score = float(latest['Z_Score']) if not np.isnan(latest['Z_Score']) else 0.0
        atr = float(latest['ATR']) if not np.isnan(latest['ATR']) else (close_price * 0.02)
        
        recent_low = float(df['Low'].tail(20).min())
        atr_stop = close_price - (atr * 2.0)
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
            f"📌 <b>السهم:</b> <code>{ticker_symbol}</code>\n"
            f"💰 <b>السعر:</b> {currency} {close_price:.2f}\n"
            f"📊 <b>(Z-Score):</b> {z_score:.2f}\n"
            f"🛑 <b>وقف الخسارة:</b> {currency} {stop_loss:.2f}\n"
            f"🎯 <b>الهدف (1:2):</b> {currency} {take_profit:.2f}\n"
            f"⚖️ <b>الكمية المقترحة:</b> {shares} سهم\n"
            f"🌐 <b>اتجاه السوق:</b> {check_market_sentiment(is_saudi)}"
        )
        return msg, chart_path, trade_data
    except Exception as e:
        return f"⚠️ خطأ أثناء تحليل <code>{ticker_symbol}</code>: {e}", None, None

def run_realtime_scanner():
    global alerted_cache
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    
    # 1. تحديث التقييم الذاتي أولاً
    current_z_threshold = evaluate_and_adapt()
    
    if "current_date" not in alerted_cache or alerted_cache["current_date"] != today_str:
        alerted_cache = {"current_date": today_str}

    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🔍 فحص السوق (الحد المطلوب للسيولة Z-Score: {current_z_threshold})...")

    history = load_history()

    for sector_name, tickers in SECTORS_WATCHLIST.items():
        for ticker in tickers:
            if ticker in alerted_cache: continue
            try:
                tk = yf.Ticker(ticker)
                df = tk.history(period="1mo")
                if df.empty or len(df) < 20: continue
                
                df['Vol_Mean'] = df['Volume'].rolling(20).mean()
                df['Vol_Std'] = df['Volume'].rolling(20).std()
                latest = df.iloc[-1]
                z_score = float((latest['Volume'] - latest['Vol_Mean']) / latest['Vol_Std'])
                
                # استخدام معامل السيولة الديناميكي الجديد
                if z_score >= current_z_threshold and latest['Close'] >= latest['Open']:
                    msg, chart_path, trade_data = analyze_single_ticker(ticker)
                    if chart_path:
                        send_telegram_photo(chart_path, msg)
                        os.remove(chart_path)
                    else:
                        send_telegram_message(msg)
                    
                    # حفظ الصفقة لتقييمها لاحقاً
                    if trade_data:
                        history["trades"].append(trade_data)
                        save_history(history)
                        
                    alerted_cache[ticker] = True
                    time.sleep(3)
            except:
                pass

def background_scheduler():
    while True:
        run_realtime_scanner()
        time.sleep(900) # فحص كل 15 دقيقة

def listen_for_commands():
    print("🤖 نظام التنبيهات الفورية Quant OS V2.0 يعمل مع التقييم الذاتي...")
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=30"
            response = requests.get(url, timeout=35)
            if response.status_code == 200:
                for update in response.json().get("result", []):
                    offset = update["update_id"] + 1
                    text = update.get("message", {}).get("text", "").strip()
                    chat_id = str(update.get("message", {}).get("chat", {}).get("id", ""))
                    
                    if chat_id == TELEGRAM_CHAT_ID and text:
                        if text.lower() in ["/scan", "فحص"]:
                            send_telegram_message("⏳ <b>جاري ফحص السوق وتقييم الأداء...</b>")
                            run_realtime_scanner()
                        else:
                            msg, chart_path, _ = analyze_single_ticker(text)
                            if chart_path:
                                send_telegram_photo(chart_path, msg)
                                os.remove(chart_path)
                            else:
                                send_telegram_message(msg)
        except:
            time.sleep(5)
        time.sleep(2)

if __name__ == "__main__":
    threading.Thread(target=background_scheduler, daemon=True).start()
    listen_for_commands()
