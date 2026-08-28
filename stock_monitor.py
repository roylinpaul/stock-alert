import os
import csv
import glob
import io
import re
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
HOLDINGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "holdings")

DEFAULT_HOLDINGS = {
    "QQQ": {"shares": 3.30903, "avg_cost": 702.4717},
    "TSLA": {"shares": 7.04474, "avg_cost": 378.2652},
}

def _to_num(x):
    if x is None:
        return 0.0
    s = str(x).replace(",", "").replace('"', "").strip()
    if not s or s == "-":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0

def _pick_latest_csv(folder):
    files = glob.glob(os.path.join(folder, "*.csv"))
    if not files:
        return None
    cand = [f for f in files if "複委託庫存" in os.path.basename(f)]
    if not cand:
        cand = files

    def sort_key(f):
        m = re.search(r"(\d{14})", os.path.basename(f))
        ts = int(m.group(1)) if m else 0
        return (ts, os.path.getmtime(f))

    return max(cand, key=sort_key)

def load_holdings():
    try:
        path = _pick_latest_csv(HOLDINGS_DIR)
        if not path:
            print("[持股] 未找到庫存 CSV，使用內建預設值")
            return dict(DEFAULT_HOLDINGS)

        raw = None
        for enc in ("utf-8-sig", "cp950", "big5"):
            try:
                with io.open(path, encoding=enc, newline="") as f:
                    raw = f.read()
                break
            except UnicodeDecodeError:
                continue
        if raw is None:
            print(f"[持股] 無法解碼 {os.path.basename(path)}，使用內建預設值")
            return dict(DEFAULT_HOLDINGS)

        agg = {}
        for row in csv.DictReader(io.StringIO(raw)):
            code = (row.get("代號") or "").strip().strip('"')
            if not code:
                continue
            shares = _to_num(row.get("目前庫存"))
            if shares <= 0:
                continue
            cost = _to_num(row.get("庫存成本"))
            if cost <= 0:
                cost = shares * _to_num(row.get("均價"))
            a = agg.setdefault(code, {"shares": 0.0, "cost": 0.0})
            a["shares"] += shares
            a["cost"] += cost

        holdings = {
            code: {"shares": a["shares"], "avg_cost": a["cost"] / a["shares"]}
            for code, a in agg.items()
            if a["shares"] > 0
        }
        if not holdings:
            print(f"[持股] {os.path.basename(path)} 無有效持股列，使用內建預設值")
            return dict(DEFAULT_HOLDINGS)

        print(
            f"[持股] 已從 {os.path.basename(path)} 載入："
            + ", ".join(
                f"{k} {v['shares']:.5f}股@{v['avg_cost']:.2f}"
                for k, v in holdings.items()
            )
        )
        return holdings
    except Exception as e:
        print(f"[持股] 讀取庫存 CSV 發生例外：{e}，使用內建預設值")
        return dict(DEFAULT_HOLDINGS)

HOLDINGS = load_holdings()

# ===== BTC 持倉設定 =====
BTC_HOLDING = {
    "amount": 0.00585807,
    "cost_twd": 13000.0,
}

MULTI_DAY_WINDOW = 5
HIGH_POINT_WINDOW = 30
MA_QUARTER = 60
MA_YEAR = 240

# ===== MAX 交易所 API =====
MAX_API_BASE = "https://max-api.maicoin.com/api/v2"

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_IDS_RAW = os.environ.get("LINE_USER_IDS", "")
LINE_USER_IDS = [uid.strip() for uid in LINE_USER_IDS_RAW.split(",") if uid.strip()]

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"

def fetch_price_data(symbol: str):
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

def fetch_max_btc_twd():
    """從 MAX 交易所抓 BTC/TWD 即時行情與日K，包含 USD 參考價與匯率。"""
    try:
        t = requests.get(f"{MAX_API_BASE}/tickers/btctwd", timeout=10).json()
        last = float(t["last"])
        open_p = float(t["open"])

        k = requests.get(
            f"{MAX_API_BASE}/k",
            params={"market": "btctwd", "period": 1440, "limit": 260},
            timeout=10,
        ).json()
        closes = [float(row[4]) for row in k]

        btc_usd = None
        usdtwd = 32.0  # 備用匯率
        try:
            tu = requests.get(f"{MAX_API_BASE}/tickers/btcusdt", timeout=10).json()
            btc_usd = float(tu["last"])
            if btc_usd > 0:
                usdtwd = last / btc_usd
        except Exception:
            pass

        return {"last": last, "open": open_p, "closes": closes, "btc_usd": btc_usd, "usdtwd": usdtwd}
    except Exception as e:
        print(f"[錯誤] 抓取 MAX BTC/TWD 失敗: {e}")
        return None

