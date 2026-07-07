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
}

# ===== 美股持股設定 =====
# 已依最新複委託庫存資料更新加權平均成本
HOLDINGS = {
    "QQQ": {"shares": 2.61157, "avg_cost": 698.4306},
    "TSLA": {"shares": 4.90565, "avg_cost": 395.4867},
}

# ===== BTC 持倉設定 =====
BTC_HOLDING = {
    "amount": 0.00433356,
    "cost_twd": 10000.0,
}

MULTI_DAY_WINDOW = 5
HIGH_POINT_WINDOW = 30
MA_QUARTER = 60
MA_YEAR = 240

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_IDS_RAW = os.environ.get("LINE_USER_IDS", "")
LINE_USER_IDS = [uid.strip() for uid in LINE_USER_IDS_RAW.split(",") if uid.strip()]

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"

def fetch_price_data(symbol: str):
    """僅抓取純常規交易時間的收盤價歷史。"""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="2y", prepost=False)
        if hist.empty:
            return None
        closes = hist["Close"].dropna()
        if len(closes) < 2:
            return None
        return closes
    except Exception as e:
        print(f"[錯誤] 抓取 {symbol} 歷史資料失敗: {e}")
        return None

def fetch_usdtwd():
    """抓 USD/TWD 即時匯率，採多重代碼備援機制。"""
    for symbol in ["USDTWD=X", "TWD=X"]:
        try:
            fx = yf.Ticker(symbol).history(period="1mo")["Close"].dropna()
            if not fx.empty:
                return float(fx.iloc[-1])
        except Exception:
            continue
    print("[警告] 匯率(USD/TWD)所有備用管道抓取皆失敗")
    return None

def calc_ma(closes, window: int):
    """計算移動平均線。"""
    if len(closes) < window + 1:
        return None, None
    ma_series = closes.rolling(window=window).mean()
    ma_today = ma_series.iloc[-1]
    ma_yesterday = ma_series.iloc[-2]
    return ma_today, ma_yesterday

def analyze(name: str, config: dict):
    """分析單一標的。"""
    closes = fetch_price_data(config["symbol"])
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
        broke_ma60 = (prev_price >= ma60_yesterday) and (latest_price < ma60_today)

    # === 年線(MA240) ===
    ma240_today, ma240_yesterday = calc_ma(closes, MA_YEAR)
    broke_ma240 = False
    if ma240_today is not None and ma240_yesterday is not None:
        broke_ma240 = (prev_price >= ma240_yesterday) and (latest_price < ma240_today)

    is_daily_alert = daily_change_pct <= config["daily_threshold"]
    is_multi_day_alert = (
        cumulative_change_pct is not None
        and cumulative_change_pct <= config["multi_day_threshold"]
    )
    is_alert = is_daily_alert or is_multi_day_alert
    is_watch = drawdown_pct <= config["watch_threshold"]

    vs_ma60 = None
    vs_ma240 = None
    if ma60_today is not None:
        vs_ma60 = (latest_price - ma60_today) / ma60_today * 100
    if ma240_today is not None:
        vs_ma240 = (latest_price - ma240_today) / ma240_today * 100

    h = HOLDINGS.get(name)
    holding = None
    if h and h["shares"] > 0:
        cost_basis = h["shares"] * h["avg_cost"]
        market_value = h["shares"] * latest_price
        pnl_amount = market_value - cost_basis
        pnl_pct = (pnl_amount / cost_basis * 100) if cost_basis else 0.0
        holding = {
            "shares": h["shares"],
            "avg_cost": h["avg_cost"],
            "cost_basis": cost_basis,
            "market_value": market_value,
            "pnl_amount": pnl_amount,
            "pnl_pct": pnl_pct,
        }

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
        "holding": holding,
    }

