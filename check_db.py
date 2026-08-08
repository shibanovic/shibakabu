# check_db.py
import sqlite3
import pandas as pd

# pandasのコンソール表示が見切れにくくなる設定
pd.set_option("display.max_columns", None)
pd.set_option("display.width", 1000)


def check_database():
    conn = sqlite3.connect("stock_analysis.db")

    print("========================================")
    print("🐶 1. 銘柄マスター (companies)")
    print("========================================")
    df_companies = pd.read_sql_query("SELECT * FROM companies;", conn)
    print(df_companies[["ticker", "code", "name", "sector"]])
    print("\n")

    print("========================================")
    print("🐶 2. 最新の株価・指標データ (daily_prices 直近5件)")
    print("========================================")
    df_prices = pd.read_sql_query(
        """
        SELECT ticker, date, close, change_pct, sma_25, rsi_14 
        FROM daily_prices 
        ORDER BY date DESC 
        LIMIT 5;
    """,
        conn,
    )
    print(df_prices)
    print("\n")

    print("========================================")
    print("🐶 3. 最新の市場指標データ (market_indices 直近5件)")
    print("========================================")
    df_market = pd.read_sql_query(
        """
        SELECT * FROM market_indices 
        ORDER BY date DESC 
        LIMIT 5;
    """,
        conn,
    )
    print(df_market)
    print("\n")

    # レコード件数のカウント
    price_count = pd.read_sql_query(
        "SELECT COUNT(*) as count FROM daily_prices;", conn
    )["count"][0]
    print("========================================")
    print(f"📊 総株価レコード数: {price_count:,} 件")
    print("========================================")

    conn.close()


if __name__ == "__main__":
    check_database()