def calc_ma(closes, window: int):
    if len(closes) < window + 1:
        return None, None
    ma_series = closes.rolling(window=window).mean()
    ma_today = ma_series.iloc[-1]
    ma_yesterday = ma_series.iloc[-2]
    return ma_today, ma_yesterday

def analyze(name: str, config: dict):
    """分析單一美股標的（yfinance，美元計價）。"""
    closes = fetch_price_data(config["symbol"])
    if closes is None or len(closes) < 2:
        print(f"[警告] {name} 資料不足，略過")
        return None

    latest_price = closes.iloc[-1]
    prev_price = closes.iloc[-2]

    daily_change_pct = (latest_price - prev_price) / prev_price * 100

    hp_window = closes.iloc[-HIGH_POINT_WINDOW:] if len(closes) >= HIGH_POINT_WINDOW else closes
    high_30d = hp_window.max()
    high_30d_date = hp_window.idxmax().strftime("%m/%d")
    drawdown_pct = (latest_price - high_30d) / high_30d * 100

    cumulative_change_pct = None
    if len(closes) > MULTI_DAY_WINDOW:
        window_start_price = closes.iloc[-(MULTI_DAY_WINDOW + 1)]
        cumulative_change_pct = (latest_price - window_start_price) / window_start_price * 100

    ma60_today, ma60_yesterday = calc_ma(closes, MA_QUARTER)
    broke_ma60 = False
    if ma60_today is not None and ma60_yesterday is not None:
        broke_ma60 = (prev_price >= ma60_yesterday) and (latest_price < ma60_today)

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

def analyze_btc_twd(config: dict, max_data: dict):
    """以 MAX BTC/TWD 台幣資料分析 BTC（包含 USD 計算持倉）。"""
    closes = max_data["closes"]
    if not closes or len(closes) < 2:
        return None

    latest_price = max_data["last"]
    prev_price = float(closes[-2])
    open_today = max_data["open"]

    daily_change_pct = (latest_price - open_today) / open_today * 100 if open_today else 0.0

    hp = closes[-HIGH_POINT_WINDOW:] if len(closes) >= HIGH_POINT_WINDOW else closes
    high_30d = max(hp)
    high_30d_date = datetime.now().strftime("%m/%d")

    cumulative_change_pct = None
    if len(closes) > MULTI_DAY_WINDOW:
        window_start = closes[-(MULTI_DAY_WINDOW + 1)]
        cumulative_change_pct = (latest_price - window_start) / window_start * 100

    def ma(n):
        if len(closes) < n:
            return None
        return sum(closes[-n:]) / n

    ma60 = ma(MA_QUARTER)
    ma240 = ma(MA_YEAR)
    vs_ma60 = (latest_price - ma60) / ma60 * 100 if ma60 else None
    vs_ma240 = (latest_price - ma240) / ma240 * 100 if ma240 else None

    is_daily_alert = daily_change_pct <= config["daily_threshold"]
    is_multi_day_alert = (
        cumulative_change_pct is not None
        and cumulative_change_pct <= config["multi_day_threshold"]
    )
    drawdown_pct = (latest_price - high_30d) / high_30d * 100

    # 計算 BTC 持倉數據 (包含 TWD 與換算為 USD 的部分)
    btc_holding_data = None
    if BTC_HOLDING and BTC_HOLDING.get("amount", 0) > 0:
        amount = BTC_HOLDING["amount"]
        cost_twd = BTC_HOLDING["cost_twd"]
        value_twd = amount * latest_price
        pnl_twd = value_twd - cost_twd
        
        usdtwd = max_data.get("usdtwd", 32.0)
        cost_usd = cost_twd / usdtwd
        btc_usd = max_data.get("btc_usd") or (latest_price / usdtwd)
        market_value_usd = amount * btc_usd
        pnl_usd = market_value_usd - cost_usd
        pnl_pct = (pnl_usd / cost_usd * 100) if cost_usd else 0.0

        btc_holding_data = {
            "amount": amount,
            "cost_twd": cost_twd,
            "value_twd": value_twd,
            "pnl_twd": pnl_twd,
            "cost_basis": cost_usd,       # USD 成本
            "market_value": market_value_usd, # USD 現值
            "pnl_amount": pnl_usd,         # USD 損益
            "pnl_pct": pnl_pct,
        }

    return {
        "name": "BTC",
        "latest_price": latest_price,
        "prev_price": prev_price,
        "daily_change_pct": daily_change_pct,
        "high_30d": high_30d,
        "high_30d_date": high_30d_date,
        "drawdown_pct": drawdown_pct,
        "cumulative_change_pct": cumulative_change_pct,
        "ma60": ma60,
        "ma240": ma240,
        "vs_ma60": vs_ma60,
        "vs_ma240": vs_ma240,
        "broke_ma60": ((prev_price >= ma60) and (latest_price < ma60)) if ma60 else False,
        "broke_ma240": ((prev_price >= ma240) and (latest_price < ma240)) if ma240 else False,
        "is_alert": is_daily_alert or is_multi_day_alert,
        "is_daily_alert": is_daily_alert,
        "is_multi_day_alert": is_multi_day_alert,
        "is_watch": drawdown_pct <= config["watch_threshold"],
        "daily_threshold": config["daily_threshold"],
        "multi_day_threshold": config["multi_day_threshold"],
        "watch_threshold": config["watch_threshold"],
        "btc_usd": max_data.get("btc_usd"),
        "usdtwd": max_data.get("usdtwd"),
        "holding": btc_holding_data,
    }

