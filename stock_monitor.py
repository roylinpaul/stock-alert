"""
美股 / 加密貨幣 跌幅監控腳本(v3:多人通知 + 近30日高點)
監控標的:QQQ、TSLA、BTC-USD
觸發條件:
  1. 當日跌幅 >= 5%
  2. 近 N 天累積跌幅 >= 10%
通知方式:LINE Messaging API,可同時通知多個 User

每則通知都會附帶:
  - 最新價、前一日收盤
  - 當日漲跌
  - 近 30 日高點 + 距高點漲跌幅(顯示從高點回落多少)
"""

import os
from datetime import datetime

import requests
import yfinance as yf


# ===== 設定區 =====
TICKERS = {
    "QQQ": "QQQ",
    "TSLA": "TSLA",
    "BTC": "BTC-USD",
}

DAILY_DROP_THRESHOLD = -5.0
MULTI_DAY_WINDOW = 5
MULTI_DAY_DROP_THRESHOLD = -10.0
HIGH_POINT_WINDOW = 30  # 近 N 日高點觀察區間

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_IDS_RAW = os.environ.get("LINE_USER_IDS", "")
LINE_USER_IDS = [uid.strip() for uid in LINE_USER_IDS_RAW.split(",") if uid.strip()]

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


def fetch_price_data(symbol: str, days: int = 40):
    """抓取最近 N 個交易日的收盤價。預設多抓一些以涵蓋 30 日高點計算。"""
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period=f"{days + 10}d")
    if hist.empty:
        return None
    return hist["Close"].dropna()


def analyze(symbol: str, display_name: str):
    """分析單一標的,回傳訊息(無論是否觸發都會回 None 或字串)。"""
    closes = fetch_price_data(symbol, days=max(MULTI_DAY_WINDOW, HIGH_POINT_WINDOW) + 2)
    if closes is None or len(closes) < 2:
        print(f"[警告] {display_name} 資料不足,略過")
        return None

    latest_price = closes.iloc[-1]
    prev_price = closes.iloc[-2]
    daily_change_pct = (latest_price - prev_price) / prev_price * 100

    # === 計算近 30 日高點 ===
    recent_window = closes.iloc[-HIGH_POINT_WINDOW:] if len(closes) >= HIGH_POINT_WINDOW else closes
    high_30d = recent_window.max()
    high_30d_date = recent_window.idxmax().strftime("%m/%d")
    drawdown_pct = (latest_price - high_30d) / high_30d * 100  # 從高點回落幾%

    # === 檢查警示條件 ===
    alerts = []

    if daily_change_pct <= DAILY_DROP_THRESHOLD:
        alerts.append(
            f"⚠️ 當日跌幅 {daily_change_pct:.2f}%(門檻 {DAILY_DROP_THRESHOLD}%)"
        )

    if len(closes) > MULTI_DAY_WINDOW:
        window_start_price = closes.iloc[-(MULTI_DAY_WINDOW + 1)]
        cumulative_change_pct = (latest_price - window_start_price) / window_start_price * 100
        if cumulative_change_pct <= MULTI_DAY_DROP_THRESHOLD:
            alerts.append(
                f"⚠️ 近 {MULTI_DAY_WINDOW} 日累積跌幅 {cumulative_change_pct:.2f}%"
                f"(門檻 {MULTI_DAY_DROP_THRESHOLD}%)"
            )

    if not alerts:
        print(
            f"[OK] {display_name} 當日 {daily_change_pct:+.2f}%, "
            f"距 {HIGH_POINT_WINDOW} 日高點 {drawdown_pct:+.2f}%,未觸發警示"
        )
        return None

    # === 組裝訊息 ===
    msg_lines = [
        f"📉 {display_name} 觸發警示",
        f"最新價:{latest_price:.2f}",
        f"前一日:{prev_price:.2f}({daily_change_pct:+.2f}%)",
        f"📊 近 {HIGH_POINT_WINDOW} 日高點:{high_30d:.2f}({high_30d_date})",
        f"📊 距高點:{drawdown_pct:+.2f}%",
        *alerts,
    ]
    return "\n".join(msg_lines)


def send_line_message(text: str) -> bool:
    """透過 LINE Messaging API push 訊息給所有設定的 User。"""
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_IDS:
        print("[錯誤] 未設定 LINE_CHANNEL_ACCESS_TOKEN 或 LINE_USER_IDS")
        print("訊息內容(未發送):")
        print(text)
        return False

    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    success_count = 0
    for user_id in LINE_USER_IDS:
        payload = {
            "to": user_id,
            "messages": [{"type": "text", "text": text}],
        }
        try:
            resp = requests.post(LINE_PUSH_URL, headers=headers, json=payload, timeout=10)
            if resp.status_code == 200:
                print(f"[OK] LINE 訊息已送出至 {user_id[:10]}...")
                success_count += 1
            else:
                print(f"[錯誤] 發送至 {user_id[:10]}... 失敗 ({resp.status_code}): {resp.text}")
        except requests.RequestException as e:
            print(f"[錯誤] 發送至 {user_id[:10]}... 例外:{e}")

    return success_count > 0


def main():
    print(f"=== 監控執行於 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"通知對象數:{len(LINE_USER_IDS)} 人")

    triggered_messages = []
    for display_name, symbol in TICKERS.items():
        try:
            msg = analyze(symbol, display_name)
            if msg:
                triggered_messages.append(msg)
        except Exception as e:
            print(f"[錯誤] 處理 {display_name} 時發生例外:{e}")

    if not triggered_messages:
        print("無警示,結束。")
        return

    header = f"🚨 市場跌幅警示 ({datetime.now().strftime('%Y-%m-%d')})"
    full_message = header + "\n\n" + "\n\n".join(triggered_messages)
    send_line_message(full_message)


if __name__ == "__main__":
    main()
