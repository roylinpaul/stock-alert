"""
美股 / 加密貨幣 每日市場日報 + 警示系統 (v5)
監控標的:QQQ、TSLA、BTC-USD、NASDAQ Composite
通知時機:每天執行,無論是否觸發都會發送

警示分級(由強到弱):
  🚨 大跌警示:當日跌幅 >= 5%(BTC 8%)或 5 日累積 >= 10%(BTC 15%)
  📌 觀察點提醒:從 30 日高點回落 >= 10%(BTC 15%)
  ✅ 一般日報:其餘標的的當日狀態

觀察點純標記,不涉及加碼建議。
最終決策永遠由你判斷市場狀況、現金部位、整體環境後決定。

通知方式:LINE Messaging API
"""

import os
from datetime import datetime

import requests
import yfinance as yf


# ===== 監控標的設定 =====
# daily_threshold: 當日跌幅大跌警示門檻
# multi_day_threshold: 近 N 日累積跌幅警示門檻
# watch_threshold: 從 30 日高點回落觀察點提醒門檻
TICKERS = {
    "QQQ": {
        "symbol": "QQQ",
        "daily_threshold": -5.0,
        "multi_day_threshold": -10.0,
        "watch_threshold": -10.0,
    },
    "TSLA": {
        "symbol": "TSLA",
        "daily_threshold": -5.0,
        "multi_day_threshold": -10.0,
        "watch_threshold": -10.0,
    },
    "BTC": {
        "symbol": "BTC-USD",
        "daily_threshold": -8.0,
        "multi_day_threshold": -15.0,
        "watch_threshold": -15.0,   # BTC 波動大,觀察點也設寬一點
    },
    "NASDAQ": {
        "symbol": "^IXIC",
        "daily_threshold": -5.0,
        "multi_day_threshold": -10.0,
        "watch_threshold": -10.0,
    },
}

MULTI_DAY_WINDOW = 5      # 累積跌幅觀察天數
HIGH_POINT_WINDOW = 30    # 高點觀察區間

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_IDS_RAW = os.environ.get("LINE_USER_IDS", "")
LINE_USER_IDS = [uid.strip() for uid in LINE_USER_IDS_RAW.split(",") if uid.strip()]

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


def fetch_price_data(symbol: str, days: int = 40):
    """抓取最近 N 個交易日的收盤價。"""
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period=f"{days + 10}d")
    if hist.empty:
        return None
    return hist["Close"].dropna()


def analyze(name: str, config: dict):
    """分析單一標的。"""
    closes = fetch_price_data(
        config["symbol"],
        days=max(MULTI_DAY_WINDOW, HIGH_POINT_WINDOW) + 2,
    )
    if closes is None or len(closes) < 2:
        print(f"[警告] {name} 資料不足,略過")
        return None

    latest_price = closes.iloc[-1]
    prev_price = closes.iloc[-2]
    daily_change_pct = (latest_price - prev_price) / prev_price * 100

    # 30 日高點
    recent_window = closes.iloc[-HIGH_POINT_WINDOW:] if len(closes) >= HIGH_POINT_WINDOW else closes
    high_30d = recent_window.max()
    high_30d_date = recent_window.idxmax().strftime("%m/%d")
    drawdown_pct = (latest_price - high_30d) / high_30d * 100

    # 累積跌幅
    cumulative_change_pct = None
    if len(closes) > MULTI_DAY_WINDOW:
        window_start_price = closes.iloc[-(MULTI_DAY_WINDOW + 1)]
        cumulative_change_pct = (latest_price - window_start_price) / window_start_price * 100

    # 判斷三種狀態
    is_daily_alert = daily_change_pct <= config["daily_threshold"]
    is_multi_day_alert = (
        cumulative_change_pct is not None
        and cumulative_change_pct <= config["multi_day_threshold"]
    )
    is_alert = is_daily_alert or is_multi_day_alert
    is_watch = drawdown_pct <= config["watch_threshold"]

    return {
        "name": name,
        "latest_price": latest_price,
        "prev_price": prev_price,
        "daily_change_pct": daily_change_pct,
        "high_30d": high_30d,
        "high_30d_date": high_30d_date,
        "drawdown_pct": drawdown_pct,
        "cumulative_change_pct": cumulative_change_pct,
        "is_alert": is_alert,
        "is_daily_alert": is_daily_alert,
        "is_multi_day_alert": is_multi_day_alert,
        "is_watch": is_watch,
        "daily_threshold": config["daily_threshold"],
        "multi_day_threshold": config["multi_day_threshold"],
        "watch_threshold": config["watch_threshold"],
    }