def _loss_desc(pnl_pct: float) -> str:
    """依損益幅度自動產生文字描述。"""
    if pnl_pct >= 0:
        if pnl_pct < 3:
            return "賬面微幅浮盈"
        elif pnl_pct < 10:
            return "賬面小幅獲利"
        elif pnl_pct < 25:
            return "賬面中幅獲利"
        else:
            return "賬面大幅獲利"
    else:
        loss = abs(pnl_pct)
        if loss < 3:
            return "賬面僅微幅浮虧"
        elif loss < 10:
            return "賬面小幅浮虧"
        elif loss < 25:
            return "賬面中幅浮虧"
        else:
            return "賬面大幅浮虧"

def format_btc_twd_holding(result: dict) -> str:
    """BTC 台幣持倉明細。"""
    if not result.get("holding"):
        return None
    h = result["holding"]
    price_twd = result["latest_price"]
    avg_price_twd = h["cost_twd"] / h["amount"] if h["amount"] else 0.0
    sign = "🟢" if h["pnl_twd"] >= 0 else "🔴"
    desc = _loss_desc(h["pnl_pct"])

    usd_note = ""
    if result.get("btc_usd"):
        usd_note = f"（折合約 ${result['btc_usd']:,.0f} 美元）"

    return "\n".join([
        f"   持有數量 {h['amount']:.8f} BTC",
        f"   投入成本 NT${h['cost_twd']:,.2f}",
        f"   買入均價 NT${avg_price_twd:,.0f}/BTC",
        f"   市場單價 NT${price_twd:,.0f}/BTC{usd_note}",
        f"   現值 NT${h['value_twd']:,.2f}",
        f"   {sign} 損益 {h['pnl_pct']:+.2f}%（{desc} NT${h['pnl_twd']:+,.2f}）",
    ])

def format_normal(result: dict) -> str:
    """一般日報格式（美股，美元計價）。"""
    name = result["name"]
    daily = result["daily_change_pct"]
    arrow = "📈" if daily >= 0 else "📉"

    lines = [
        f"{arrow} {name} 當日 {daily:+.2f}%",
        f"   收盤 {result['latest_price']:.2f}",
        f"   30日高 {result['high_30d']:.2f}（{result['high_30d_date']}）",
        f"   距高 {result['drawdown_pct']:+.2f}%",
    ]

    ma_line = ""
    if result["ma60"] is not None:
        sign = "下" if result["vs_ma60"] < 0 else "上"
        ma_line += f"季線{sign}{abs(result['vs_ma60']):.1f}%"
    if result["ma240"] is not None:
        if ma_line:
            ma_line += " "
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
        lines.append(f"   {sign} 損益 {h['pnl_amount']:+.2f}（{h['pnl_pct']:+.2f}%）")

    return "\n".join(lines)

