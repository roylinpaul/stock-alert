import os
import schedule
import time
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
HOLDINGS = {
    "QQQ": {"shares": 1.1971, "avg_cost": 668.95},
    "TSLA": {"shares": 3.25416, "avg_cost": 400.676},
    "MRVL": {"shares": 4.00000, "avg_cost": 287.350},
}

# ===== BTC 持倉設定(TWD 損益計算用,與美股分開) =====
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


def fetch_price_data(symbol: str, days: int = 260):
    """抓取最近 N 個交易日的收盤價，增強防錯與即時性。"""
    try:
        ticker = yf.Ticker(symbol)
        # 多抓一些日子確保滾動均線計算安全
        hist = ticker.history(period="2y", interval="1d")
        if hist.empty:
            return None
        
        # 確保最後一筆收盤價不是 NaN (有時剛收盤會是 NaN)
        closes = hist["Close"].dropna()
        if len(closes) > 0 and closes.index[-1].strftime('%Y-%m-%d') == datetime.now().strftime('%Y-%m-%d'):
            # 如果今天還沒完全收盤或數據不全，至少確保有最新的數值
            pass
        return closes
    except Exception as e:
        print(f"[錯誤] 抓取 {symbol} 資料失敗: {e}")
        return None


def fetch_usdtwd():
    """抓 USD/TWD 即時匯率，增加備用代碼提高穩定度。"""
    for ticker_symbol in ["TWD=X", "USDTWD=X"]:
        try:
            fx = yf.Ticker(ticker_symbol).history(period="5d")["Close"].dropna()
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
    closes = fetch_price_data(config["symbol"], days=MA_YEAR + 10)
    if closes is None or len(closes) < 2:
        print(f"[警告] {name} 資料不足,略過")
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
        "holding
