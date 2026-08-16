# app.py (予測シミュレーション＆テーブル追加版)
import re
import time
import urllib.request
from datetime import datetime, timedelta
import pandas as pd
import streamlit as st
import yfinance as yf
from supabase import create_client, Client

# 1. 認証チェック用の関数
def check_password():
    """パスワードが正しいかチェックする関数"""
    if st.session_state.get("password_correct", False):
        return True

    st.subheader("🔒 ログインしてください")
    password = st.text_input("パスワード", type="password")
    
    if st.button("ログイン"):
        if password == "3080":
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("パスワードが違います")
            
    return False

if not check_password():
    st.stop()
    
# ページ設定
st.set_page_config(
    page_title="しばかぶ 🐶 - 株価分析＆ポートフォリオ管理",
    page_icon="🐶",
    layout="wide",
)

# リンク風ボタンのカスタムCSS
st.markdown(
    """
    <style>
    button[data-testid="stBaseButton-tertiary"] {
        text-decoration: underline !important;
        color: #1E88E5 !important;
        font-weight: bold !important;
        padding: 0px !important;
        border: none !important;
    }
    </style>
""",
    unsafe_allow_html=True,
)

# 英語セクター ➔ 日本語セクター変換辞書
SECTOR_MAP_JP = {
    "Technology": "情報・通信",
    "Consumer Cyclical": "一般消費財",
    "Financial Services": "金融・銀行",
    "Healthcare": "ヘルスケア・医薬品",
    "Industrials": "資本財・機械",
    "Communication Services": "通信・サービス",
    "Energy": "エネルギー",
    "Consumer Defensive": "生活必需品・食品",
    "Utilities": "電気・ガス・公益",
    "Real Estate": "不動産",
    "Basic Materials": "素材・化学",
}

# Supabaseクライアントの初期化
def init_supabase() -> Client:
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)

supabase = init_supabase()

# Yahoo!ファイナンス(JP)から日本語銘柄名を自動取得
def fetch_japanese_company_name(code):
    clean_code = code.replace(".T", "").strip()
    url = f"https://finance.yahoo.co.jp/quote/{clean_code}.T"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=4) as response:
            html = response.read().decode("utf-8")
            title_text = ""
            og_match = re.search(
                r'\x3cmeta\s+property="og:title"\s+content="([^"]+)"', html
            )
            if og_match:
                title_text = og_match.group(1)
            else:
                title_match = re.search(
                    r"\x3ctitle[^\x3e]*\x3e(.*?)\x3c/title\x3e",
                    html,
                    re.DOTALL,
                )
                if title_match:
                    title_text = title_match.group(1)

            if "【" in title_text:
                jp_name = title_text.split("【")[0].strip()
                jp_name = re.sub(r"\(株\)|（株）", "", jp_name).strip()
                if (
                    jp_name
                    and not re.search(r"[\x3c\x3e{}\\/]", jp_name)
                    and len(jp_name) < 40
                ):
                    return jp_name
    except Exception:
        pass
    return None

# 市場インデックスの取得
@st.cache_data(ttl=300)
def fetch_market_indices():
    indices = {"^N225": "日経平均", "JPY=X": "米ドル/円", "^GSPC": "S&P 500"}
    results = {}
    for ticker_symbol, label in indices.items():
        try:
            t = yf.Ticker(ticker_symbol)
            hist = t.history(period="5d")
            if len(hist) >= 2:
                latest_close = hist["Close"].iloc[-1]
                prev_close = hist["Close"].iloc[-2]
                change = latest_close - prev_close
                change_pct = (change / prev_close) * 100
                results[label] = {
                    "price": latest_close,
                    "change": change,
                    "change_pct": change_pct,
                }
            elif len(hist) == 1:
                results[label] = {
                    "price": hist["Close"].iloc[-1],
                    "change": 0.0,
                    "change_pct": 0.0,
                }
        except Exception:
            pass
    return results

# RSI計算関数（Wilderの平滑化）
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

# テーマ一覧の取得
@st.cache_data(ttl=60)
def load_themes():
    res = supabase.table("themes").select("*").order("name").execute()
    df = pd.DataFrame(res.data)
    return df

# 銘柄一覧・全指標の取得
@st.cache_data(ttl=60)
def load_companies():
    res_c = supabase.table("companies").select("*").order("ticker").execute()
    df_c = pd.DataFrame(res_c.data)
    if df_c.empty:
        return pd.DataFrame()

    res_ct = supabase.table("company_themes").select("*").order("ticker").execute()
    df_ct = pd.DataFrame(res_ct.data)

    res_t = supabase.table("themes").select("*").order("theme_id").execute()
    df_t = pd.DataFrame(res_t.data)

    if not df_ct.empty and not df_t.empty:
        df_t_map = df_t.set_index("theme_id")["name"].to_dict()
        df_ct["theme_name"] = df_ct["theme_id"].map(df_t_map)
        themes_grouped = df_ct.groupby("ticker", sort=False)["theme_name"].apply(
            lambda x: ", ".join([str(v) for v in x if pd.notna(v)])
        ).reset_index()
        themes_grouped.rename(columns={"theme_name": "themes"}, inplace=True)
        df_c = pd.merge(df_c, themes_grouped, on="ticker", how="left")
    else:
        df_c["themes"] = ""

    df_dp_list = []
    chunk_size = 1000
    offset = 0
    while True:
        res_dp = (
            supabase.table("daily_prices")
            .select("ticker, close, volume, sma_25, sma_75, rsi_14, date")
            .order("ticker", desc=False)
            .order("date", desc=False)
            .range(offset, offset + chunk_size - 1)
            .execute()
        )
        if not res_dp.data:
            break
        df_dp_list.extend(res_dp.data)
        if len(res_dp.data) < chunk_size:
            break
        offset += chunk_size
    
    df_dp = pd.DataFrame(df_dp_list)

    if not df_dp.empty:
        df_dp["date"] = pd.to_datetime(df_dp["date"])
        df_dp = df_dp.sort_values(["ticker", "date"], ascending=[True, True], kind="mergesort")
        
        df_dp["vol_sma_20"] = df_dp.groupby("ticker", sort=False)["volume"].transform(
            lambda x: x.rolling(window=20, min_periods=1).mean()
        )

        idx = df_dp.groupby("ticker", sort=False)["date"].idxmax()
        latest_dp = df_dp.loc[idx].copy()
        
        latest_dp.rename(columns={
            "close": "latest_close",
            "volume": "latest_volume",
            "vol_sma_20": "latest_vol_sma_20",
            "sma_25": "latest_sma_25",
            "sma_75": "latest_sma_75",
            "rsi_14": "latest_rsi"
        }, inplace=True)
        latest_dp.drop(columns=["date"], inplace=True, errors="ignore")

        df_c = df_c.drop(columns=["latest_close", "latest_volume", "latest_vol_sma_20", "latest_sma_25", "latest_sma_75", "latest_rsi"], errors="ignore")
        df_c = pd.merge(df_c, latest_dp, on="ticker", how="left")
    else:
        df_c["latest_close"] = None
        df_c["latest_volume"] = None
        df_c["latest_vol_sma_20"] = None
        df_c["latest_sma_25"] = None
        df_c["latest_sma_75"] = None
        df_c["latest_rsi"] = None

    numeric_cols = [
        "per", "pbr", "roe", "dividend_yield", 
        "latest_close", "latest_volume", "latest_vol_sma_20", 
        "latest_sma_25", "latest_sma_75", "latest_rsi"
    ]
    for col in numeric_cols:
        if col in df_c.columns:
            df_c[col] = pd.to_numeric(df_c[col], errors="coerce")

    if "code" in df_c.columns:
        df_c = df_c.sort_values(by="code", ascending=True, kind="mergesort")

    return df_c

