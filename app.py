# app.py (PostgreSQL / Supabase 完全移行版)
import re
import urllib.request
from datetime import datetime, timedelta
import pandas as pd
import streamlit as st
import yfinance as yf
from sqlalchemy import create_engine, text

# 1. 認証チェック用の関数（または判定ロジック）
def check_password():
    """パスワードが正しいかチェックする関数"""
    
    # ログイン済みの場合はそのまま通す
    if st.session_state.get("password_correct", False):
        return True

    # ログイン画面の表示
    st.subheader("🔒 ログインしてください")
    
    # パスワード入力欄
    password = st.text_input("パスワード", type="password")
    
    if st.button("ログイン"):
        if password == "3080":
            st.session_state["password_correct"] = True
            st.rerun()  # 画面を再読み込みしてアプリ本体を表示
        else:
            st.error("パスワードが違います")
            
    return False

# 2. 最初にパスワードチェックを実行
if not check_password():
    st.stop()  # パスワードが合致するまでは、これ以降のアプリのコードを読み込まない
    
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


# データベース接続エンジン (Supabase / PostgreSQL)
def get_engine():
    db = st.secrets["postgres"]
    port = db.get("port", 5432)
    if not port:
        port = 5432
    
    # URLの末尾に ?sslmode=require を追加
    url = f"postgresql://{db['user']}:{db['password']}@{db['host']}:{port}/{db['dbname']}?sslmode=require"
    return create_engine(url)


# DBスキーマの自動アップデート（PostgreSQL用）
def init_db_schema():
    with engine.begin() as conn:
        for col_def in ["per DOUBLE PRECISION", "pbr DOUBLE PRECISION", "dividend_yield DOUBLE PRECISION", "roe DOUBLE PRECISION"]:
            try:
                conn.execute(text(f"ALTER TABLE companies ADD COLUMN IF NOT EXISTS {col_def};"))
            except Exception:
                pass

init_db_schema()


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


# 銘柄一覧・全指標の取得（PostgreSQL用 STRING_AGG 対応）
@st.cache_data
def load_companies():
    query = """
    SELECT c.ticker, c.code, c.name, c.sector, c.per, c.pbr, c.dividend_yield, c.roe,
           latest_p.close AS latest_close,
           latest_p.volume AS latest_volume,
           latest_p.sma_25 AS latest_sma_25,
           latest_p.sma_75 AS latest_sma_75,
           latest_p.rsi_14 AS latest_rsi,
           STRING_AGG(t.name, ', ') AS themes
    FROM companies c
    LEFT JOIN company_themes ct ON c.ticker = ct.ticker
    LEFT JOIN themes t ON ct.theme_id = t.theme_id
    LEFT JOIN (
        SELECT dp1.ticker, dp1.close, dp1.volume, dp1.sma_25, dp1.sma_75, dp1.rsi_14, dp1.date
        FROM daily_prices dp1
        INNER JOIN (
            SELECT ticker, MAX(date) AS max_date 
            FROM daily_prices 
            GROUP BY ticker
        ) dp2 ON dp1.ticker = dp2.ticker AND dp1.date = dp2.max_date
    ) latest_p ON c.ticker = latest_p.ticker
    GROUP BY c.ticker, c.code, c.name, c.sector, c.per, c.pbr, c.dividend_yield, c.roe,
             latest_p.close, latest_p.volume, latest_p.sma_25, latest_p.sma_75, latest_p.rsi_14, latest_p.date
    ORDER BY c.code ASC;
    """
    df = pd.read_sql(query, engine)
    return df

# テーマ一覧の取得
def load_themes():
    df = pd.read_sql("SELECT theme_id, name FROM themes ORDER BY name ASC;", engine)
    return df