def format_normal(result: dict) -> str:
    """一般狀況:簡潔顯示。"""
    name = result["name"]
    daily = result["daily_change_pct"]
    arrow = "📈" if daily >= 0 else "📉"

    return (
        f"{arrow} {name} 當日 {daily:+.2f}%\n"
        f"   收盤 {result['latest_price']:.2f}|"
        f"30日高 {result['high_30d']:.2f}({result['high_30d_date']})|"
        f"距高 {result['drawdown_pct']:+.2f}%"
    )


def format_watch(result: dict) -> str:
    """觀察點提醒:已到自訂的關注閾值,純標記不提決策。"""
    name = result["name"]
    daily = result["daily_change_pct"]
    lines = [
        f"📌 {name} 觸發觀察點",
        f"當前:{result['latest_price']:.2f}",
        f"30 日高點:{result['high_30d']:.2f}({result['high_30d_date']})",
        f"距高點:{result['drawdown_pct']:+.2f}%(觀察門檻 {result['watch_threshold']}%)",
        f"當日:{daily:+.2f}%",
        "",
        "📍 已達到你設定的關注閾值,可評估市場狀況。",
    ]
    return "\n".join(lines)


def format_alert(result: dict) -> str:
    """觸發大跌:強化版顯示。"""
    name = result["name"]
    daily = result["daily_change_pct"]
    lines = [
        "🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨",
        f"📉📉  {name} 大跌警示  📉📉",
        f"🔻🔻  跌幅 {daily:+.2f}%  🔻🔻",
        "🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨",
        "",
        f"💰 收盤:{result['latest_price']:.2f}",
        f"📊 前一日:{result['prev_price']:.2f}",
        f"⛰️ 30 日高點:{result['high_30d']:.2f}({result['high_30d_date']})",
        f"📉 距高點:{result['drawdown_pct']:+.2f}%",
    ]

    if result["cumulative_change_pct"] is not None:
        lines.append(
            f"📅 近 {MULTI_DAY_WINDOW} 日累積:{result['cumulative_change_pct']:+.2f}%"
        )

    lines.append("")
    if result["is_daily_alert"]:
        lines.append(
            f"⚠️ 當日跌幅突破門檻 ({result['daily_threshold']}%)"
        )
    if result["is_multi_day_alert"]:
        lines.append(
            f"⚠️ 累積跌幅突破門檻 ({result['multi_day_threshold']}%)"
        )

    lines.append("━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def build_message(results: list) -> str:
    """組裝完整訊息。"""
    today = datetime.now().strftime("%Y-%m-%d")

    # 三類分組:大跌警示(最強) → 觀察點(中等) → 一般日報(最弱)
    # 注意:大跌警示優先於觀察點,觸發大跌的標的就不再列為觀察點
    alert_results = [r for r in results if r["is_alert"]]
    watch_results = [r for r in results if r["is_watch"] and not r["is_alert"]]
    normal_results = [r for r in results if not r["is_alert"] and not r["is_watch"]]

    sections = []

    # === 大跌警示區塊 ===
    if alert_results:
        sections.append(f"🚨🚨🚨 緊急警示 🚨🚨🚨\n({today})")
        for r in alert_results:
            sections.append(format_alert(r))

    # === 觀察點區塊 ===
    if watch_results:
        if alert_results:
            sections.append("📌 觀察點提醒")
        else:
            sections.append(f"📌 觀察點提醒 ({today})")
        for r in watch_results:
            sections.append(format_watch(r))

    # === 一般日報區塊 ===
    if normal_results:
        if alert_results or watch_results:
            sections.append("📊 其餘標的日報")
        else:
            sections.append(f"📊 市場日報 ({today})")
        for r in normal_results:
            sections.append(format_normal(r))

    return "\n\n".join(sections)


def send_line_message(text: str) -> bool:
    """透過 LINE Messaging API push 訊息。"""
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

    results = []
    for name, config in TICKERS.items():
        try:
            r = analyze(name, config)
            if r:
                results.append(r)
                if r["is_alert"]:
                    status = "🚨 警示"
                elif r["is_watch"]:
                    status = "📌 觀察"
                else:
                    status = "✅ 正常"
                print(
                    f"[{status}] {name} 當日 {r['daily_change_pct']:+.2f}%, "
                    f"距 {HIGH_POINT_WINDOW} 日高點 {r['drawdown_pct']:+.2f}%"
                )
        except Exception as e:
            print(f"[錯誤] 處理 {name} 時發生例外:{e}")

    if not results:
        print("無任何資料,結束。")
        return

    message = build_message(results)
    print("\n=== 即將發送訊息 ===")
    print(message)
    print("===================\n")

    send_line_message(message)


if __name__ == "__main__":
    main()