def format_normal(result: dict) -> str:
    """一般日報格式。"""
    name = result["name"]
    daily = result["daily_change_pct"]
    arrow = "📈" if daily >= 0 else "📉"

    lines = [
        f"{arrow} {name} 當日 {daily:+.2f}%",
        f"   收盤 {result['latest_price']:.2f}",
        f"   30日高 {result['high_30d']:.2f}({result['high_30d_date']})",
        f"   距高 {result['drawdown_pct']:+.2f}%",
    ]

    ma_line = ""
    if result["ma60"] is not None:
        sign = "下" if result["vs_ma60"] < 0 else "上"
        ma_line += f"季線{sign}{abs(result['vs_ma60']):.1f}%"
    if result["ma240"] is not None:
        if ma_line:
            ma_line += "  "
        sign = "下" if result["vs_ma240"] < 0 else "上"
        ma_line += f"年線{sign}{abs(result['vs_ma240']):.1f}%"
    if ma_line:
        lines.append(f"   {ma_line}")

    if result.get("holding"):
        h = result["holding"]
        lines.append(f"   持股 {h['shares']:.5f} 股")
        lines.append(f"   均價 {h['avg_cost']:.2f} 成本 {h['cost_basis']:.2f}")
        lines.append(f"   現值 {h['market_value']:.2f}")
        sign = "🟢" if h["pnl_amount"] >= 0 else "🔴"
        lines.append(f"   {sign} 損益 {h['pnl_amount']:+.2f}({h['pnl_pct']:+.2f}%)")

    return "\n".join(lines)

def format_btc_merged(result: dict, usdtwd) -> str:
    """BTC 一般日報格式：將台幣持倉數據精簡，直接合併於 BTC 區塊最下方。"""
    daily = result["daily_change_pct"]
    arrow = "📈" if daily >= 0 else "📉"

    lines = [
        f"{arrow} BTC 當日 {daily:+.2f}%",
        f"   收盤 {result['latest_price']:.2f}",
        f"   30日高 {result['high_30d']:.2f}({result['high_30d_date']})",
        f"   距高 {result['drawdown_pct']:+.2f}%",
    ]

    ma_line = ""
    if result["ma60"] is not None:
        sign = "下" if result["vs_ma60"] < 0 else "上"
        ma_line += f"季線{sign}{abs(result['vs_ma60']):.1f}%"
    if result["ma240"] is not None:
        if ma_line:
            ma_line += "  "
        sign = "下" if result["vs_ma240"] < 0 else "上"
        ma_line += f"年線{sign}{abs(result['vs_ma240']):.1f}%"
    if ma_line:
        lines.append(f"   {ma_line}")

    # === BTC 台幣持倉數據直屬合併 ===
    if BTC_HOLDING and BTC_HOLDING.get("amount", 0) > 0:
        amount = BTC_HOLDING["amount"]
        cost_twd = BTC_HOLDING["cost_twd"]
        if usdtwd is None:
            lines.append("   ⚠️ 匯率抓取失敗，本次無法換算台幣損益")
        else:
            btc_usd = result["latest_price"]
            value_twd = amount * btc_usd * usdtwd
            pnl_twd = value_twd - cost_twd
            pnl_pct = (pnl_twd / cost_twd * 100) if cost_twd else 0.0
            sign = "🟢" if pnl_twd >= 0 else "🔴"
            
            lines.append(f"   成本 NT${cost_twd:,.0f}")
            lines.append(f"   現值 NT${value_twd:,.0f}")
            lines.append(f"   {sign} 損益 NT${pnl_twd:+,.0f}({pnl_pct:+.2f}%)")

    return "\n".join(lines)

def format_watch(result: dict) -> str:
    """觀察點提醒格式。"""
    lines = [
        f"📌 {result['name']} 觸發觀察點",
        f"當前: {result['latest_price']:.2f}",
        f"30 日高點: {result['high_30d']:.2f}({result['high_30d_date']})",
        f"距高點: {result['drawdown_pct']:+.2f}%(觀察門檻 {result['watch_threshold']}%)",
        f"當日: {result['daily_change_pct']:+.2f}%",
    ]
    if result["ma60"] is not None:
        lines.append(f"季線(MA60): {result['ma60']:.2f}(距 {result['vs_ma60']:+.2f}%)")
    if result["ma240"] is not None:
        lines.append(f"年線(MA240): {result['ma240']:.2f}(距 {result['vs_ma240']:+.2f}%)")
    if result["holding"]:
        h = result["holding"]
        lines.append(f"持股損益: {h['pnl_amount']:+.2f}({h['pnl_pct']:+.2f}%)")
    lines.append("")
    lines.append("📍 已達到你設定的關注閾值，可評估市場狀況。")
    return "\n".join(lines)

