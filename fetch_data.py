# fetch_data.py
import sqlite3
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import yfinance as yf

# 初期登録する主要銘柄リスト (ticker, code, name, sector)
INITIAL_STOCKS = [
    ("7203.T", "7203", "トヨタ自動車", "輸送用機器"),
    ("6758.T", "6758", "ソニーグループ", "電気機器"),
    ("7974.T", "7974", "任天堂", "その他製品"),
    ("9983.T", "9983", "ファーストリテイリング", "小売業"),
    ("9984.T", "9984", "ソフトバンクグループ", "情報・通信業"),
    ("8306.T", "8306", "三菱UFJフィナンシャルG", "銀行業"),
    ("6861.T", "6861", "キーエンス", "電気機器"),
    ("8035.T", "8035", "東京エレクトロン", "電気機器"),
]


def calculate_rsi(series, period=14):
    """RSI(14)を計算する関数"""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def setup_companies(conn):
    """銘柄マスターに初期銘柄を登録"""
    cursor = conn.cursor()
    for ticker, code, name, sector in INITIAL_STOCKS:
        cursor.execute(
            """
        INSERT OR REPLACE INTO companies (ticker, code, name, sector)
        VALUES (?, ?, ?, ?);
        """,
            (ticker, code, name, sector),
        )
    conn.commit()
    print(
        f"✅ 銘柄マスターに {len(INITIAL_STOCKS)} 銘柄を登録しました。"
    )


def fetch_stock_prices(conn):
    """yfinanceから過去株価を取得し、テクニカル指標を計算してDBへ保存"""
    cursor = conn.cursor()

    # 過去2年分のデータを取りに行く
    end_date = datetime.today().strftime("%Y-%m-%d")
    start_date = (datetime.today() - timedelta(days=730)).strftime(
        "%Y-%m-%d"
    )

    for ticker, code, name, _ in INITIAL_STOCKS:
        print(f"📥 株価取得中: {name} ({code})...")
        df = yf.download(
            ticker, start=start_date, end=end_date, progress=False
        )

        if df.empty:
            print(f"⚠️ {name} のデータ取得に失敗しました。")
            continue

        # MultiIndexカラム対策 (yfinanceの最新仕様に対応)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # テクニカル指標の計算
        df["change_pct"] = df["Close"].pct_change() * 100
        df["sma_25"] = df["Close"].rolling(window=25).mean()
        df["sma_75"] = df["Close"].rolling(window=75).mean()
        df["rsi_14"] = calculate_rsi(df["Close"], 14)

        # DB保存用のデータ整形
        for date, row in df.iterrows():
            date_str = date.strftime("%Y-%m-%d")

            # NaN (計算不能な初期データ) を None に置換
            def safe_val(val):
                return None if pd.isna(val) else float(val)

            cursor.execute(
                """
            INSERT OR REPLACE INTO daily_prices (
                ticker, date, open, high, low, close, change_pct, volume, sma_25, sma_75, rsi_14
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
                (
                    ticker,
                    date_str,
                    safe_val(row["Open"]),
                    safe_val(row["High"]),
                    safe_val(row["Low"]),
                    safe_val(row["Close"]),
                    safe_val(row["change_pct"]),
                    int(row["Volume"]) if not pd.isna(row["Volume"]) else 0,
                    safe_val(row["sma_25"]),
                    safe_val(row["sma_75"]),
                    safe_val(row["rsi_14"]),
                ),
            )

    conn.commit()
    print("✅ 株価データおよびテクニカル指標の保存が完了しました！")


def fetch_market_indices(conn):
    """日経平均(^N225) と ドル円(JPY=X) のデータを取得して保存"""
    print("📥 市場指標（日経平均・ドル円）を取得中...")
    cursor = conn.cursor()

    end_date = datetime.today().strftime("%Y-%m-%d")
    start_date = (datetime.today() - timedelta(days=730)).strftime(
        "%Y-%m-%d"
    )

    n225 = yf.download(
        "^N225", start=start_date, end=end_date, progress=False
    )
    usdjpy = yf.download(
        "JPY=X", start=start_date, end=end_date, progress=False
    )

    if isinstance(n225.columns, pd.MultiIndex):
        n225.columns = n225.columns.get_level_values(0)
    if isinstance(usdjpy.columns, pd.MultiIndex):
        usdjpy.columns = usdjpy.columns.get_level_values(0)

    n225["change_pct"] = n225["Close"].pct_change() * 100
    usdjpy["change_pct"] = usdjpy["Close"].pct_change() * 100

    # 日付をキーに結合してDB保存
    combined = pd.DataFrame(
        {
            "nikkei_close": n225["Close"],
            "nikkei_change_pct": n225["change_pct"],
            "usdjpy_close": usdjpy["Close"],
            "usdjpy_change_pct": usdjpy["change_pct"],
        }
    ).dropna(how="all")

    for date, row in combined.iterrows():
        date_str = date.strftime("%Y-%m-%d")
        cursor.execute(
            """
        INSERT OR REPLACE INTO market_indices (
            date, nikkei_close, nikkei_change_pct, usdjpy_close, usdjpy_change_pct
        ) VALUES (?, ?, ?, ?, ?);
        """,
            (
                date_str,
                None if pd.isna(row["nikkei_close"]) else float(row["nikkei_close"]),
                None if pd.isna(row["nikkei_change_pct"]) else float(row["nikkei_change_pct"]),
                None if pd.isna(row["usdjpy_close"]) else float(row["usdjpy_close"]),
                None if pd.isna(row["usdjpy_change_pct"]) else float(row["usdjpy_change_pct"]),
            ),
        )

    conn.commit()
    print("✅ 市場指標データの保存が完了しました！")


if __name__ == "__main__":
    conn = sqlite3.connect("stock_analysis.db")
    conn.execute("PRAGMA foreign_keys = ON;")

    setup_companies(conn)
    fetch_stock_prices(conn)
    fetch_market_indices(conn)

    conn.close()
    print("【しばかぶ】データ初期投入がすべて完了しました！")