# 単一銘柄の株価データフェッチ＆テクニカル指標計算・DB保存
def update_stock_prices_in_db(ticker, start_date=None, end_date=None):
    if end_date is None:
        end_date = (datetime.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        try:
            dt = datetime.strptime(end_date, "%Y-%m-%d")
            end_date = (dt + timedelta(days=1)).strftime("%Y-%m-%d")
        except Exception:
            end_date = (datetime.today() + timedelta(days=1)).strftime("%Y-%m-%d")

    if start_date is None:
        try:
            res_max = supabase.table("daily_prices").select("date").eq("ticker", ticker).order("date", desc=True).limit(1).execute()
            if res_max.data and len(res_max.data) > 0:
                last_date_str = res_max.data[0]["date"]
                last_dt = datetime.strptime(last_date_str, "%Y-%m-%d")
                start_date = (last_dt + timedelta(days=1)).strftime("%Y-%m-%d")
            else:
                start_date = (datetime.today() - timedelta(days=730)).strftime("%Y-%m-%d")
        except Exception:
            start_date = (datetime.today() - timedelta(days=730)).strftime("%Y-%m-%d")

    try:
        calc_start_dt = datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=150)
        fetch_start_date = calc_start_dt.strftime("%Y-%m-%d")

        df = yf.download(ticker, start=fetch_start_date, end=end_date, progress=False, auto_adjust=False)
        if df.empty:
            return False
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df["change_pct"] = df["Close"].pct_change() * 100
        df["sma_25"] = df["Close"].rolling(window=25).mean()
        df["sma_75"] = df["Close"].rolling(window=75).mean()
        df["rsi_14"] = calculate_rsi(df["Close"], 14)

        exp12 = df["Close"].ewm(span=12, adjust=False).mean()
        exp26 = df["Close"].ewm(span=26, adjust=False).mean()
        df["macd"] = exp12 - exp26
        df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()

        bb_sma = df["Close"].rolling(window=20).mean()
        bb_std = df["Close"].rolling(window=20).std()
        df["bb_upper"] = bb_sma + (bb_std * 2)
        df["bb_lower"] = bb_sma - (bb_std * 2)

        high_low = df["High"] - df["Low"]
        high_close = (df["High"] - df["Close"].shift()).abs()
        low_close = (df["Low"] - df["Close"].shift()).abs()
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df["atr_14"] = true_range.rolling(window=14).mean()

        df_to_save = df.loc[start_date:]
        if df_to_save.empty:
            return True

        records = []
        for date, row in df_to_save.iterrows():
            date_str = date.strftime("%Y-%m-%d")
            def safe_val(val):
                return None if pd.isna(val) else float(val)

            records.append({
                "ticker": ticker,
                "date": date_str,
                "open": safe_val(row.get("Open")),
                "high": safe_val(row.get("High")),
                "low": safe_val(row.get("Low")),
                "close": safe_val(row.get("Close")),
                "change_pct": safe_val(row.get("change_pct")),
                "volume": int(row["Volume"]) if ("Volume" in row and not pd.isna(row["Volume"])) else 0,
                "sma_25": safe_val(row.get("sma_25")),
                "sma_75": safe_val(row.get("sma_75")),
                "rsi_14": safe_val(row.get("rsi_14")),
                "macd": safe_val(row.get("macd")),
                "macd_signal": safe_val(row.get("macd_signal")),
                "bb_upper": safe_val(row.get("bb_upper")),
                "bb_lower": safe_val(row.get("bb_lower")),
                "atr_14": safe_val(row.get("atr_14")),
            })

        for i in range(0, len(records), 500):
            batch = records[i:i+500]
            supabase.table("daily_prices").upsert(batch, on_conflict="ticker,date").execute()
            
        info = yf.Ticker(ticker).info
        current_price = info.get("currentPrice") or info.get("regularMarketPrice")
        per = info.get("trailingPE") or info.get("forwardPE")
        pbr = info.get("priceToBook")
        
        dividend_rate = info.get("dividendRate")
        if dividend_rate and current_price:
            div_yield = (dividend_rate / current_price) * 100
        else:
            raw_div = info.get("dividendYield")
            div_yield = (
                (raw_div * 100 if raw_div and raw_div < 1.0 else raw_div)
                if raw_div
                else None
            )

        raw_roe = info.get("returnOnEquity")
        roe = raw_roe * 100 if raw_roe is not None else None

        metrics_data = {}
        if per is not None: metrics_data["per"] = per
        if pbr is not None: metrics_data["pbr"] = pbr
        if div_yield is not None: metrics_data["dividend_yield"] = div_yield
        if roe is not None: metrics_data["roe"] = roe

        if metrics_data:
            supabase.table("companies").update(metrics_data).eq("ticker", ticker).execute()
            
    except Exception as e:
        return False
        
    return True

# 全銘柄の株価および指標を一括更新
def refresh_all_stocks_data_smart():
    res = supabase.table("companies").select("ticker, code, name").order("ticker").execute()
    tickers_data = res.data
    if not tickers_data:
        return 0, 0, "登録されている銘柄がありません。", []

    res_dp = supabase.table("daily_prices").select("ticker, date").order("ticker").order("date").execute()
    df_dp = pd.DataFrame(res_dp.data)
    
    max_date_dict = {}
    if not df_dp.empty:
        max_dates = df_dp.groupby("ticker", sort=False)["date"].max().reset_index()
        max_date_dict = dict(zip(max_dates["ticker"], max_dates["date"]))

    sorted_tickers = []
    for item in tickers_data:
        ticker = item["ticker"]
        last_date = max_date_dict.get(ticker, "1970-01-01")
        sorted_tickers.append({
            "ticker": ticker,
            "code": item["code"],
            "name": item["name"],
            "last_date": last_date
        })

    sorted_tickers = sorted(sorted_tickers, key=lambda x: x["last_date"])
    tickers_list = [x["ticker"] for x in sorted_tickers]

    success_count = 0
    failed_items = []
    chunk_size = 50
    total_tickers = len(tickers_list)

    for i in range(0, total_tickers, chunk_size):
        chunk = tickers_list[i:i+chunk_size]
        for ticker in chunk:
            target_info = next((x for x in sorted_tickers if x["ticker"] == ticker), None)
            display_name = f"{target_info['code']}: {target_info['name']}" if target_info else ticker
            try:
                success = update_stock_prices_in_db(ticker, start_date=None)
                if success:
                    success_count += 1
                else:
                    failed_items.append(display_name)
            except Exception:
                failed_items.append(display_name)

        if i + chunk_size < total_tickers:
            time.sleep(8)

    st.cache_data.clear()
    return success_count, total_tickers, f"全 {total_tickers} 銘柄中 {success_count} 銘柄のデータを更新しました。", failed_items

# 新規銘柄登録 ＆ データ取得
def register_and_fetch_stock(code_input, custom_name="", custom_sector=""):
    clean_code = code_input.strip().upper()
    ticker = f"{clean_code}.T" if not clean_code.endswith(".T") else clean_code
    code = clean_code.replace(".T", "")

    existing_res = supabase.table("companies").select("name").or_(f"ticker.eq.{ticker},code.eq.{code}").execute()
    existing_df = pd.DataFrame(existing_res.data)
    if not existing_df.empty:
        return (
            False,
            f"⚠️ 銘柄コード【{code}】（{existing_df['name'].iloc[0]}）はすでに登録されています。",
        )

    yf_obj = yf.Ticker(ticker)
    try:
        info = yf_obj.info
    except Exception:
        info = {}

    name = custom_name.strip()
    if not name:
        jp_name = fetch_japanese_company_name(code)
        if jp_name:
            name = jp_name
        else:
            name = (
                info.get("shortName")
                or info.get("longName")
                or f"銘柄 {code}"
            )

    raw_sector = info.get("sector", "未分類")
    sector = (
        custom_sector.strip()
        if custom_sector.strip()
        else SECTOR_MAP_JP.get(raw_sector, raw_sector)
    )

    current_price = info.get("currentPrice") or info.get("regularMarketPrice")
    per = info.get("trailingPE") or info.get("forwardPE")
    pbr = info.get("priceToBook")

    dividend_rate = info.get("dividendRate")
    if dividend_rate and current_price:
        div_yield = (dividend_rate / current_price) * 100
    else:
        raw_div = info.get("dividendYield")
        div_yield = (
            (raw_div * 100 if raw_div and raw_div < 1.0 else raw_div)
            if raw_div
            else 0.0
        )

    raw_roe = info.get("returnOnEquity")
    roe = raw_roe * 100 if raw_roe is not None else None

    company_data = {
        "ticker": ticker,
        "code": code,
        "name": name,
        "sector": sector,
        "per": per,
        "pbr": pbr,
        "dividend_yield": div_yield,
        "roe": roe,
    }
    
    supabase.table("companies").upsert(company_data, on_conflict="ticker").execute()

    end_date = datetime.today().strftime("%Y-%m-%d")
    start_date = (datetime.today() - timedelta(days=730)).strftime("%Y-%m-%d")
    success = update_stock_prices_in_db(ticker, start_date=start_date, end_date=end_date)
    if not success:
        return False, f"⚠️ '{ticker}' の株価データを取得できませんでした。"

    st.cache_data.clear()
    return True, f"🎉 【{name} ({code})】 を登録し、データを読み込みました！"