def format_btc_merged(result: dict) -> str:
    """BTC 一般日報（台幣計價，資料來自 MAX）。"""
    daily = result["daily_change_pct"]
    arrow = "📈" if daily >= 0 else "📉"

    lines = [
        f"{arrow} BTC 當日 {daily:+.2f}%",
        f"   收盤 NT${result['latest_price']:,.0f}",
        f"   30日高 NT${result['high_30d']:,.0f}",
        f"   距高 {result['drawdown_pct']:+.2f}%",
    ]

    ma_line = ""
    if result["ma60"] is not None:
        sign = "下" if result["vs_ma60"] < 0 else "上"
        ma_line += f"季線{sign}{abs(result['vs_ma60']):.1f}%"
    if result["ma240"] is not None:
        if ma_line:
            ma_line += " "
        sign = "下" if result["vs_ma240"] < 0 else "上"
        ma_line += f"年線{sign}{abs(result['vs_ma240']):.1f}%"
    if ma_line:
        lines.append(f"   {ma_line}")

    holding_block = format_btc_twd_holding(result)
    if holding_block:
        lines.append(holding_block)

    return "\n".join(lines)

def format_total_portfolio(results: list) -> str:
    """資產投資組合總計（包含美股與 BTC，換算 USD 統計）。"""
    held = [r for r in results if r.get("holding")]
    if not held:
        return None

    total_cost = sum(r["holding"]["cost_basis"] for r in held)
    total_value = sum(r["holding"]["market_value"] for r in held)
    total_pnl = total_value - total_cost
    total_pct = (total_pnl / total_cost * 100) if total_cost else 0.0

    big = "🟢" if total_pnl >= 0 else "🔴"
    lines = [
        "💼 總投資組合總計 (含BTC, USD)",
        "─────────────",
        f"總成本 ${total_cost:.2f}",
        f"總現值 ${total_value:.2f}",
        f"{big} 總損益 ${total_pnl:+.2f}（{total_pct:+.2f}%）",
    ]
    return "\n".join(lines)

def build_message(results: list) -> str:
    """組裝完整訊息。"""
    today = datetime.now().strftime("%Y-%m-%d")

    stock_results = [r for r in results if r["name"] != "BTC"]
    btc_result = next((r for r in results if r["name"] == "BTC"), None)

    sections = [f"📊 市場日報 ({today})", "━━━━━━━━━━━━━━━"]

    for r in stock_results:
        sections.append(format_normal(r))

    if btc_result is not None:
        sections.append(format_btc_merged(btc_result))

    # 傳入包含美股與 BTC 的完整 results 計算總計
    portfolio = format_total_portfolio(results)
    if portfolio:
        sections.append("=================")
        sections.append(portfolio)

    return "\n\n".join(sections)

def send_line_message(text: str) -> bool:
    """透過 LINE Messaging API push 訊息。"""
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_IDS:
        print("[錯誤] 未設定 LINE_CHANNEL_ACCESS_TOKEN 或 LINE_USER_IDS")
        print("訊息內容（未發送）：")
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
            print(f"[錯誤] 發送至 {user_id[:10]}... 例外：{e}")

    return success_count > 0

def main():
    print(f"=== 監控執行於 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"通知對象數：{len(LINE_USER_IDS)} 人")

    max_btc = fetch_max_btc_twd()
    if max_btc is not None:
        print(f"MAX BTC/TWD 即時價：{max_btc['last']:,.0f}")

    results = []
    for name, config in TICKERS.items():
        try:
            if name == "BTC":
                r = analyze_btc_twd(config, max_btc) if max_btc else None
                if r is None:
                    print("[警告] MAX BTC/TWD 抓取失敗，略過 BTC")
            else:
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
                if r.get("holding"):
                    pnl_info = f" 損益:{r['holding']['pnl_amount']:+.2f}({r['holding']['pnl_pct']:+.2f}%)"
                print(
                    f"[{status}] {name} 當日 {r['daily_change_pct']:+.2f}%, "
                    f"距高 {r['drawdown_pct']:+.2f}%{ma_info}{pnl_info}"
                )
        except Exception as e:
            print(f"[錯誤] 處理 {name} 時發生例外：{e}")

    if not results:
        print("無任何資料，結束。")
        return

    message = build_message(results)
    print("\n=== 即將發送訊息 ===")
    print(message)
    print("===================\n")

    send_line_message(message)

if __name__ == "__main__":
    main()
