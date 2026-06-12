"""
美股 / 加密貨幣 每日市場日報 + 警示系統 (v9)
監控標的:QQQ、TSLA、MRVL、BTC-USD
持股損益:
  - 美股(USD):QQQ、TSLA、MRVL —— 用券商成本均價 × 持股
  - BTC(TWD):獨立計算 —— 用即時幣價 × 匯率換算台幣,對比台幣成本
通知時機:每天執行,無論是否觸發都會發送

警示分級(由強到弱):
  🚨 大跌警示:當日跌幅 >= 5%(BTC 8%)或 5 日累積 >= 10%(BTC 15%)h
  📉 跌破年線警示:收盤價從年線(MA240)上方跌破到下方
  📉 跌破季線警示:收盤價從季線(MA60)上方跌破到下方
  ✅ 一般日報:其餘標的的當日狀態
  💼 投資組合損益(美股,USD):每天附在最後
  ₿ BTC 持倉損益(TWD):每天附在最後

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
    "MRVL": {
        "symbol": "MRVL",
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

# ===== 美股持股設定(USD 損益計算用) =====
# 更新方式:對照券商「複委託庫存」CSV
#   shares   -> 「可用庫存」欄
#   avg_cost -> 「均價」欄(已含手續費,即真實成本均價)
# 或直接用 gen_holdings.py 由 CSV 自動產生後貼回此區塊。
# 沒有持股的標的(如 BTC)不列入此處,BTC 由下方 BTC_HOLDING 獨立計算。
# 來源:複委託庫存 20260610
HOLDINGS = {
    "QQQ":  {"shares": 1.06163, "avg_cost": 660.023},
    "TSLA": {"shares": 3.01145, "avg_cost": 400.013},
    "MRVL": {"shares": 4.00000, "avg_cost": 287.350},
}

# ===== BTC 持倉設定(TWD 損益計算用,與美股分開) =====
# BTC 以台幣在交易所買進,與複委託美股不同貨幣/平台,故獨立計算。
# 系統每天:現值(TWD) = amount × BTC-USD即時價 × USD/TWD即時匯率
#           損益(TWD) = 現值 - cost_twd
#   amount   -> 你錢包顯示的 BTC 數量
#   cost_twd -> 投入成本(台幣)
# amount 設為 0 則不顯示 BTC 損益(但 BTC 仍會照常做大跌/均線監控)。
BTC_HOLDING = {
    "amount": 0.00433356,   # ⚠️ 估算值(由錢包現值反推),請換成錢包實際 BTC 數量
    "cost_twd": 10000.0,    # 投入成本(台幣),由錢包 ROI -11.56% 反推 ≈ 10000
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


def fetch_usdtwd():
    """抓 USD/TWD 即時匯率(1 美元 = ? 台幣)。失敗回傳 None。"""
    try:
        fx = yf.Ticker("TWD=X").history(period="5d")["Close"].dropna()
        if not fx.empty:
            return float(fx.iloc[-1])
    except Exception as e:
        print(f"[警告] 匯率(USD/TWD)抓取失敗:{e}")
    return None


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

    # === 美股持股損益(USD,若有部位) ===
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
    """日報:價格/走勢 + 持股損益(若有)內嵌,一行一個資訊。"""
    name = result["name"]
    daily = result["daily_change_pct"]
    arrow = "📈" if daily >= 0 else "📉"

    lines = [
        f"{arrow} {name} 當日 {daily:+.2f}%",
        f"   收盤 {result['latest_price']:.2f}",
        f"   30日高 {result['high_30d']:.2f}({result['high_30d_date']})",
        f"   距高 {result['drawdown_pct']:+.2f}%",
    ]

    # === 季線/年線壓一行 ===
    ma_line = ""
    if result["ma60"] is not None:
        sign = "下" if result["vs_ma60"] < 0 else ""
        ma_line += f"季線{sign}{abs(result['vs_ma60']):.1f}%"
    if result["ma240"] is not None:
        if ma_line:
            ma_line += "  "
        sign = "下" if result["vs_ma240"] < 0 else ""
        ma_line += f"年線{sign}{abs(result['vs_ma240']):.1f}%"
    if ma_line:
        lines.append(f"   {ma_line}")

    # === 持股損益(若有) ===
    if result.get("holding"):
        h = result["holding"]
        lines.append(f"   持股 {h['shares']:.5f} 股")
        lines.append(f"   均價 {h['avg_cost']:.2f} 成本 {h['cost_basis']:.2f}")
        lines.append(f"   現值 {h['market_value']:.2f}")
        sign = "🟢" if h["pnl_amount"] >= 0 else "🔴"
        lines.append(f"   {sign} 損益 {h['pnl_amount']:+.2f}({h['pnl_pct']:+.2f}%)")

    return "\n".join(lines)


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

    # 附帶持股損益
    if result["holding"]:
        h = result["holding"]
        lines.append(f"持股損益:{h['pnl_amount']:+.2f}({h['pnl_pct']:+.2f}%)")

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

    # 附帶持股損益(若有部位)
    if result["holding"]:
        h = result["holding"]
        lines.append(f"💼 持股 {h['shares']:.5f} 股")
        lines.append(f"   均價 {h['avg_cost']:.2f} 成本 {h['cost_basis']:.2f}")
        lines.append(f"   現值 {h['market_value']:.2f}")
        sign = "🟢" if h["pnl_amount"] >= 0 else "🔴"
        lines.append(f"   {sign} 損益 {h['pnl_amount']:+.2f}({h['pnl_pct']:+.2f}%)")
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

    # 附帶持股損益(美股,若有部位)
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
    """美股投資組合總計(無標題,作為日報尾部)。"""
    held = [r for r in results if r.get("holding")]
    if not held:
        return None

    total_cost = sum(r["holding"]["cost_basis"] for r in held)
    total_value = sum(r["holding"]["market_value"] for r in held)
    total_pnl = total_value - total_cost
    total_pct = (total_pnl / total_cost * 100) if total_cost else 0.0

    big = "🟢" if total_pnl >= 0 else "🔴"
    lines = [
        f"總成本 ${total_cost:.2f}",
        f"總現值 ${total_value:.2f}",
        f"{big} 總損益 ${total_pnl:+.2f}({total_pct:+.2f}%)",
    ]
    return "\n".join(lines)


def format_btc_holding(btc_result: dict, usdtwd) -> str:
    """BTC 持倉損益(TWD,獨立計算)。每行一個資訊。"""
    if not BTC_HOLDING or BTC_HOLDING.get("amount", 0) <= 0:
        return None
    if btc_result is None:
        return None
    if usdtwd is None:
        return ("₿ BTC 持倉損益(台幣)\n"
                "─────────────\n"
                "   ⚠️ 匯率抓取失敗,本次無法換算台幣損益")

    amount = BTC_HOLDING["amount"]
    cost_twd = BTC_HOLDING["cost_twd"]
    btc_usd = btc_result["latest_price"]
    value_twd = amount * btc_usd * usdtwd
    pnl_twd = value_twd - cost_twd
    pnl_pct = (pnl_twd / cost_twd * 100) if cost_twd else 0.0
    sign = "🟢" if pnl_twd >= 0 else "🔴"

    lines = [
        "₿ BTC 持倉損益(台幣)",
        "─────────────",
        f"   持有 {amount:.8f} BTC",
        f"   幣價 US${btc_usd:,.0f}",
        f"   匯率 {usdtwd:.2f}(USD/TWD)",
        f"   成本 NT${cost_twd:,.0f}",
        f"   現值 NT${value_twd:,.0f}",
        f"   {sign} 損益 NT${pnl_twd:+,.0f}({pnl_pct:+.2f}%)",
    ]
    return "\n".join(lines)


def build_message(results: list, usdtwd=None) -> str:
    """組裝完整訊息。每個標的統一用 format_normal 顯示。"""
    today = datetime.now().strftime("%Y-%m-%d")

    sections = [f"📊 市場日報 ({today})"]

    for r in results:
        sections.append(format_normal(r))

    # === 美股投資組合總計(無標題,分隔線) ===
    portfolio = format_portfolio(results)
    if portfolio:
        sections.append("═════════════")
        sections.append(portfolio)

    # === BTC 持倉損益(TWD,獨立,在最後) ===
    btc_result = next((r for r in results if r["name"] == "BTC"), None)
    btc_block = format_btc_holding(btc_result, usdtwd)
    if btc_block:
        sections.append(btc_block)

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