# ポートフォリオ計算ロジック
def calculate_portfolio_and_summary():
    res_tx = supabase.table("transactions").select("transaction_id, ticker, type, trade_date, price, quantity, memo").order("trade_date").order("transaction_id").execute()
    tx_raw = res_tx.data
    if not tx_raw:
        return pd.DataFrame(), 0.0, 0.0, 0.0, pd.DataFrame()

    tx_df = pd.DataFrame(tx_raw)
    
    res_c = supabase.table("companies").select("ticker, code, name").order("ticker").execute()
    c_df = pd.DataFrame(res_c.data)
    
    if not c_df.empty:
        tx_df = pd.merge(tx_df, c_df[["ticker", "code", "name"]], on="ticker", how="left")
    else:
        tx_df["code"] = ""
        tx_df["name"] = ""

    res_dp = supabase.table("daily_prices").select("ticker, close, date").order("ticker").order("date").execute()
    df_dp = pd.DataFrame(res_dp.data)
    
    price_dict = {}
    if not df_dp.empty:
        df_dp["date"] = pd.to_datetime(df_dp["date"])
        idx = df_dp.groupby("ticker", sort=False)["date"].idxmax()
        latest_prices = df_dp.loc[idx]
        price_dict = dict(zip(latest_prices["ticker"], latest_prices["close"]))

    portfolio = {}

    tx_df["price"] = pd.to_numeric(tx_df["price"], errors="coerce").fillna(0.0)
    tx_df["quantity"] = pd.to_numeric(tx_df["quantity"], errors="coerce").fillna(0.0)

    for _, row in tx_df.iterrows():
        ticker = row["ticker"]
        t_type = row["type"]
        
        try:
            price = float(row["price"])
        except (ValueError, TypeError):
            price = 0.0

        try:
            qty = float(row["quantity"])
        except (ValueError, TypeError):
            qty = 0.0

        if ticker not in portfolio:
            portfolio[ticker] = {
                "code": row.get("code", ""),
                "name": row.get("name", ticker),
                "qty": 0.0,
                "total_cost": 0.0,
                "realized_pnl": 0.0,
            }

        p = portfolio[ticker]
        if t_type == "BUY":
            p["qty"] += qty
            p["total_cost"] += price * qty
        elif t_type == "SELL":
            if p["qty"] > 0:
                avg_price = p["total_cost"] / p["qty"] if p["qty"] > 0 else 0.0
                p["realized_pnl"] += (price - avg_price) * qty
                p["qty"] -= qty
                p["total_cost"] -= avg_price * qty
                if p["qty"] <= 0:
                    p["qty"] = 0.0
                    p["total_cost"] = 0.0

    portfolio_rows = []
    total_investment = 0.0
    total_current_value = 0.0
    total_realized_pnl = 0.0

    for ticker, data in portfolio.items():
        total_realized_pnl += data["realized_pnl"]
        if data["qty"] > 0:
            avg_price = data["total_cost"] / data["qty"]
            current_price = price_dict.get(ticker, avg_price)
            if pd.isna(current_price):
                current_price = avg_price

            current_value = current_price * data["qty"]
            unrealized_pnl = current_value - data["total_cost"]
            unrealized_pnl_pct = (
                (unrealized_pnl / data["total_cost"] * 100)
                if data["total_cost"] > 0
                else 0.0
            )

            total_investment += data["total_cost"]
            total_current_value += current_value

            p5_price = avg_price * 1.05
            p5_val = data["total_cost"] * 1.05

            p10_price = avg_price * 1.10
            p10_val = data["total_cost"] * 1.10

            m3_price = avg_price * 0.97
            m3_val = data["total_cost"] * 0.97

            m5_price = avg_price * 0.95
            m5_val = data["total_cost"] * 0.95

            portfolio_rows.append(
                {
                    "銘柄コード": data["code"],
                    "銘柄名": data["name"],
                    "保有株数": data["qty"],
                    "平均取得単価": round(avg_price, 1),
                    "現在値": round(current_price, 1),
                    "取得総額": round(data["total_cost"], 0),
                    "現在評価額": round(current_value, 0),
                    "評価損益(円)": round(unrealized_pnl, 0),
                    "評価損益(%)": round(unrealized_pnl_pct, 2),
                    "+5%株価": round(p5_price, 1),
                    "+5%評価額": round(p5_val, 0),
                    "+10%株価": round(p10_price, 1),
                    "+10%評価額": round(p10_val, 0),
                    "-3%株価": round(m3_price, 1),
                    "-3%評価額": round(m3_val, 0),
                    "-5%株価": round(m5_price, 1),
                    "-5%評価額": round(m5_val, 0),
                }
            )

    portfolio_df = pd.DataFrame(portfolio_rows)
    return (
        portfolio_df,
        total_investment,
        total_current_value,
        total_realized_pnl,
        tx_df,
    )

# ----------------------------------------------------
# Session State 初期化 ＆ ページ遷移の安全な管理
# ----------------------------------------------------
if "selected_stock_label" not in st.session_state:
    st.session_state["selected_stock_label"] = None

if "current_page" not in st.session_state:
    st.session_state["current_page"] = "📈 株価・テクニカル分析"

# ----------------------------------------------------
# 画面共通：ヘッダー（市場インデックス）表示
# ----------------------------------------------------
market_data = fetch_market_indices()
if market_data:
    cols = st.columns(len(market_data))
    for i, (name, data) in enumerate(market_data.items()):
        val_str = f"{data['price']:,.2f}"
        if name in ["米ドル/円", "日経平均"]:
            val_str += " 円"
        delta_str = f"{data['change']:+.2f} ({data['change_pct']:+.2f}%)"
        cols[i].metric(label=f"🌐 {name}", value=val_str, delta=delta_str)

st.divider()

# ----------------------------------------------------
# サイドバー設定
# ----------------------------------------------------
pages_list = [
    "📈 株価・テクニカル分析",
    "🔍 詳細検索（スクリーナー）",
    "🔥 上昇トレンド検知",
    "💼 ポートフォリオ＆売買管理",
    "⚙️ 銘柄登録・管理",
]

st.sidebar.title("🐶 しばかぶ メニュー")

if st.sidebar.button("🔄 キャッシュクリア＆全データ再読込"):
    st.cache_data.clear()
    st.sidebar.success("キャッシュをクリアしました！")
    st.rerun()

if st.sidebar.button("🔄 全銘柄の株価・指標を更新"):
    with st.spinner("古い順に50件ずつ差分データを更新中（レートリミット回避のため少し時間がかかります）..."):
        succ_cnt, tot_cnt, msg, failed_list = refresh_all_stocks_data_smart()
        st.sidebar.success(msg)
        if failed_list:
            st.sidebar.warning(f"⚠️ 以下の {len(failed_list)} 件の更新に失敗しました:\n" + ", ".join(failed_list))
        st.rerun()

try:
    default_idx = pages_list.index(st.session_state["current_page"])
except ValueError:
    default_idx = 0

selected_menu = st.sidebar.radio(
    "機能を選択:",
    pages_list,
    index=default_idx,
    key="nav_radio_widget",
)

if selected_menu != st.session_state["current_page"]:
    st.session_state["current_page"] = selected_menu
    st.rerun()

page = st.session_state["current_page"]

companies_df = load_companies()
themes_df = load_themes()

