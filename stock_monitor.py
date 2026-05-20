"""
美股 / 加密貨幣 每日市場日報 + 警示系統 (v6)
監控標的:QQQ、TSLA、BTC-USD、NASDAQ Composite
通知時機:每天執行,無論是否觸發都會發送

警示分級(由強到弱):
  🚨 大跌警示:當日跌幅 >= 5%(BTC 8%)或 5 日累積 >= 10%(BTC 15%)
  📉 跌破年線警示:收盤價從年線(MA240)上方跌破到下方
  📉 跌破季線警示:收盤價從季線(MA60)上方跌破到下方
  📌 觀察點提醒:從 30 日高點回落 >= 10%(BTC 15%)
  ✅ 一般日報:其餘標的的當日狀態

通知方式:LINE Messaging API
"""

import os
from datetime import datetime

import requests
import yfinance as yf


# ===== 監控標的設定 =====
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
        "watch_threshold": -15.0,
    },
    "NASDAQ": {
        "symbol": "^IXIC",
        "daily_threshold": -5.0,
        "multi_day_threshold": -10.0,
        "watch_threshold": -10.0,
    },
}

MULTI_DAY_WINDOW = 5
HIGH_POINT_WINDOW = 30
MA_QUARTER = 60    # 季線 = 60 日均線
MA_YEAR = 240      # 年線 = 240 日均線

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_IDS_RAW = os.environ.get("LINE_USER_IDS", "")
LINE_USER_IDS = [uid.strip() for uid in LINE_USER_IDS_RAW.split(",") if uid.strip()]

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


def fetch_price_data(symbol: str, days: int = 260):
    """抓取最近 N 個交易日的收盤價。需要 240+ 天才能算年線。"""
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period=f"{days + 20}d")
    if hist.empty:
        return None
    return hist["Close"].dropna()


def calc_ma(closes, window: int):
    """計算移動平均線。回傳最近 2 天的 MA 值(用來判斷是否剛跌破)。"""
    if len(closes) < window + 1:
        return None, None
    ma_series = closes.rolling(window=window).mean()
    ma_today = ma_series.iloc[-1]
    ma_yesterday = ma_series.iloc[-2]
    return ma_today, ma_yesterday


def analyze(name: str, config: dict):
    """分析單一標的。"""
    closes = fetch_price_data(config["symbol"], days=MA_YEAR + 10)
    if closes is None or len(closes) < 2:
        print(f"[警告] {name} 資料不足,略過")
        return None

    latest_price = closes.iloc[-1]
    prev_price = closes.iloc[-2]
    daily_change_pct = (latest_price - prev_price) / prev_price * 100

    # === 30 日高點 ===
    hp_window = closes.iloc[-HIGH_POINT_WINDOW:] if len(closes) >= HIGH_POINT_WINDOW else closes
    high_30d = hp_window.max()
    high_30d_date = hp_window.idxmax().strftime("%m/%d")
    drawdown_pct = (latest_price - high_30d) / high_30d * 100

    # === 累積跌幅 ===
    cumulative_change_pct = None
    if len(closes) > MULTI_DAY_WINDOW:
        window_start_price = closes.iloc[-(MULTI_DAY_WINDOW + 1)]
        cumulative_change_pct = (latest_price - window_start_price) / window_start_price * 100

    # === 季線(MA60) ===
    ma60_today, ma60_yesterday = calc_ma(closes, MA_QUARTER)
    broke_ma60 = False
    if ma60_today is not None and ma60_yesterday is not None:
        # 跌破 = 昨天在上方(或等於),今天掉到下方
        broke_ma60 = (prev_price >= ma60_yesterday) and (latest_price < ma60_today)

    # === 年線(MA240) ===
    ma240_today, ma240_yesterday = calc_ma(closes, MA_YEAR)
    broke_ma240 = False
    if ma240_today is not None and ma240_yesterday is not None:
        broke_ma240 = (prev_price >= ma240_yesterday) and (latest_price < ma240_today)

    # === 判斷狀態 ===
    is_daily_alert = daily_change_pct <= config["daily_threshold"]
    is_multi_day_alert = (
        cumulative_change_pct is not None
        and cumulative_change_pct <= config["multi_day_threshold"]
    )
    is_alert = is_daily_alert or is_multi_day_alert
    is_watch = drawdown_pct <= config["watch_threshold"]

    # === 目前相對季線/年線位置(用於日報顯示) ===
    vs_ma60 = None
    vs_ma240 = None
    if ma60_today is not None:
        vs_ma60 = (latest_price - ma60_today) / ma60_today * 100
    if ma240_today is not None:
        vs_ma240 = (latest_price - ma240_today) / ma240_today * 100

    return {
        "name": name,
        "latest_price": latest_price,
        "prev_price": prev_price,
        "daily_change_pct": daily_change_pct,
        "high_30d": high_30d,
        "high_30d_date": high_30d_date,
        "drawdown_pct": drawdown_pct,
        "cumulative_change_pct": cumulative_change_pct,
        "ma60": ma60_today,
        "ma240": ma240_today,
        "vs_ma60": vs_ma60,
        "vs_ma240": vs_ma240,
        "broke_ma60": broke_ma60,
        "broke_ma240": broke_ma240,
        "is_alert": is_alert,
        "is_daily_alert": is_daily_alert,
        "is_multi_day_alert": is_multi_day_alert,
        "is_watch": is_watch,
        "daily_threshold": config["daily_threshold"],
        "multi_day_threshold": config["multi_day_threshold"],
        "watch_threshold": config["watch_threshold"],
    }


