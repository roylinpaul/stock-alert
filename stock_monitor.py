"""
美股 / 加密貨幣 跌幅監控腳本
監控標的:QQQ、TSLA、BTC-USD
觸發條件:
  1. 當日跌幅 >= 5%
  2. 近 N 天累積跌幅 >= 10%
通知方式:LINE Messaging API (push message)

使用前需安裝套件:
    pip install yfinance requests

需要設定的環境變數:
    LINE_CHANNEL_ACCESS_TOKEN  - LINE Developers 後台取得
    LINE_USER_ID               - 你的 LINE userId(加 bot 為好友後可從 webhook 取得)

建議排程:
    平日(美股交易日結束後,台灣時間隔天早上 5:30 之後)跑一次
    Crypto 可以多跑幾次,但這支腳本以「日線收盤」為基準
"""

import os
import sys
from datetime import datetime
from typing import Optional

import requests
import yfinance as yf


# ===== 設定區 =====
TICKERS = {
    "QQQ": "QQQ",          # Invesco QQQ Trust (那斯達克 100 ETF)
    "TSLA": "TSLA",        # 特斯拉
    "BTC": "BTC-USD",      # 比特幣兌美元
}

DAILY_DROP_THRESHOLD = 5.0       # 當日跌幅門檻 (%)
MULTI_DAY_WINDOW = 5              # 累積跌幅觀察天數
MULTI_DAY_DROP_THRESHOLD = -10.0  # 累積跌幅門檻 (%)

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


def fetch_price_data(symbol: str, days: int = 10):
    """抓取最近 N 個交易日的收盤價。多抓幾天是為了避免遇到假日資料不足。"""
    ticker = yf.Ticker(symbol)
    # period 多抓一點,確保有足夠的交易日
    hist = ticker.history(period=f"{days + 10}d")
    if hist.empty:
        return None
    return hist["Close"].dropna()


def analyze(symbol: str, display_name: str):
    """分析單一標的,回傳警示訊息(若有觸發)。"""
    closes = fetch_price_data(symbol, days=MULTI_DAY_WINDOW + 2)
    if closes is None or len(closes) < 2:
        print(f"[警告] {display_name} 資料不足,略過")
        return None

    latest_price = closes.iloc[-1]
    prev_price = closes.iloc[-2]
    daily_change_pct = (latest_price - prev_price) / prev_price * 100

    alerts = []

    # 條件 1:當日跌幅
    if daily_change_pct <= DAILY_DROP_THRESHOLD:
        alerts.append(
            f"⚠️ 當日跌幅 {daily_change_pct:.2f}%(門檻 {DAILY_DROP_THRESHOLD}%)"
        )

    # 條件 2:近 N 天累積跌幅
    if len(closes) > MULTI_DAY_WINDOW:
        window_start_price = closes.iloc[-(MULTI_DAY_WINDOW + 1)]
        cumulative_change_pct = (latest_price - window_start_price) / window_start_price * 100
        if cumulative_change_pct <= MULTI_DAY_DROP_THRESHOLD:
            alerts.append(
                f"⚠️ 近 {MULTI_DAY_WINDOW} 日累積跌幅 {cumulative_change_pct:.2f}%"
                f"(門檻 {MULTI_DAY_DROP_THRESHOLD}%)"
            )

    if not alerts:
        print(f"[OK] {display_name} 當日 {daily_change_pct:+.2f}%,未觸發警示")
        return None

    msg_lines = [
        f"📉 {display_name} 觸發警示",
        f"最新價:{latest_price:.2f}",
        f"前一日:{prev_price:.2f}",
        *alerts,
    ]
    return "\n".join(msg_lines)


def send_line_message(text: str) -> bool:
    """透過 LINE Messaging API push 訊息。"""
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
        print("[錯誤] 未設定 LINE_CHANNEL_ACCESS_TOKEN 或 LINE_USER_ID")
        print("訊息內容(未發送):")
        print(text)
        return False

    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": text}],
    }
    try:
        resp = requests.post(LINE_PUSH_URL, headers=headers, json=payload, timeout=10)
        if resp.status_code == 200:
            print("[OK] LINE 訊息已送出")
            return True
        else:
            print(f"[錯誤] LINE API 回應 {resp.status_code}: {resp.text}")
            return False
    except requests.RequestException as e:
        print(f"[錯誤] 發送 LINE 訊息失敗:{e}")
        return False


def main():
    print(f"=== 監控執行於 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")

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

    # 把所有警示合併成一則訊息發送(節省 LINE 訊息數)
    header = f"🚨 市場跌幅警示 ({datetime.now().strftime('%Y-%m-%d')})"
    full_message = header + "\n\n" + "\n\n".join(triggered_messages)
    send_line_message(full_message)


if __name__ == "__main__":
    main()