# ----------------------------------------------------
# 画面 1: 株価・テクニカル分析
# ----------------------------------------------------
if page == "📈 株価・テクニカル分析":
    st.title("📈 株価＆テクニカル分析ダッシュボード")

    if companies_df.empty:
        st.info(
            "登録されている銘柄がありません。「⚙️ 銘柄登録・管理」メニューから銘柄を追加してください。"
        )
    else:
        theme_list = ["全テーマ"] + themes_df["name"].tolist() if not themes_df.empty else ["全テーマ"]
        selected_theme_filter = st.sidebar.selectbox(
            "🏷️ テーマで絞り込み", theme_list
        )

        filtered_companies = companies_df.copy()
        selected_label_state = st.session_state.get("selected_stock_label")

        if selected_theme_filter != "全テーマ":
            if selected_label_state:
                selected_row = companies_df[companies_df.apply(lambda r: f"{r['code']}: {r['name']}" == selected_label_state, axis=1)]
                filtered_by_theme = filtered_companies[
                    filtered_companies["themes"]
                    .fillna("")
                    .str.contains(selected_theme_filter)
                ]
                if not selected_row.empty and selected_row.iloc[0]["ticker"] not in filtered_by_theme["ticker"].values:
                    filtered_companies = pd.concat([selected_row, filtered_by_theme]).drop_duplicates(subset=["ticker"])
                else:
                    filtered_companies = filtered_by_theme
            else:
                filtered_companies = filtered_companies[
                    filtered_companies["themes"]
                    .fillna("")
                    .str.contains(selected_theme_filter)
                ]

        if filtered_companies.empty:
            st.warning(
                f"テーマ '{selected_theme_filter}' に該当する銘柄はありません。"
            )
        else:
            st.sidebar.markdown("### 🔍 銘柄検索・選択")
            search_query = st.sidebar.text_input(
                "コード・銘柄名・セクターで検索",
                value="",
                placeholder="例: 7203, トヨタ, 半導体 など",
                help="日本語、英語、数字のどれでも部分一致で絞り込めます"
            )

            if search_query.strip():
                query_lower = search_query.strip().lower()
                mask = (
                    filtered_companies["code"].astype(str).str.lower().str.contains(query_lower) |
                    filtered_companies["name"].astype(str).str.lower().str.contains(query_lower) |
                    filtered_companies["sector"].astype(str).str.lower().str.contains(query_lower) |
                    filtered_companies["themes"].astype(str).str.lower().str.contains(query_lower)
                )
                searched_companies = filtered_companies[mask]
                if selected_label_state:
                    selected_row = companies_df[companies_df.apply(lambda r: f"{r['code']}: {r['name']}" == selected_label_state, axis=1)]
                    if not selected_row.empty and selected_row.iloc[0]["ticker"] not in searched_companies["ticker"].values:
                        searched_companies = pd.concat([selected_row, searched_companies]).drop_duplicates(subset=["ticker"])
            else:
                searched_companies = filtered_companies

            if selected_label_state:
                selected_row_all = companies_df[companies_df.apply(lambda r: f"{r['code']}: {r['name']}" == selected_label_state, axis=1)]
                if not selected_row_all.empty and selected_row_all.iloc[0]["ticker"] not in searched_companies["ticker"].values:
                    searched_companies = pd.concat([selected_row_all, searched_companies]).drop_duplicates(subset=["ticker"])

            if searched_companies.empty:
                st.sidebar.warning("一致する銘柄が見つかりませんでした。")
                st.stop()

            company_options = {
                f"{row['code']}: {row['name']}": row["ticker"]
                for _, row in searched_companies.iterrows()
            }
            option_keys = list(company_options.keys())

            if st.session_state["selected_stock_label"] not in option_keys:
                if option_keys:
                    st.session_state["selected_stock_label"] = option_keys[0]

            selected_label = st.sidebar.selectbox(
                "絞り込み結果から選択", option_keys, key="selected_stock_label"
            )
            selected_ticker = company_options[selected_label]

            company_info = companies_df[
                companies_df["ticker"] == selected_ticker
            ].iloc[0]

            if st.button("🔄 この銘柄のデータを最新に更新（全量取得）"):
                with st.spinner(f"【{company_info['name']}】のデータを全量取得中..."):
                    end_date = datetime.today().strftime("%Y-%m-%d")
                    start_date = (datetime.today() - timedelta(days=730)).strftime("%Y-%m-%d")
                    ok = update_stock_prices_in_db(selected_ticker, start_date=start_date, end_date=end_date)
                    if ok:
                        st.success(f"【{company_info['name']}】のデータを更新しました！")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error("データの更新に失敗しました。")

            res_p = supabase.table("daily_prices").select("date, open, high, low, close, change_pct, volume, sma_25, sma_75, rsi_14").eq("ticker", selected_ticker).order("date").execute()
            df_prices = pd.DataFrame(res_p.data)

            for col in ["close", "sma_25", "sma_75", "rsi_14", "change_pct", "volume"]:
                if col in df_prices.columns:
                    df_prices[col] = pd.to_numeric(df_prices[col], errors="coerce")

            if not df_prices.empty:
                latest = df_prices.iloc[-1]
                themes_str = (
                    f" / テーマ: {company_info['themes']}"
                    if pd.notna(company_info["themes"]) and company_info["themes"] != ""
                    else ""
                )
                st.subheader(
                    f"{company_info['name']} （コード: {company_info['code']} / セクター: {company_info['sector']}{themes_str}）"
                )

                col1, col2, col3, col4, col5, col6 = st.columns(6)
                delta_str = (
                    f"{latest['change_pct']:+.2f}%"
                    if pd.notna(latest["change_pct"])
                    else None
                )
                col1.metric(
                    "最新終値", f"{latest['close']:,.1f} 円", delta=delta_str
                )
                rsi_val = (
                    f"{latest['rsi_14']:.1f}"
                    if pd.notna(latest["rsi_14"])
                    else "-"
                )
                col2.metric("RSI (14日)", f"{rsi_val}")
                per_disp = (
                    f"{company_info['per']:.1f} 倍"
                    if pd.notna(company_info["per"])
                    else "-"
                )
                col3.metric("PER (実績)", per_disp)
                pbr_disp = (
                    f"{company_info['pbr']:.2f} 倍"
                    if pd.notna(company_info["pbr"])
                    else "-"
                )
                col4.metric("PBR", pbr_disp)
                roe_disp = (
                    f"{company_info['roe']:.1f} %"
                    if pd.notna(company_info["roe"])
                    else "-"
                )
                col5.metric("ROE", roe_disp)
                div_disp = (
                    f"{company_info['dividend_yield']:.2f} %"
                    if pd.notna(company_info["dividend_yield"])
                    else "-"
                )
                col6.metric("配当利回り", div_disp)

                st.divider()

                period_option = st.radio(
                    "📅 表示期間切り替え:",
                    ["1ヶ月", "3ヶ月", "6ヶ月", "1年", "全期間"],
                    index=2,
                    horizontal=True,
                )
                period_map = {
                    "1ヶ月": 21,
                    "3ヶ月": 63,
                    "6ヶ月": 126,
                    "1年": 252,
                    "全期間": len(df_prices),
                }

                limit_days = period_map.get(period_option, len(df_prices))
                df_chart = df_prices.tail(limit_days).copy()
                df_chart["date_jp"] = pd.to_datetime(
                    df_chart["date"]
                ).dt.strftime("%Y/%m/%d")
                df_chart.set_index("date_jp", inplace=True)

                tab1, tab2, tab3, tab4 = st.tabs(
                    ["📈 株価＆移動平均線", "📊 RSI指標", "🔮 予測シミュレーション", "📄 過去データ一覧"]
                )
                with tab1:
                    chart_data = df_chart[["close", "sma_25", "sma_75"]].rename(
                        columns={
                            "close": "終値",
                            "sma_25": "25日移動平均",
                            "sma_75": "75日移動平均",
                        }
                    )
                    st.line_chart(chart_data)

                with tab2:
                    rsi_chart = df_chart[["rsi_14"]].rename(
                        columns={"rsi_14": "RSI(14日)"}
                    )
                    st.line_chart(rsi_chart)

                with tab3:
                    st.markdown("### 🔮 ボラティリティ＆トレンドベースの将来予測シミュレーション（今後20営業日）")
                    st.markdown("直近の価格トレンドの傾きと、ボラティリティ（標準偏差）を基に、今後20営業日（約1ヶ月）の値動きの目安（強気・標準・弱気シナリオ）をシミュレーションします。")
                    
                    sim_df = df_prices.tail(60).copy()
                    if len(sim_df) > 10:
                        last_date = pd.to_datetime(sim_df["date"].iloc[-1])
                        last_close = sim_df["close"].iloc[-1]
                        
                        recent_20 = sim_df.tail(20)
                        if len(recent_20) > 1:
                            price_change_rate = (recent_20["close"].iloc[-1] / recent_20["close"].iloc[0]) - 1
                            daily_trend = price_change_rate / len(recent_20)
                        else:
                            daily_trend = 0.0
                            
                        daily_vol = sim_df["close"].pct_change().std() * last_close
                        if pd.isna(daily_vol) or daily_vol == 0:
                            daily_vol = last_close * 0.01

                        future_dates = pd.bdate_range(start=last_date + timedelta(days=1), periods=20)
                        future_rows = []
                        curr_p = last_close
                        
                        for i, f_date in enumerate(future_dates, start=1):
                            curr_p = curr_p * (1 + daily_trend)
                            spread = daily_vol * (i ** 0.5)
                            
                            future_rows.append({
                                "date": f_date,
                                "日付": f_date.strftime("%Y/%m/%d"),
                                "予測(標準)": curr_p,
                                "強気シナリオ(+1σ)": curr_p + spread,
                                "弱気シナリオ(-1σ)": curr_p - spread,
                            })
                            
                        future_df = pd.DataFrame(future_rows)
                        
                        # チャート用インデックス設定
                        chart_future_df = future_df.copy()
                        chart_future_df.set_index("日付", inplace=True)
                        
                        chart_history = sim_df.copy()
                        chart_history["date_dt"] = pd.to_datetime(chart_history["date"])
                        chart_history["date_jp"] = chart_history["date_dt"].dt.strftime("%Y/%m/%d")
                        chart_history.set_index("date_jp", inplace=True)
                        
                        actual_series = chart_history["close"].rename("実績終値")
                        
                        plot_df = pd.DataFrame(index=pd.concat([chart_history.index.to_series(), chart_future_df.index.to_series()]).unique())
                        plot_df["実績終値"] = actual_series
                        plot_df["予測(標準)"] = chart_future_df["予測(標準)"]
                        plot_df["強気シナリオ(+1σ)"] = chart_future_df["強気シナリオ(+1σ)"]
                        plot_df["弱気シナリオ(-1σ)"] = chart_future_df["弱気シナリオ(-1σ)"]
                        
                        last_idx = chart_history.index[-1]
                        plot_df.loc[last_idx, "予測(標準)"] = last_close
                        plot_df.loc[last_idx, "強気シナリオ(+1σ)"] = last_close
                        plot_df.loc[last_idx, "弱気シナリオ(-1σ)"] = last_close
                        
                        st.line_chart(plot_df)
                        st.info("💡 **見方**: 実績終値のトレンド（過去20日の傾き）を延長した中心線に対し、時間の経過に伴うリスク（変動幅）を上下のバンド（±1σ）で表しています。")

                        # --- 追加：予測データテーブル ---
                        st.markdown("### 📋 予測数値詳細（今後20営業日）")
                        display_future_df = future_df.copy()
                        st.dataframe(
                            display_future_df[["日付", "強気シナリオ(+1σ)", "予測(標準)", "弱気シナリオ(-1σ)"]].style.format({
                                "強気シナリオ(+1σ)": "{:,.1f}円",
                                "予測(標準)": "{:,.1f}円",
                                "弱気シナリオ(-1σ)": "{:,.1f}円"
                            }),
                            use_container_width=True
                        )
                    else:
                        st.warning("予測に必要な十分なデータ履歴がありません。")

                with tab4:
                    jp_prices_df = df_prices.copy()
                    jp_prices_df["date"] = pd.to_datetime(
                        jp_prices_df["date"]
                    ).dt.strftime("%Y年%m月%d日")
                    jp_prices_df = jp_prices_df.rename(
                        columns={
                            "date": "日付",
                            "open": "始値",
                            "high": "高値",
                            "low": "安値",
                            "close": "終値",
                            "change_pct": "前日比(%)",
                            "volume": "出来高",
                            "sma_25": "25日移動平均",
                            "sma_75": "75日移動平均",
                            "rsi_14": "RSI(14日)",
                        }
                    )
                    st.dataframe(
                        jp_prices_df.sort_values(by="日付", ascending=False),
                        use_container_width=True,
                    )