# 単一銘柄の株価データをフェッチ＆DB保存（PostgreSQL ON CONFLICT 対応）
def update_stock_prices_in_db(ticker, start_date, end_date=None):
    if end_date is None:
        end_date = (datetime.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        try:
            dt = datetime.strptime(end_date, "%Y-%m-%d")
            end_date = (dt + timedelta(days=1)).strftime("%Y-%m-%d")
        except Exception:
            end_date = (datetime.today() + timedelta(days=1)).strftime("%Y-%m-%d")

    df = yf.download(ticker, start=start_date, end=end_date, progress=False, auto_adjust=False)
    if df.empty:
        return False
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df["change_pct"] = df["Close"].pct_change() * 100
    df["sma_25"] = df["Close"].rolling(window=25).mean()
    df["sma_75"] = df["Close"].rolling(window=75).mean()
    df["rsi_14"] = calculate_rsi(df["Close"], 14)

    with engine.begin() as conn:
        for date, row in df.iterrows():
            date_str = date.strftime("%Y-%m-%d")

            def safe_val(val):
                return None if pd.isna(val) else float(val)

            conn.execute(
                text("""
                    INSERT INTO daily_prices (
                        ticker, date, open, high, low, close, change_pct, volume, sma_25, sma_75, rsi_14
                    ) VALUES (:ticker, :date, :open, :high, :low, :close, :change_pct, :volume, :sma_25, :sma_75, :rsi_14)
                    ON CONFLICT (ticker, date) DO UPDATE SET 
                        open = EXCLUDED.open,
                        high = EXCLUDED.high,
                        low = EXCLUDED.low,
                        close = EXCLUDED.close,
                        change_pct = EXCLUDED.change_pct,
                        volume = EXCLUDED.volume,
                        sma_25 = EXCLUDED.sma_25,
                        sma_75 = EXCLUDED.sma_75,
                        rsi_14 = EXCLUDED.rsi_14;
                """),
                {
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
                },
            )
    return True


# 全銘柄の株価および指標を一括更新
def refresh_all_stocks_data():
    tickers_df = pd.read_sql("SELECT ticker FROM companies;", engine)
    tickers = tickers_df["ticker"].tolist()
    if not tickers:
        return 0, "登録されている銘柄がありません。"

    success_count = 0
    end_date = (datetime.today() + timedelta(days=1)).strftime("%Y-%m-%d")

    for ticker in tickers:
        last_date_df = pd.read_sql(
            text("SELECT MAX(date) as max_date FROM daily_prices WHERE ticker = :ticker;"),
            engine,
            params={"ticker": ticker},
        )

        max_date = last_date_df["max_date"].iloc[0] if not last_date_df.empty else None
        if max_date:
            start_date = (
                datetime.strptime(str(max_date), "%Y-%m-%d") - timedelta(days=120)
            ).strftime("%Y-%m-%d")
        else:
            start_date = (datetime.today() - timedelta(days=730)).strftime(
                "%Y-%m-%d"
            )

        if update_stock_prices_in_db(ticker, start_date, end_date):
            try:
                info = yf.Ticker(ticker).info
                current_price = info.get("currentPrice") or info.get("regularMarketPrice")
                per = info.get("forwardPE") or info.get("trailingPE")
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
                roe = (
                    raw_roe * 100
                    if raw_roe is not None
                    else (
                        info.get("roe") * 100
                        if info.get("roe") is not None
                        else None
                    )
                )

                with engine.begin() as conn:
                    conn.execute(
                        text("""
                            UPDATE companies SET per = :per, pbr = :pbr, dividend_yield = :div_yield, roe = :roe WHERE ticker = :ticker;
                        """),
                        {"per": per, "pbr": pbr, "div_yield": div_yield, "roe": roe, "ticker": ticker}
                    )
            except Exception:
                pass
            success_count += 1

    st.cache_data.clear()
    return (
        success_count,
        f"全 {len(tickers)} 銘柄中 {success_count} 銘柄の株価・指標データを最新に更新しました！",
    )


# 新規銘柄登録 ＆ データ取得
def register_and_fetch_stock(code_input, custom_name="", custom_sector=""):
    clean_code = code_input.strip().upper()
    ticker = f"{clean_code}.T" if not clean_code.endswith(".T") else clean_code
    code = clean_code.replace(".T", "")

    existing_df = pd.read_sql(
        text("SELECT name FROM companies WHERE ticker = :ticker OR code = :code;"),
        engine,
        params={"ticker": ticker, "code": code}
    )
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
    per = info.get("forwardPE") or info.get("trailingPE")
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

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO companies (ticker, code, name, sector, per, pbr, dividend_yield, roe)
                VALUES (:ticker, :code, :name, :sector, :per, :pbr, :div_yield, :roe)
                ON CONFLICT (ticker) DO UPDATE SET
                    code = EXCLUDED.code,
                    name = EXCLUDED.name,
                    sector = EXCLUDED.sector,
                    per = EXCLUDED.per,
                    pbr = EXCLUDED.pbr,
                    dividend_yield = EXCLUDED.dividend_yield,
                    roe = EXCLUDED.roe;
            """),
            {
                "ticker": ticker,
                "code": code,
                "name": name,
                "sector": sector,
                "per": per,
                "pbr": pbr,
                "div_yield": div_yield,
                "roe": roe,
            },
        )

    end_date = datetime.today().strftime("%Y-%m-%d")
    start_date = (datetime.today() - timedelta(days=730)).strftime("%Y-%m-%d")
    success = update_stock_prices_in_db(ticker, start_date, end_date)
    if not success:
        return False, f"⚠️ '{ticker}' の株価データを取得できませんでした。"

    st.cache_data.clear()
    return True, f"🎉 【{name} ({code})】 を登録し、データを読み込みました！"


# ポートフォリオ計算ロジック
def calculate_portfolio_and_summary():
    tx_df = pd.read_sql(
        text("""
            SELECT t.transaction_id, t.ticker, c.code, c.name, t.type, t.trade_date, t.price, t.quantity, t.memo
            FROM transactions t
            JOIN companies c ON t.ticker = c.ticker
            ORDER BY t.trade_date ASC, t.transaction_id ASC;
        """),
        engine,
    )
    latest_prices = pd.read_sql(
        text("""
            SELECT dp.ticker, dp.close
            FROM daily_prices dp
            INNER JOIN (
                SELECT ticker, MAX(date) as max_date
                FROM daily_prices
                GROUP BY ticker
            ) latest ON dp.ticker = latest.ticker AND dp.date = latest.max_date;
        """),
        engine,
    )

    if tx_df.empty:
        return pd.DataFrame(), 0.0, 0.0, 0.0, tx_df

    price_dict = dict(zip(latest_prices["ticker"], latest_prices["close"]))
    portfolio = {}

    for _, row in tx_df.iterrows():
        ticker = row["ticker"]
        t_type = row["type"]
        price = row["price"]
        qty = row["quantity"]

        if ticker not in portfolio:
            portfolio[ticker] = {
                "code": row["code"],
                "name": row["name"],
                "qty": 0,
                "total_cost": 0.0,
                "realized_pnl": 0.0,
            }

        p = portfolio[ticker]
        if t_type == "BUY":
            p["qty"] += qty
            p["total_cost"] += price * qty
        elif t_type == "SELL":
            if p["qty"] > 0:
                avg_price = p["total_cost"] / p["qty"]
                p["realized_pnl"] += (price - avg_price) * qty
                p["qty"] -= qty
                p["total_cost"] -= avg_price * qty
                if p["qty"] <= 0:
                    p["qty"] = 0
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
            current_value = current_price * data["qty"]
            unrealized_pnl = current_value - data["total_cost"]
            unrealized_pnl_pct = (
                (unrealized_pnl / data["total_cost"] * 100)
                if data["total_cost"] > 0
                else 0.0
            )

            total_investment += data["total_cost"]
            total_current_value += current_value

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
# Session State 初期化
# ----------------------------------------------------
if "current_page" not in st.session_state:
    st.session_state["current_page"] = "📈 株価・テクニカル分析"
if "selected_stock_label" not in st.session_state:
    st.session_state["selected_stock_label"] = None
if "nav_radio" not in st.session_state:
    st.session_state["nav_radio"] = "📈 株価・テクニカル分析"

if "requested_page" in st.session_state:
    st.session_state["nav_radio"] = st.session_state["requested_page"]
    del st.session_state["requested_page"]

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
st.sidebar.title("🐶 しばかぶ メニュー")

if st.sidebar.button("🔄 全銘柄の株価・指標を更新"):
    with st.spinner("最新データを取得中..."):
        cnt, msg = refresh_all_stocks_data()
        st.sidebar.success(msg)
        st.rerun()

page = st.sidebar.radio(
    "機能を選択:",
    [
        "📈 株価・テクニカル分析",
        "🔍 詳細検索（スクリーナー）",
        "💼 ポートフォリオ＆売買管理",
        "⚙️ 銘柄登録・管理",
    ],
    key="nav_radio",
)
st.session_state["current_page"] = page

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
        theme_list = ["全テーマ"] + themes_df["name"].tolist()
        selected_theme_filter = st.sidebar.selectbox(
            "🏷️ テーマで絞り込み", theme_list
        )

        filtered_companies = companies_df.copy()
        if selected_theme_filter != "全テーマ":
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
            company_options = {
                f"{row['code']}: {row['name']}": row["ticker"]
                for _, row in filtered_companies.iterrows()
            }
            option_keys = list(company_options.keys())

            if st.session_state["selected_stock_label"] not in option_keys:
                st.session_state["selected_stock_label"] = option_keys[0]

            selected_label = st.sidebar.selectbox(
                "銘柄を選んでください", option_keys, key="selected_stock_label"
            )
            selected_ticker = company_options[selected_label]

            company_info = companies_df[
                companies_df["ticker"] == selected_ticker
            ].iloc[0]

            df_prices = pd.read_sql(
                text("""
                    SELECT date, open, high, low, close, change_pct, volume, sma_25, sma_75, rsi_14
                    FROM daily_prices WHERE ticker = :ticker ORDER BY date ASC;
                """),
                engine,
                params={"ticker": selected_ticker},
            )

            if not df_prices.empty:
                latest = df_prices.iloc[-1]
                themes_str = (
                    f" / テーマ: {company_info['themes']}"
                    if pd.notna(company_info["themes"])
                    else ""
                )
                st.subheader(
                    f"{company_info['name']} （コード: {company_info['code']} / セクター: {company_info['sector']}{themes_str}）"
                )

                col1, col2, col3, col4, col5, col6 = st.columns(6)
                delta_str = (
                    f"{latest['change_pct']:+.2f}%"
                    if latest["change_pct"] is not None
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
                col3.metric("PER (予想)", per_disp)
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

                tab1, tab2, tab3 = st.tabs(
                    ["📈 株価＆移動平均線", "📊 RSI指標", "📄 過去データ一覧"]
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
                for s in companies_df["sector"].dropna().unique()
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
        themes_list = themes_df["name"].tolist()
        selected_theme = c10.selectbox(
            "テーマ",
            themes_list if themes_list else ["なし"],
            disabled=not use_theme,
            label_visibility="collapsed",
        )

        res_df = companies_df.copy()
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
        if use_theme and selected_theme:
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
                    key=f"btn_nav_{row['code']}",
                    type="tertiary",
                ):
                    st.session_state["selected_stock_label"] = (
                        f"{row['code']}: {row['name']}"
                    )
                    st.session_state["requested_page"] = (
                        "📈 株価・テクニカル分析"
                    )
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
# 画面 3: ポートフォリオ＆売買管理
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
        st.markdown("### 🟢 現在の保有資産一覧")
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
                    with engine.begin() as conn:
                        conn.execute(
                            text("""
                                INSERT INTO transactions (ticker, type, trade_date, price, quantity, memo)
                                VALUES (:ticker, :type, :trade_date, :price, :quantity, :memo);
                            """),
                            {
                                "ticker": ticker,
                                "type": tx_type,
                                "trade_date": str(trade_date),
                                "price": price,
                                "quantity": quantity,
                                "memo": memo,
                            },
                        )
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

            st.markdown("#### 🗑️ 取引の削除")
            del_id = st.number_input(
                "削除したい取引の 取引ID を入力", min_value=1, step=1
            )
            if st.button("取引を削除する"):
                with engine.begin() as conn:
                    conn.execute(
                        text("DELETE FROM transactions WHERE transaction_id = :del_id;"),
                        {"del_id": del_id}
                    )
                st.success(f"ID: {del_id} の取引を削除しました。")
                st.rerun()
        else:
            st.info("まだ取引履歴はありません。")

# ----------------------------------------------------
# 画面 4: ⚙️ 銘柄登録・管理
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

            cur_themes_df = pd.read_sql(
                text("SELECT theme_id FROM company_themes WHERE ticker = :ticker;"),
                engine,
                params={"ticker": selected_edit_ticker},
            )
            current_theme_ids = cur_themes_df["theme_id"].tolist()

            with st.form("edit_company_form"):
                e_name = st.text_input("銘柄名", value=comp_data["name"])
                e_sector = st.text_input("セクター", value=comp_data["sector"])

                theme_options = {
                    row["name"]: row["theme_id"]
                    for _, row in themes_df.iterrows()
                }
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
                    with engine.begin() as conn:
                        conn.execute(
                            text("UPDATE companies SET name = :name, sector = :sector WHERE ticker = :ticker;"),
                            {"name": e_name, "sector": e_sector, "ticker": selected_edit_ticker}
                        )
                        conn.execute(
                            text("DELETE FROM company_themes WHERE ticker = :ticker;"),
                            {"ticker": selected_edit_ticker}
                        )
                        for t_name in selected_theme_names:
                            t_id = theme_options[t_name]
                            conn.execute(
                                text("INSERT INTO company_themes (ticker, theme_id) VALUES (:ticker, :theme_id);"),
                                {"ticker": selected_edit_ticker, "theme_id": t_id}
                            )

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
                        with engine.begin() as conn:
                            conn.execute(
                                text("INSERT INTO themes (name, description) VALUES (:name, :description);"),
                                {"name": new_theme_name.strip(), "description": theme_desc.strip()}
                            )
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
                use_container_width=True,
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
                with engine.begin() as conn:
                    conn.execute(
                        text("DELETE FROM companies WHERE ticker = :ticker;"),
                        {"ticker": selected_del_ticker}
                    )
                st.cache_data.clear()
                st.success(f"銘柄（{selected_del_ticker}）を削除しました。")
                st.rerun()