def format_ma_break(result: dict) -> str:
    """跌破季線或年線的警示格式。"""
    name = result["name"]
    lines = []

    if result["broke_ma240"]:
        lines.extend([
            "⛔⛔⛔⛔⛔⛔⛔⛔⛔⛔",
            f"📉📉 {name} 跌破年線 📉📉",
            "⛔⛔⛔⛔⛔⛔⛔⛔⛔⛔",
            "",
            f"💰 收盤: {result['latest_price']:.2f}",
            f"📊 年線(MA240): {result['ma240']:.2f}",
            f"📊 距年線: {result['vs_ma240']:+.2f}%",
            "",
            "⚠️ 股價從年線上方跌破到下方",
            "⚠️ 年線是長期多空分界，跌破代表中長期趨勢轉弱",
            "━━━━━━━━━━━━━━━",
        ])

    if result["broke_ma60"]:
        lines.extend([
            "⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️",
            f"📉📉 {name} 跌破季線 📉📉",
            "⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️",
            "",
            f"💰 收盤: {result['latest_price']:.2f}",
            f"📊 季線(MA60): {result['ma60']:.2f}",
            f"📊 距季線: {result['vs_ma60']:+.2f}%",
            "",
            "⚠️ 股價從季線上方跌破到下方",
            "⚠️ 季線是中期趨勢支撐，跌破需留意後續走勢",
            "━━━━━━━━━━━━━━━",
        ])

    if result["holding"]:
        h = result["holding"]
        lines.append(f"💼 持股 {h['shares']:.5f} 股")
        lines.append(f"   均價 {h['avg_cost']:.2f} 成本 {h['cost_basis']:.2f}")
        lines.append(f"   現值 {h['market_value']:.2f}")
        sign = "🟢" if h["pnl_amount"] >= 0 else "🔴"
        lines.append(f"   {sign} 損益 {h['pnl_amount']:+.2f}({h['pnl_pct']:+.2f}%)")
    return "\n".join(lines)

def format_alert(result: dict) -> str:
    """觸發大跌的強化版警示格式。"""
    name = result["name"]
    daily = result["daily_change_pct"]
    lines = [
        "🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨",
        f"📉📉 {name} 大跌警示 📉📉",
        f"🔻🔻 跌幅 {daily:+.2f}% 🔻🔻",
        "🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨",
        "",
        f"💰 收盤: {result['latest_price']:.2f}",
        f"📊 前一日: {result['prev_price']:.2f}",
        f"⛰️ 30 日高點: {result['high_30d']:.2f}({result['high_30d_date']})",
        f"📉 距高點: {result['drawdown_pct']:+.2f}%",
    ]

    if result["cumulative_change_pct"] is not None:
        lines.append(f"📅 近 {MULTI_DAY_WINDOW} 日累積: {result['cumulative_change_pct']:+.2f}%")

    if result["ma60"] is not None:
        lines.append(f"📊 季線(MA60): {result['ma60']:.2f}(距 {result['vs_ma60']:+.2f}%)")
    if result["ma240"] is not None:
        lines.append(f"📊 年線(MA240): {result['ma240']:.2f}(距 {result['vs_ma240']:+.2f}%)")

    if result["holding"]:
        h = result["holding"]
        lines.append(f"💼 持股 {h['shares']:.5f} 股")
        lines.append(f"   均價 {h['avg_cost']:.2f} 成本 {h['cost_basis']:.2f}")
        lines.append(f"   現值 {h['market_value']:.2f}")
        sign = "🟢" if h["pnl_amount"] >= 0 else "🔴"
        lines.append(f"   {sign} 損益 {h['pnl_amount']:+.2f}({h['pnl_pct']:+.2f}%)")

    lines.append("")
    if result["is_daily_alert"]:
        lines.append(f"⚠️ 當日跌幅突破門檻 ({result['daily_threshold']}%)")
    if result["is_multi_day_alert"]:
        lines.append(f"⚠️ 累積跌幅突破門檻 ({result['multi_day_threshold']}%)")

    lines.append("━━━━━━━━━━━━━━━")
    return "\n".join(lines)