# ----------------------------------------------------
# 画面 2: 🔍 詳細検索（スクリーナー）
# ----------------------------------------------------
elif page == "🔍 詳細検索（スクリーナー）":
    st.title("🔍 条件絞り込み検索（銘柄スクリーナー）")
    st.markdown(
        "ファンダメンタル指標やテクニカル指標の条件を指定して、お宝銘柄をスクリーニングできます。"
    )

    if companies_df.empty:
        st.info("登録銘柄がありません。まずは銘柄を追加してください。")
    else:
        res_df = companies_df.copy()

        for col in ["per", "pbr", "roe", "dividend_yield", "latest_close", "latest_volume", "latest_sma_25", "latest_sma_75", "latest_rsi"]:
            if col in res_df.columns:
                res_df[col] = pd.to_numeric(res_df[col], errors="coerce")

        st.subheader("⚙️ 検索条件の設定")

        c1, c2, c3, c4 = st.columns(4)
        use_per = c1.checkbox("PER 上限（倍）", value=True)
        per_max = c1.number_input(
            "PER 上限",
            min_value=0.0,
            value=15.0,
            step=1.0,
            disabled=not use_per,
            label_visibility="collapsed",
        )

        use_pbr = c2.checkbox("PBR 上限（倍）", value=True)
        pbr_max = c2.number_input(
            "PBR 上限",
            min_value=0.0,
            value=1.0,
            step=0.1,
            disabled=not use_pbr,
            label_visibility="collapsed",
        )

        use_roe = c3.checkbox("ROE 下限（%）", value=True)
        roe_min = c3.number_input(
            "ROE 下限",
            min_value=0.0,
            value=8.0,
            step=1.0,
            disabled=not use_roe,
            label_visibility="collapsed",
        )

        use_div = c4.checkbox("配当利回り 下限（%）", value=False)
        div_min = c4.number_input(
            "配当利回り 下限",
            min_value=0.0,
            value=3.0,
            step=0.5,
            disabled=not use_div,
            label_visibility="collapsed",
        )

        c5, c6, c7, c8 = st.columns(4)
        use_rsi = c5.checkbox("RSI(14日) 上限", value=False)
        rsi_max = c5.number_input(
            "RSI 上限",
            min_value=0.0,
            max_value=100.0,
            value=30.0,
            step=5.0,
            disabled=not use_rsi,
            label_visibility="collapsed",
        )

        use_vol = c6.checkbox("直近出来高 下限（株）", value=True)
        volume_min = c6.number_input(
            "出来高 下限",
            min_value=0,
            value=10000,
            step=10000,
            disabled=not use_vol,
            label_visibility="collapsed",
        )

        use_ma = c7.checkbox("移動平均線 条件", value=False)
        ma_condition = c7.selectbox(
            "移動平均線との位置関係",
            [
                "25日線より上（短期上昇傾向）",
                "75日線より上（中期上昇傾向）",
                "25日線・75日線の両方より上",
            ],
            disabled=not use_ma,
            label_visibility="collapsed",
        )

        use_sector = c8.checkbox("セクター指定", value=False)
        sectors = sorted(
            [
                s
                for s in res_df["sector"].dropna().unique()
                if s != "未分類"
            ]
        )
        selected_sector = c8.selectbox(
            "セクター",
            sectors if sectors else ["未分類"],
            disabled=not use_sector,
            label_visibility="collapsed",
        )

        c9, c10 = st.columns([1, 3])
        use_theme = c9.checkbox("テーマ指定", value=False)
        themes_list = themes_df["name"].tolist() if not themes_df.empty else []
        selected_theme = c10.selectbox(
            "テーマ",
            themes_list if themes_list else ["なし"],
            disabled=not use_theme,
            label_visibility="collapsed",
        )

        if use_per and per_max > 0:
            res_df = res_df[
                (res_df["per"].notna()) & (res_df["per"] <= per_max)
            ]
        if use_pbr and pbr_max > 0:
            res_df = res_df[
                (res_df["pbr"].notna()) & (res_df["pbr"] <= pbr_max)
            ]
        if use_roe and roe_min > 0:
            res_df = res_df[
                (res_df["roe"].notna()) & (res_df["roe"] >= roe_min)
            ]
        if use_div and div_min > 0:
            res_df = res_df[
                (res_df["dividend_yield"].notna())
                & (res_df["dividend_yield"] >= div_min)
            ]
        if use_rsi and rsi_max > 0:
            res_df = res_df[
                (res_df["latest_rsi"].notna())
                & (res_df["latest_rsi"] <= rsi_max)
            ]
        if use_vol and volume_min > 0:
            res_df = res_df[
                (res_df["latest_volume"].notna())
                & (res_df["latest_volume"] >= volume_min)
            ]

        if use_ma:
            if ma_condition == "25日線より上（短期上昇傾向）":
                res_df = res_df[
                    (res_df["latest_close"].notna())
                    & (res_df["latest_sma_25"].notna())
                    & (res_df["latest_close"] > res_df["latest_sma_25"])
                ]
            elif ma_condition == "75日線より上（中期上昇傾向）":
                res_df = res_df[
                    (res_df["latest_close"].notna())
                    & (res_df["latest_sma_75"].notna())
                    & (res_df["latest_close"] > res_df["latest_sma_75"])
                ]
            elif ma_condition == "25日線・75日線の両方より上":
                res_df = res_df[
                    (res_df["latest_close"].notna())
                    & (res_df["latest_sma_25"].notna())
                    & (res_df["latest_sma_75"].notna())
                    & (res_df["latest_close"] > res_df["latest_sma_25"])
                    & (res_df["latest_close"] > res_df["latest_sma_75"])
                ]

        if use_sector and selected_sector:
            res_df = res_df[res_df["sector"] == selected_sector]
        if use_theme and selected_theme and selected_theme != "なし":
            res_df = res_df[
                res_df["themes"].fillna("").str.contains(selected_theme)
            ]

        st.divider()
        st.subheader(f"📋 該当銘柄一覧 ({len(res_df)} 件)")

        if not res_df.empty:
            cols = st.columns([1, 2.5, 1.2, 1, 1, 1, 1.2, 1, 1.2, 1.5])
            cols[0].markdown("**コード**")
            cols[1].markdown("**銘柄名**")
            cols[2].markdown("**現在値**")
            cols[3].markdown("**PER**")
            cols[4].markdown("**PBR**")
            cols[5].markdown("**ROE**")
            cols[6].markdown("**配当利回り**")
            cols[7].markdown("**RSI**")
            cols[8].markdown("**出来高**")
            cols[9].markdown("**セクター**")
            st.divider()

            for idx, row in res_df.iterrows():
                cols = st.columns([1, 2.5, 1.2, 1, 1, 1, 1.2, 1, 1.2, 1.5])
                cols[0].write(f"{row['code']}")

                if cols[1].button(
                    f"🔗 {row['name']}",
                    key=f"btn_nav_scr_{row['code']}",
                    type="tertiary",
                ):
                    st.session_state["selected_stock_label"] = (
                        f"{row['code']}: {row['name']}"
                    )
                    st.session_state["current_page"] = "📈 株価・テクニカル分析"
                    st.rerun()

                cols[2].write(
                    f"{row['latest_close']:,.1f}円"
                    if pd.notna(row["latest_close"])
                    else "-"
                )
                cols[3].write(
                    f"{row['per']:.1f}倍" if pd.notna(row["per"]) else "-"
                )
                cols[4].write(
                    f"{row['pbr']:.2f}倍" if pd.notna(row["pbr"]) else "-"
                )
                cols[5].write(
                    f"{row['roe']:.1f}%" if pd.notna(row["roe"]) else "-"
                )
                cols[6].write(
                    f"{row['dividend_yield']:.2f}%"
                    if pd.notna(row["dividend_yield"])
                    else "-"
                )
                cols[7].write(
                    f"{row['latest_rsi']:.1f}"
                    if pd.notna(row["latest_rsi"])
                    else "-"
                )
                cols[8].write(
                    f"{int(row['latest_volume']):,}株"
                    if pd.notna(row["latest_volume"])
                    else "-"
                )
                cols[9].write(
                    f"{row['sector']}" if pd.notna(row["sector"]) else "-"
                )
        else:
            st.warning("条件に該当する銘柄が見つかりませんでした。")