def format_normal(result: dict) -> str:
    """一般狀況:簡潔顯示,包含季線/年線資訊。"""
    name = result["name"]
    daily = result["daily_change_pct"]
    arrow = "📈" if daily >= 0 else "📉"

    line1 = (
        f"{arrow} {name} 當日 {daily:+.2f}%\n"
        f"   收盤 {result['latest_price']:.2f}|"
        f"30日高 {result['high_30d']:.2f}({result['high_30d_date']})|"
        f"距高 {result['drawdown_pct']:+.2f}%"
    )

    # 加入季線/年線位置
    ma_info = []
    if result["ma60"] is not None:
        pos = "上" if result["vs_ma60"] >= 0 else "下"
        ma_info.append(f"季線{pos}{abs(result['vs_ma60']):.1f}%")
    if result["ma240"] is not None:
        pos = "上" if result["vs_ma240"] >= 0 else "下"
        ma_info.append(f"年線{pos}{abs(result['vs_ma240']):.1f}%")

    if ma_info:
        line1 += f"\n   {' | '.join(ma_info)}"

    return line1


def format_watch(result: dict) -> str:
    """觀察點提醒。"""
    lines = [
        f"📌 {result['name']} 觸發觀察點",
        f"當前:{result['latest_price']:.2f}",
        f"30 日高點:{result['high_30d']:.2f}({result['high_30d_date']})",
        f"距高點:{result['drawdown_pct']:+.2f}%(觀察門檻 {result['watch_threshold']}%)",
        f"當日:{result['daily_change_pct']:+.2f}%",
    ]

    # 附帶均線位置
    if result["ma60"] is not None:
        lines.append(f"季線(MA60):{result['ma60']:.2f}(距 {result['vs_ma60']:+.2f}%)")
    if result["ma240"] is not None:
        lines.append(f"年線(MA240):{result['ma240']:.2f}(距 {result['vs_ma240']:+.2f}%)")

    lines.append("")
    lines.append("📍 已達到你設定的關注閾值,可評估市場狀況。")
    return "\n".join(lines)


