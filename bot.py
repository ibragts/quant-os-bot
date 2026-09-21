import os
import sys
import time
import requests
import datetime
import threading
import pandas as pd
import numpy as np
import yfinance as yf
import matplotlib.pyplot as plt

# إصلاح مشكلة ترميز النصوص العربية في نافذة الأوامر
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

plt.rcParams['font.sans-serif'] = ['Segoe UI', 'Arial', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# ==========================================
# إعدادات النظام والبوت وإدارة المخاطر
# ==========================================
TELEGRAM_TOKEN = "8649936966:AAEFSbOMQp-1DahaJUJYGm9rAubfkJ9uWfw"
TELEGRAM_CHAT_ID = "901311116"

PORTFOLIO_CAPITAL = 100000  # رأس المال الافتراضي
RISK_PER_TRADE_PCT = 0.01   # نسبة المخاطرة (1%)

SECTORS_WATCHLIST = {
    "🇺🇸 التقنية والنمو الأمريكي": [
        "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "NFLX", "AMD", "AVGO"
    ],
    "🇺🇸 المال والأعمال الأمريكي": [
        "JPM", "BAC", "GS", "MS", "V", "MA", "AXP"
    ],
    "🇺🇸 الطاقة والدفاع الأمريكي": [
        "XOM", "CVX", "COP", "SLB", "LMT", "RTX"
    ],
    "🇸🇦 البنوك والمالية السعودية": [
        "1120.SR", "1180.SR", "1010.SR", "1020.SR", "1080.SR", "1150.SR"
    ],
    "🇸🇦 الطاقة والبتروكيماويات السعودية": [
        "2222.SR", "2010.SR", "2310.SR", "2350.SR", "2380.SR"
    ],
    "🇸🇦 الاتصالات والخدمات السعودية": [
        "7010.SR", "7020.SR", "7200.SR", "2082.SR", "1211.SR"
    ]
}

SMART_ALIASES = {
    "LUCID": "LCID", "APPLE": "AAPL", "TESLA": "TSLA", "MICROSOFT": "MSFT",
    "AMAZON": "AMZN", "NETFLIX": "NFLX", "GOOGLE": "GOOGL", "NVIDIA": "NVIDIA",
    "الراجحي": "1120.SR", "ALRAJHI": "1120.SR", "أرامكو": "2222.SR",
    "ARAMCO": "2222.SR", "سابك": "2010.SR", "SABIC": "2010.SR",
    "الأهلي": "1180.SR", "SNB": "1180.SR", "STC": "7010.SR", "معادن": "1211.SR"
}

# سجل لتتبع الأسهم التي تم إرسال تنبيه عنها اليوم لمنع التكرار
alerted_cache = {}

def send_telegram_message(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"⚠️ Telegram Message Error: {e}")
        return False

def send_telegram_photo(photo_path, caption):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        with open(photo_path, 'rb') as photo:
            payload = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"}
            files = {"photo": photo}
            response = requests.post(url, data=payload, files=files, timeout=15)
            return response.status_code == 200
    except Exception as e:
        print(f"⚠️ Telegram Photo Error: {e}")
        return False

def generate_stock_chart(df, ticker_symbol, stop_loss, take_profit):
    try:
        plt.figure(figsize=(10, 5))
        plt.plot(df.index, df['Close'], label='Close Price', color='#1f77b4', linewidth=2)
        
        if 'MA20' in df.columns:
            plt.plot(df.index, df['MA20'], label='MA20', color='#ff7f0e', linestyle='--', alpha=0.8)
            
        plt.axhline(y=stop_loss, color='red', linestyle=':', label=f'Hybrid Stop Loss ({stop_loss:.2f})')
        plt.axhline(y=take_profit, color='green', linestyle=':', label=f'Take Profit ({take_profit:.2f})')
        
        plt.title(f"Quant OS Real-Time Alert: {ticker_symbol}", fontsize=14, fontweight='bold', color='#333333')
        plt.xlabel("Date", fontsize=10)
        plt.ylabel("Price", fontsize=10)
        plt.legend(loc='upper left')
        plt.grid(True, linestyle='--', alpha=0.5)
        plt.tight_layout()
        
        chart_path = f"temp_alert_{ticker_symbol.replace('.', '_')}.png"
        plt.savefig(chart_path, dpi=150)
        plt.close()
        return chart_path
    except Exception as e:
        print(f"⚠️ خطأ في توليد الشارت: {e}")
        return None

def check_market_sentiment(is_saudi=False):
    try:
        market_ticker = "^TASI.SR" if is_saudi else "SPY"
        tk = yf.Ticker(market_ticker)
        # تم تصحيح الفترة لتكون '1mo' لكي تقبلها مكتبة yfinance بشكل صحيح
        df = tk.history(period="1mo")
        if not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            ma20 = df['Close'].rolling(20).mean().iloc[-1]
            current_price = df['Close'].iloc[-1]
            return "🟢 صاعد (إيجابي)" if current_price >= ma20 else "🔴 هابط (حذر مطلوب)"
    except Exception:
        pass
    return "⚪ مستقر"

def calculate_position_sizing(close_price, stop_loss):
    risk_amount = PORTFOLIO_CAPITAL * RISK_PER_TRADE_PCT
    risk_per_share = abs(close_price - stop_loss)
    if risk_per_share == 0:
        return 0, 0
    shares_count = int(risk_amount / risk_per_share)
    total_position_value = shares_count * close_price
    return shares_count, total_position_value

def analyze_single_ticker(ticker_symbol):
    ticker_symbol = ticker_symbol.upper().strip()
    if ticker_symbol in SMART_ALIASES:
        ticker_symbol = SMART_ALIASES[ticker_symbol]
    if ticker_symbol.isdigit():
        ticker_symbol = f"{ticker_symbol}.SR"

    try:
        tk = yf.Ticker(ticker_symbol)
        df = tk.history(period="3mo")
        if df.empty or len(df) < 20:
            return f"⚠️ <b>تعذر جلب بيانات كافية للسهم:</b> <code>{ticker_symbol}</code>", None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        close_price = float(df['Close'].iloc[-1])
        df['Vol_Mean'] = df['Volume'].rolling(20).mean()
        df['Vol_Std'] = df['Volume'].rolling(20).std()
        df['Z_Score'] = (df['Volume'] - df['Vol_Mean']) / df['Vol_Std']

        df['High-Low'] = df['High'] - df['Low']
        df['High-Close'] = np.abs(df['High'] - df['Close'].shift(1))
        df['Low-Close'] = np.abs(df['Low'] - df['Close'].shift(1))
        df['TR'] = df[['High-Low', 'High-Close', 'Low-Close']].max(axis=1)
        df['ATR'] = df['TR'].ewm(span=14, adjust=False).mean()
        df['MA20'] = df['Close'].rolling(20).mean()

        latest = df.iloc[-1]
        z_score = float(latest['Z_Score']) if not np.isnan(latest['Z_Score']) else 0.0
        atr = float(latest['ATR']) if not np.isnan(latest['ATR']) else (close_price * 0.02)
        
        # --- التحليل الهجين لوقف الخسارة ---
        recent_low = float(df['Low'].tail(20).min())
        atr_stop = close_price - (atr * 2.0)
        stop_loss = min(recent_low, atr_stop)
        
        risk_distance = close_price - stop_loss
        take_profit = close_price + (risk_distance * 2.0)
        
        shares, pos_value = calculate_position_sizing(close_price, stop_loss)
        is_saudi = ".SR" in ticker_symbol
        market_trend = check_market_sentiment(is_saudi)
        currency = "SAR" if is_saudi else "$"

        chart_path = generate_stock_chart(df, ticker_symbol, stop_loss, take_profit)

        msg = (
            f"🚨 <b>تنبيه سيولة فوري | Quant OS V2.0</b>\n"
            f"───────────────────\n"
            f"📌 <b>السهم:</b> <code>{ticker_symbol}</code>\n"
            f"💰 <b>السعر الحالي:</b> {currency} {close_price:.2f}\n"
            f"📊 <b>معامل السيولة (Z-Score):</b> {z_score:.2f}\n"
            f"📉 <b>مؤشر التقلب (ATR):</b> {currency} {atr:.2f}\n"
            f"🛑 <b>وقف الخسارة الهجين:</b> {currency} {stop_loss:.2f}\n"
            f"🎯 <b>الهدف المتوقع (1:2):</b> {currency} {take_profit:.2f}\n"
            f"⚖️ <b>حجم الصفقة المقترح (مخاطر 1%):</b> {shares} سهم ({currency} {pos_value:,.2f})\n"
            f"🌐 <b>اتجاه السوق:</b> {market_trend}"
        )
        return msg, chart_path
    except Exception as e:
        return f"⚠️ خطأ أثناء تحليل السهم <code>{ticker_symbol}</code>: {e}", None

def run_realtime_scanner():
    global alerted_cache
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    
    # تنظيف السجل يومياً لتبدأ التنبيهات لليوم الجديد
    if "current_date" not in alerted_cache or alerted_cache["current_date"] != today_str:
        alerted_cache = {"current_date": today_str}

    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🔍 جاري فحص قائمة المتابعة للسيولة الشاذة...")

    for sector_name, tickers in SECTORS_WATCHLIST.items():
        for ticker in tickers:
            # تخطي السهم إذا تم إرسال تنبيه له اليوم مسبقاً
            if ticker in alerted_cache:
                continue
            try:
                tk = yf.Ticker(ticker)
                df = tk.history(period="1mo")
                if df.empty or len(df) < 20:
                    continue
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)

                df['Vol_Mean'] = df['Volume'].rolling(20).mean()
                df['Vol_Std'] = df['Volume'].rolling(20).std()
                df['Z_Score'] = (df['Volume'] - df['Vol_Mean']) / df['Vol_Std']

                latest = df.iloc[-1]
                z_score = float(latest['Z_Score']) if not np.isnan(latest['Z_Score']) else 0.0
                close_price = float(latest['Close'])
                open_price = float(latest['Open'])

                # شرط إطلاق التنبيه الفوري: سيولة عالية صاعدة
                if z_score >= 2.5 and close_price >= open_price:
                    msg, chart_path = analyze_single_ticker(ticker)
                    if chart_path and os.path.exists(chart_path):
                        send_telegram_photo(chart_path, msg)
                        try:
                            os.remove(chart_path)
                        except:
                            pass
                    else:
                        send_telegram_message(msg)
                    
                    # تسجيل أن السهم تم تنبيهه اليوم
                    alerted_cache[ticker] = True
                    time.sleep(3) # فاصل زمني لتجنب الضغط على الـ API
            except Exception as e:
                print(f"⚠️ خطأ في فحص السهم {ticker}: {e}")