# ----------------------------------------------------
# 画面 3: 🔥 上昇トレンド検知
# ----------------------------------------------------
elif page == "🔥 上昇トレンド検知":
    st.title("🔥 上昇トレンド検知ダッシュボード")
    st.markdown("設定したトレンド定義に基づいて、今勢いのある銘柄を自動で検知します。（最新DB参照）")

    if companies_df.empty:
        st.info("登録されている銘柄がありません。")
    else:
        base_df = companies_df.copy()
        base_df["vol_ratio"] = base_df.apply(
            lambda r: r["latest_volume"] / r["latest_vol_sma_20"] 
            if pd.notna(r.get("latest_volume")) and pd.notna(r.get("latest_vol_sma_20")) and r["latest_vol_sma_20"] > 0 
            else None,
            axis=1
        )
        base_df["disp_25"] = base_df.apply(
            lambda r: ((r["latest_close"] - r["latest_sma_25"]) / r["latest_sma_25"]) * 100
            if pd.notna(r.get("latest_close")) and pd.notna(r.get("latest_sma_25")) and r["latest_sma_25"] > 0
            else None,
            axis=1
        )

        t1, t2, t3, t4, t5 = st.tabs([
            "① 移動平均線（MA）の順序ベース",
            "② おすすめ総合定義（トレンド＆モメンタム型）",
            "③ 情報通信系上昇トレンド",
            "④ 底値からの脱出？",
            "⑤ ヘルスケア系上昇トレンド"
        ])

        with t1:
            st.markdown("### 【① 移動平均線（MA）の順序ベース】")
            filtered_trend_df1 = base_df[
                (base_df["latest_close"].notna()) &
                (base_df["latest_sma_25"].notna()) &
                (base_df["latest_sma_75"].notna()) &
                (base_df["latest_close"] > base_df["latest_sma_25"]) &
                (base_df["latest_sma_25"] > base_df["latest_sma_75"])
            ].copy()

            st.subheader(f"🚀 検知された上昇トレンド銘柄 ({len(filtered_trend_df1)} 件)")
            if not filtered_trend_df1.empty:
                display_df1 = pd.DataFrame({
                    "コード": filtered_trend_df1["code"],
                    "銘柄名": filtered_trend_df1["name"],
                    "現在値(円)": filtered_trend_df1["latest_close"].apply(lambda x: f"{x:,.1f}" if pd.notna(x) else "-"),
                    "25日線": filtered_trend_df1["latest_sma_25"].apply(lambda x: f"{x:,.1f}" if pd.notna(x) else "-"),
                    "75日線": filtered_trend_df1["latest_sma_75"].apply(lambda x: f"{x:,.1f}" if pd.notna(x) else "-"),
                    "RSI(14)": filtered_trend_df1["latest_rsi"].apply(lambda x: f"{x:.1f}" if pd.notna(x) else "-"),
                    "出来高": filtered_trend_df1["latest_volume"].apply(lambda x: f"{int(x):,}株" if pd.notna(x) else "-"),
                    "出来高倍率": filtered_trend_df1["vol_ratio"].apply(lambda x: f"{x:.2f}倍" if pd.notna(x) else "-"),
                    "セクター": filtered_trend_df1["sector"]
                })
                st.dataframe(display_df1, use_container_width=True, hide_index=True)
            else:
                st.warning("現在の条件に一致する上昇トレンド銘柄はありませんでした。")

        with t2:
            st.markdown("### 【② おすすめ総合定義：トレンド＆モメンタム型】")
            filtered_trend_df2 = base_df[
                (base_df["latest_close"].notna()) &
                (base_df["latest_sma_25"].notna()) &
                (base_df["latest_sma_75"].notna()) &
                (base_df["latest_close"] > base_df["latest_sma_25"]) &
                (base_df["latest_sma_25"] > base_df["latest_sma_75"]) &
                (base_df["latest_volume"].notna()) &
                (base_df["latest_vol_sma_20"].notna()) &
                (base_df["latest_volume"] >= base_df["latest_vol_sma_20"] * 1.2) &
                (base_df["latest_rsi"].notna()) &
                (base_df["latest_rsi"] <= 70.0)
            ].copy()

            st.subheader(f"🚀 検知された上昇トレンド銘柄 ({len(filtered_trend_df2)} 件)")
            if not filtered_trend_df2.empty:
                display_df2 = pd.DataFrame({
                    "コード": filtered_trend_df2["code"],
                    "銘柄名": filtered_trend_df2["name"],
                    "現在値(円)": filtered_trend_df2["latest_close"].apply(lambda x: f"{x:,.1f}" if pd.notna(x) else "-"),
                    "25日線": filtered_trend_df2["latest_sma_25"].apply(lambda x: f"{x:,.1f}" if pd.notna(x) else "-"),
                    "75日線": filtered_trend_df2["latest_sma_75"].apply(lambda x: f"{x:,.1f}" if pd.notna(x) else "-"),
                    "RSI(14)": filtered_trend_df2["latest_rsi"].apply(lambda x: f"{x:.1f}" if pd.notna(x) else "-"),
                    "出来高": filtered_trend_df2["latest_volume"].apply(lambda x: f"{int(x):,}株" if pd.notna(x) else "-"),
                    "出来高倍率": filtered_trend_df2["vol_ratio"].apply(lambda x: f"{x:.2f}倍" if pd.notna(x) else "-"),
                    "セクター": filtered_trend_df2["sector"]
                })
                st.dataframe(display_df2, use_container_width=True, hide_index=True)
            else:
                st.warning("現在の条件に一致する上昇トレンド銘柄はありませんでした。")

        with t3:
            st.markdown("### 【③ 情報通信系上昇トレンド】")
            filtered_trend_df3 = base_df[
                (base_df["sector"].isin(["情報・通信", "情報・通信業"])) &
                (base_df["latest_rsi"].notna()) & (base_df["latest_rsi"].between(70, 80)) &
                (base_df["latest_close"].notna()) & (base_df["latest_sma_25"].notna()) &
                (base_df["latest_close"] > base_df["latest_sma_25"]) &
                (base_df["latest_sma_75"].notna()) &
                (base_df["latest_sma_25"] > base_df["latest_sma_75"]) &
                (base_df["vol_ratio"].notna()) & (base_df["vol_ratio"] >= 1.4) &
                (base_df["disp_25"].notna()) & (base_df["disp_25"].between(20, 50))
            ].copy()

            st.subheader(f"🚀 検知された銘柄 ({len(filtered_trend_df3)} 件)")
            if not filtered_trend_df3.empty:
                display_df3 = pd.DataFrame({
                    "コード": filtered_trend_df3["code"],
                    "銘柄名": filtered_trend_df3["name"],
                    "現在値(円)": filtered_trend_df3["latest_close"].apply(lambda x: f"{x:,.1f}" if pd.notna(x) else "-"),
                    "RSI(14)": filtered_trend_df3["latest_rsi"].apply(lambda x: f"{x:.1f}" if pd.notna(x) else "-"),
                    "出来高倍率": filtered_trend_df3["vol_ratio"].apply(lambda x: f"{x:.2f}倍" if pd.notna(x) else "-"),
                    "25日乖離率": filtered_trend_df3["disp_25"].apply(lambda x: f"{x:+.1f}%" if pd.notna(x) else "-"),
                    "セクター": filtered_trend_df3["sector"]
                })
                st.dataframe(display_df3, use_container_width=True, hide_index=True)
            else:
                st.warning("現在の条件に一致する銘柄はありませんでした。")

        with t4:
            st.markdown("### 【④ 底値からの脱出？】")
            filtered_trend_df4 = base_df[
                (base_df["latest_rsi"].notna()) & (base_df["latest_rsi"] <= 40) &
                (base_df["latest_close"].notna()) & (base_df["latest_sma_25"].notna()) &
                (base_df["latest_close"] <= base_df["latest_sma_25"]) &
                (base_df["latest_sma_75"].notna()) &
                (base_df["latest_sma_25"] <= base_df["latest_sma_75"]) &
                (base_df["vol_ratio"].notna()) & (base_df["vol_ratio"] >= 1.2)
            ].copy()

            st.subheader(f"🚀 検知された銘柄 ({len(filtered_trend_df4)} 件)")
            if not filtered_trend_df4.empty:
                display_df4 = pd.DataFrame({
                    "コード": filtered_trend_df4["code"],
                    "銘柄名": filtered_trend_df4["name"],
                    "現在値(円)": filtered_trend_df4["latest_close"].apply(lambda x: f"{x:,.1f}" if pd.notna(x) else "-"),
                    "RSI(14)": filtered_trend_df4["latest_rsi"].apply(lambda x: f"{x:.1f}" if pd.notna(x) else "-"),
                    "出来高倍率": filtered_trend_df4["vol_ratio"].apply(lambda x: f"{x:.2f}倍" if pd.notna(x) else "-"),
                    "セクター": filtered_trend_df4["sector"]
                })
                st.dataframe(display_df4, use_container_width=True, hide_index=True)
            else:
                st.warning("現在の条件に一致する銘柄はありませんでした。")

        with t5:
            st.markdown("### 【⑤ ヘルスケア系上昇トレンド】")
            filtered_trend_df5 = base_df[
                (base_df["sector"] == "ヘルスケア・医薬品") &
                (base_df["latest_rsi"].notna()) & (base_df["latest_rsi"] >= 60) &
                (base_df["latest_close"].notna()) & (base_df["latest_sma_25"].notna()) &
                (base_df["latest_close"] > base_df["latest_sma_25"]) &
                (base_df["latest_sma_75"].notna()) &
                (base_df["latest_sma_25"] > base_df["latest_sma_75"])
            ].copy()

            st.subheader(f"🚀 検知された銘柄 ({len(filtered_trend_df5)} 件)")
            if not filtered_trend_df5.empty:
                display_df5 = pd.DataFrame({
                    "コード": filtered_trend_df5["code"],
                    "銘柄名": filtered_trend_df5["name"],
                    "現在値(円)": filtered_trend_df5["latest_close"].apply(lambda x: f"{x:,.1f}" if pd.notna(x) else "-"),
                    "RSI(14)": filtered_trend_df5["latest_rsi"].apply(lambda x: f"{x:.1f}" if pd.notna(x) else "-"),
                    "出来高倍率": filtered_trend_df5["vol_ratio"].apply(lambda x: f"{x:.2f}倍" if pd.notna(x) else "-"),
                    "セクター": filtered_trend_df5["sector"]
                })
                st.dataframe(display_df5, use_container_width=True, hide_index=True)
            else:
                st.warning("現在の条件に一致する銘柄はありませんでした。")