def format_ma_break(result: dict) -> str:
    """跌破季線或年線的警示。"""
    name = result["name"]
    lines = []

    if result["broke_ma240"]:
        lines.extend([
            "⛔⛔⛔⛔⛔⛔⛔⛔⛔⛔",
            f"📉📉  {name} 跌破年線  📉📉",
            "⛔⛔⛔⛔⛔⛔⛔⛔⛔⛔",
            "",
            f"💰 收盤:{result['latest_price']:.2f}",
            f"📊 年線(MA240):{result['ma240']:.2f}",
            f"📊 距年線:{result['vs_ma240']:+.2f}%",
            "",
            "⚠️ 股價從年線上方跌破到下方",
            "⚠️ 年線是長期多空分界,跌破代表中長期趨勢轉弱",
            "━━━━━━━━━━━━━━━",
        ])

    if result["broke_ma60"]:
        lines.extend([
            "⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️",
            f"📉📉  {name} 跌破季線  📉📉",
            "⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️",
            "",
            f"💰 收盤:{result['latest_price']:.2f}",
            f"📊 季線(MA60):{result['ma60']:.2f}",
            f"📊 距季線:{result['vs_ma60']:+.2f}%",
            "",
            "⚠️ 股價從季線上方跌破到下方",
            "⚠️ 季線是中期趨勢支撐,跌破需留意後續走勢",
            "━━━━━━━━━━━━━━━",
        ])

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

    # 附帶均線位置
    if result["ma60"] is not None:
        lines.append(f"📊 季線(MA60):{result['ma60']:.2f}(距 {result['vs_ma60']:+.2f}%)")
    if result["ma240"] is not None:
        lines.append(f"📊 年線(MA240):{result['ma240']:.2f}(距 {result['vs_ma240']:+.2f}%)")

    lines.append("")
    if result["is_daily_alert"]:
        lines.append(f"⚠️ 當日跌幅突破門檻 ({result['daily_threshold']}%)")
    if result["is_multi_day_alert"]:
        lines.append(f"⚠️ 累積跌幅突破門檻 ({result['multi_day_threshold']}%)")

    lines.append("━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def build_message(results: list) -> str:
    """組裝完整訊息。"""
    today = datetime.now().strftime("%Y-%m-%d")

    # 五類分組:大跌 → 跌破均線 → 觀察點 → 一般日報
    alert_results = [r for r in results if r["is_alert"]]
    ma_break_results = [r for r in results if (r["broke_ma60"] or r["broke_ma240"]) and not r["is_alert"]]
    watch_results = [r for r in results if r["is_watch"] and not r["is_alert"] and not r["broke_ma60"] and not r["broke_ma240"]]
    normal_results = [r for r in results if not r["is_alert"] and not r["is_watch"] and not r["broke_ma60"] and not r["broke_ma240"]]

    sections = []

    # === 大跌警示 ===
    if alert_results:
        sections.append(f"🚨🚨🚨 緊急警示 🚨🚨🚨\n({today})")
        for r in alert_results:
            sections.append(format_alert(r))
            # 如果同時跌破均線,附帶提示
            if r["broke_ma60"] or r["broke_ma240"]:
                sections.append(format_ma_break(r))

    # === 跌破均線警示 ===
    if ma_break_results:
        if not alert_results:
            sections.append(f"📉 均線跌破警示 ({today})")
        else:
            sections.append("📉 均線跌破警示")
        for r in ma_break_results:
            sections.append(format_ma_break(r))

    # === 觀察點 ===
    if watch_results:
        if alert_results or ma_break_results:
            sections.append("📌 觀察點提醒")
        else:
            sections.append(f"📌 觀察點提醒 ({today})")
        for r in watch_results:
            sections.append(format_watch(r))

    # === 一般日報 ===
    if normal_results:
        if alert_results or ma_break_results or watch_results:
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
                flags = []
                if r["is_alert"]:
                    flags.append("🚨大跌")
                if r["broke_ma240"]:
                    flags.append("⛔破年線")
                if r["broke_ma60"]:
                    flags.append("⚠️破季線")
                if r["is_watch"]:
                    flags.append("📌觀察")
                status = " ".join(flags) if flags else "✅ 正常"
                ma_info = ""
                if r["vs_ma60"] is not None:
                    ma_info += f" MA60:{r['ma60']:.1f}({r['vs_ma60']:+.1f}%)"
                if r["vs_ma240"] is not None:
                    ma_info += f" MA240:{r['ma240']:.1f}({r['vs_ma240']:+.1f}%)"
                print(
                    f"[{status}] {name} 當日 {r['daily_change_pct']:+.2f}%, "
                    f"距高 {r['drawdown_pct']:+.2f}%{ma_info}"
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