def background_scheduler():
    while True:
        try:
            run_realtime_scanner()
        except Exception as e:
            print(f"⚠️ خطأ في حلقة التنبيهات الفورية: {e}")
        # يفحص القائمة كل 15 دقيقة تلقائياً
        time.sleep(900)

def listen_for_commands():
    print("🤖 نظام التنبيهات الفورية Quant OS V2.0 يعمل الآن بنجاح...")
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=30"
            response = requests.get(url, timeout=35)
            if response.status_code == 200:
                data = response.json()
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    message = update.get("message", {})
                    text = message.get("text", "").strip()
                    chat_id = str(message.get("chat", {}).get("id", ""))
                    
                    if chat_id == TELEGRAM_CHAT_ID and text:
                        text_lower = text.lower()
                        if text_lower in ["/scan", "فحص", "تحديث"]:
                            send_telegram_message("⏳ <b>جاري فحص السوق والبحث عن فرص فورية يدوياً...</b>")
                            run_realtime_scanner()
                            send_telegram_message("✅ <b>تم الانتهاء من الفحص اليدوي.</b>")
                        elif text_lower in ["/start"]:
                            send_telegram_message("🤖 <b>مرحباً بك في نظام التنبيهات الفورية Quant OS V2.0</b>\n\n• النظام يراقب السوق تلقائياً ويرسل تنبيهات الشارت والشسيولة فور حدوثها.")
                        else:
                            send_telegram_message(f"⏳ <b>جاري التحليل الفوري وتوليد الشارت للسهم: {text.upper()}...</b>")
                            msg, chart_path = analyze_single_ticker(text)
                            if chart_path and os.path.exists(chart_path):
                                send_telegram_photo(chart_path, msg)
                                try:
                                    os.remove(chart_path)
                                except:
                                    pass
                            else:
                                send_telegram_message(msg)
        except Exception as e:
            print(f"⚠️ خطأ في استقبال الأوامر: {e}")
            time.sleep(5)
        time.sleep(2)

if __name__ == "__main__":
    t = threading.Thread(target=background_scheduler, daemon=True)
    t.start()
    listen_for_commands()