# ----------------------------------------------------
# 画面 4: 💼 ポートフォリオ＆売買管理
# ----------------------------------------------------
elif page == "💼 ポートフォリオ＆売買管理":
    st.title("💼 ポートフォリオ＆売買履歴管理")
    (
        pf_df,
        tot_inv,
        tot_val,
        tot_realized,
        tx_df,
    ) = calculate_portfolio_and_summary()

    st.subheader("📊 資産パフォーマンス概要")
    unrealized_pnl = tot_val - tot_inv
    unrealized_pct = (unrealized_pnl / tot_inv * 100) if tot_inv > 0 else 0.0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("総投資額 (取得原価)", f"{tot_inv:,.0f} 円")
    col2.metric("現在の評価額", f"{tot_val:,.0f} 円")
    col3.metric(
        "評価損益 (含み損益)",
        f"{unrealized_pnl:,.0f} 円",
        delta=f"{unrealized_pct:+.2f}%",
    )
    col4.metric(
        "累計確定損益 (実現損益)",
        f"{tot_realized:,.0f} 円",
        delta="確定利益/損失",
    )

    st.divider()

    tab1, tab2, tab3 = st.tabs(
        ["保有株式一覧", "➕ 取引を入力する", "📜 売買履歴・削除"]
    )
    with tab1:
        st.markdown("### 🟢 現在の保有資産一覧（買値からの目標・損切ライン付）")
        if not pf_df.empty:
            st.dataframe(pf_df, use_container_width=True)
        else:
            st.info("現在保有中の株式はありません。")

    with tab2:
        st.markdown("### ➕ 新しい売買ログを入力")
        if companies_df.empty:
            st.warning("登録されている銘柄がありません。")
        else:
            with st.form("add_transaction_form", clear_on_submit=True):
                company_dict = {
                    f"{row['code']}: {row['name']}": row["ticker"]
                    for _, row in companies_df.iterrows()
                }
                c1, c2, c3 = st.columns(3)
                selected_comp = c1.selectbox(
                    "銘柄を選択", list(company_dict.keys())
                )
                tx_type = c2.selectbox(
                    "売買種別",
                    ["BUY", "SELL"],
                    format_func=lambda x: (
                        "🟢 買付 (BUY)" if x == "BUY" else "🔴 売却 (SELL)"
                    ),
                )
                trade_date = c3.date_input("取引日")

                c4, c5, c6 = st.columns(3)
                price = c4.number_input(
                    "約定単価 (円)", min_value=1.0, value=1000.0, step=10.0
                )
                quantity = c5.number_input(
                    "株数", min_value=1, value=100, step=100
                )
                memo = c6.text_input("メモ", value="")
                submit = st.form_submit_button("💾 取引を保存する")

                if submit:
                    ticker = company_dict[selected_comp]
                    supabase.table("transactions").insert({
                        "ticker": ticker,
                        "type": tx_type,
                        "trade_date": str(trade_date),
                        "price": price,
                        "quantity": quantity,
                        "memo": memo,
                    }).execute()
                    st.success("✅ 取引ログを保存しました！")
                    st.rerun()

    with tab3:
        st.markdown("### 📜 過去の売買履歴")
        if not tx_df.empty:
            jp_tx_df = tx_df.rename(
                columns={
                    "transaction_id": "取引ID",
                    "trade_date": "取引日",
                    "code": "コード",
                    "name": "銘柄名",
                    "type": "売買種別",
                    "price": "単価(円)",
                    "quantity": "株数",
                    "memo": "メモ",
                }
            )
            st.dataframe(
                jp_tx_df[
                    [
                        "取引ID",
                        "取引日",
                        "コード",
                        "銘柄名",
                        "売買種別",
                        "単価(円)",
                        "株数",
                        "メモ",
                    ]
                ],
                use_container_width=True,
            )

            st.markdown("### 🗑️ 取引の削除")
            del_id = st.number_input(
                "削除したい取引の 取引ID を入力", min_value=1, step=1
            )
            if st.button("取引を削除する"):
                supabase.table("transactions").delete().eq("transaction_id", del_id).execute()
                st.success(f"ID: {del_id} の取引を削除しました。")
                st.rerun()
        else:
            st.info("まだ取引履歴はありません。")