def format_portfolio(results: list) -> str:
    """美股投資組合總計。"""
    held = [r for r in results if r.get("holding")]
    if not held:
        return None

    total_cost = sum(r["holding"]["cost_basis"] for r in held)
    total_value = sum(r["holding"]["market_value"] for r in held)
    total_pnl = total_value - total_cost
    total_pct = (total_pnl / total_cost * 100) if total_cost else 0.0

    big = "🟢" if total_pnl >= 0 else "🔴"
    lines = [
        "💼 美股投資組合總計 (USD)",
        "─────────────",
        f"總成本 ${total_cost:.2f}",
        f"總現值 ${total_value:.2f}",
        f"{big} 總損益 ${total_pnl:+.2f}({total_pct:+.2f}%)",
    ]
    return "\n".join(lines)

def format_btc_holding(btc_result: dict, usdtwd) -> str:
    """當 BTC 觸發特殊狀態時的台幣庫存格式。"""
    if not BTC_HOLDING or BTC_HOLDING.get("amount", 0) <= 0:
        return None
    if btc_result is None:
        return None
    if usdtwd is None:
        return "   ⚠️ 匯率抓取失敗，本次無法換算台幣損益"

    amount = BTC_HOLDING["amount"]
    cost_twd = BTC_HOLDING["cost_twd"]
    btc_usd = btc_result["latest_price"]
    value_twd = amount * btc_usd * usdtwd
    pnl_twd = value_twd - cost_twd
    pnl_pct = (pnl_twd / cost_twd * 100) if cost_twd else 0.0
    sign = "🟢" if pnl_twd >= 0 else "🔴"

    lines = [
        f"   成本 NT${cost_twd:,.0f}",
        f"   現值 NT${value_twd:,.0f}",
        f"   {sign} 損益 NT${pnl_twd:+,.0f}({pnl_pct:+.2f}%)",
    ]
    return "\n".join(lines)

def build_message(results: list, usdtwd=None) -> str:
    """組裝完整訊息。"""
    today = datetime.now().strftime("%Y-%m-%d")

    stock_results = [r for r in results if r["name"] != "BTC"]
    btc_result = next((r for r in results if r["name"] == "BTC"), None)

    sections = [f"📊 市場日報 ({today})", "━━━━━━━━━━━━━━━"]

    # === 1. 美股個股動態格式 ===
    for r in stock_results:
        if r["is_alert"]:
            sections.append(format_alert(r))
        elif r["broke_ma60"] or r["broke_ma240"]:
            sections.append(format_ma_break(r))
        elif r["is_watch"]:
            sections.append(format_watch(r))
        else:
            sections.append(format_normal(r))

    # === 2. BTC 動態格式 ===
    if btc_result is not None:
        if btc_result["is_alert"]:
            sections.append(format_alert(btc_result))
            btc_holding_block = format_btc_holding(btc_result, usdtwd)
            if btc_holding_block:
                sections.append(btc_holding_block)
        elif btc_result["broke_ma60"] or btc_result["broke_ma240"]:
            sections.append(format_ma_break(btc_result))
            btc_holding_block = format_btc_holding(btc_result, usdtwd)
            if btc_holding_block:
                sections.append(btc_holding_block)
        elif btc_result["is_watch"]:
            sections.append(format_watch(btc_result))
            btc_holding_block = format_btc_holding(btc_result, usdtwd)
            if btc_holding_block:
                sections.append(btc_holding_block)
        else:
            sections.append(format_btc_merged(btc_result, usdtwd))

    # === 3. 美股投資組合總計 ===
    portfolio = format_portfolio(stock_results)
    if portfolio:
        sections.append("=================")
        sections.append(portfolio)

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

    usdtwd = fetch_usdtwd()
    if usdtwd is not None:
        print(f"USD/TWD 匯率:{usdtwd:.4f}")

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
                pnl_info = ""
                if r["holding"]:
                    pnl_info = f" 損益:{r['holding']['pnl_amount']:+.2f}({r['holding']['pnl_pct']:+.2f}%)"
                print(
                    f"[{status}] {name} 當日 {r['daily_change_pct']:+.2f}%, "
                    f"距高 {r['drawdown_pct']:+.2f}%{ma_info}{pnl_info}"
                )
        except Exception as e:
            print(f"[錯誤] 處理 {name} 時發生例外:{e}")

    # 【已修復】移除帶有賦值運算式的錯誤語法，改採標準防禦型程式碼
    if not results:
        print("無任何資料,結束。")
        return

    message = build_message(results, usdtwd)
    print("\n=== 即將發送訊息 ===")
    print(message)
    print("===================\n")

    send_line_message(message)

if __name__ == "__main__":
    main()