# ----------------------------------------------------
# 画面 5: ⚙️ 銘柄登録・管理
# ----------------------------------------------------
elif page == "⚙️ 銘柄登録・管理":
    st.title("⚙️ 銘柄登録・マスター管理")
    tab1, tab2, tab3, tab4 = st.tabs(
        [
            "➕ 新規銘柄を追加",
            "✏️ 銘柄情報・テーマ設定",
            "🏷️ テーママスター追加",
            "📋 登録銘柄一覧・削除",
        ]
    )

    with tab1:
        with st.form("add_company_form", clear_on_submit=True):
            st.subheader("銘柄コードを入力")
            c1, c2, c3 = st.columns([2, 2, 2])
            code_input = c1.text_input(
                "銘柄コード (例: 6920 または 6920.T)", value=""
            )
            custom_name = c2.text_input(
                "銘柄名 (任意)", placeholder="例: レーザーテック"
            )
            custom_sector = c3.text_input(
                "セクター (任意)", placeholder="例: 電気機器"
            )
            submit_btn = st.form_submit_button("🚀 銘柄を追加してデータを取得")

            if submit_btn:
                if not code_input:
                    st.error("銘柄コードを入力してください。")
                else:
                    with st.spinner("重複チェック＆指標データを取得中..."):
                        success, msg = register_and_fetch_stock(
                            code_input, custom_name, custom_sector
                        )
                        if success:
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)

    with tab2:
        st.subheader("✏️ 登録済み銘柄の編集・テーマ設定")
        if not companies_df.empty:
            company_dict = {
                f"{row['code']}: {row['name']}": row["ticker"]
                for _, row in companies_df.iterrows()
            }
            selected_edit_label = st.selectbox(
                "編集する銘柄を選択", list(company_dict.keys())
            )
            selected_edit_ticker = company_dict[selected_edit_label]

            comp_data = companies_df[
                companies_df["ticker"] == selected_edit_ticker
            ].iloc[0]

            cur_themes_res = supabase.table("company_themes").select("theme_id").eq("ticker", selected_edit_ticker).order("theme_id").execute()
            current_theme_ids = [item["theme_id"] for item in cur_themes_res.data]

            with st.form("edit_company_form"):
                e_name = st.text_input("銘柄名", value=comp_data["name"])
                e_sector = st.text_input("セクター", value=comp_data["sector"])

                theme_options = {
                    row["name"]: row["theme_id"]
                    for _, row in themes_df.iterrows()
                } if not themes_df.empty else {}
                
                default_selected_themes = [
                    name
                    for name, t_id in theme_options.items()
                    if t_id in current_theme_ids
                ]

                selected_theme_names = st.multiselect(
                    "🏷️ テーマを設定",
                    list(theme_options.keys()),
                    default=default_selected_themes,
                )
                update_btn = st.form_submit_button("💾 変更内容を保存")

                if update_btn:
                    supabase.table("companies").update({"name": e_name, "sector": e_sector}).eq("ticker", selected_edit_ticker).execute()
                    supabase.table("company_themes").delete().eq("ticker", selected_edit_ticker).execute()
                    
                    new_mappings = []
                    for t_name in selected_theme_names:
                        t_id = theme_options[t_name]
                        new_mappings.append({"ticker": selected_edit_ticker, "theme_id": t_id})
                    if new_mappings:
                        supabase.table("company_themes").insert(new_mappings).execute()

                    st.cache_data.clear()
                    st.success("✅ 保存完了しました！")
                    st.rerun()
        else:
            st.info("登録されている銘柄はありません。")

    with tab3:
        st.subheader("🏷️ テーママスターの追加")
        with st.form("add_theme_form", clear_on_submit=True):
            new_theme_name = st.text_input(
                "新しいテーマ名 (例: 半導体, 高配当, AI)"
            )
            theme_desc = st.text_input("説明 (任意)")
            add_theme_btn = st.form_submit_button("➕ テーマを追加")

            if add_theme_btn:
                if new_theme_name.strip():
                    try:
                        supabase.table("themes").insert({
                            "name": new_theme_name.strip(),
                            "description": theme_desc.strip()
                        }).execute()
                        st.success(f"テーマ '{new_theme_name}' を追加しました！")
                        st.rerun()
                    except Exception:
                        st.error("そのテーマ名は既に存在します（またはエラーが発生しました）。")

    with tab4:
        st.subheader("現在登録されている銘柄")
        if not companies_df.empty:
            jp_companies_df = companies_df.rename(
                columns={
                    "code": "コード",
                    "name": "銘柄名",
                    "sector": "セクター",
                    "per": "PER(倍)",
                    "pbr": "PBR(倍)",
                    "roe": "ROE(%)",
                    "dividend_yield": "配当利回り(%)",
                    "themes": "設定テーマ",
                    "ticker": "ティッカー",
                }
            )
            st.dataframe(
                jp_companies_df[
                    [
                        "コード",
                        "銘柄名",
                        "セクター",
                        "PER(倍)",
                        "PBR(倍)",
                        "ROE(%)",
                        "配当利回り(%)",
                        "設定テーマ",
                    ]
                ],
                use_container_width+True,
            )

            st.markdown("---")
            st.subheader("🗑️ 銘柄の削除")
            del_company_options = {
                f"{row['code']}: {row['name']}": row["ticker"]
                for _, row in companies_df.iterrows()
            }
            selected_del_label = st.selectbox(
                "削除する銘柄を選択", list(del_company_options.keys())
            )
            selected_del_ticker = del_company_options[selected_del_label]

            if st.button("❌ 選択した銘柄をDBから削除する"):
                supabase.table("companies").delete().eq("ticker", selected_del_ticker).execute()
                st.cache_data.clear()
                st.success(f"銘柄（{selected_del_ticker}）を削除しました。")
                st.rerun()