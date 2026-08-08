# init_db.py (改善全反映・最終版)
import sqlite3


def create_tables():
    conn = sqlite3.connect("stock_analysis.db")
    cursor = conn.cursor()

    cursor.execute("PRAGMA foreign_keys = ON;")

    # 1. 銘柄マスター (ticker: '7203.T', code: '7203' を分離)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS companies (
        ticker TEXT PRIMARY KEY, -- yfinance用 ('7203.T')
        code TEXT NOT NULL,      -- 表示・検索用 ('7203')
        name TEXT NOT NULL,      -- 銘柄名 ('トヨタ自動車')
        sector TEXT,
        market TEXT,
        is_active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (DATETIME('now', 'localtime'))
    );
    """)

    # 2. テーママスター
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS themes (
        theme_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        description TEXT
    );
    """)

    # 3. 中間テーブル (銘柄 × テーマ)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS company_themes (
        ticker TEXT,
        theme_id INTEGER,
        PRIMARY KEY (ticker, theme_id),
        FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE,
        FOREIGN KEY (theme_id) REFERENCES themes(theme_id) ON DELETE CASCADE
    );
    """)

    # 4. 個別株の日足・財務・テクニカル (前日比 change_pct を追加)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS daily_prices (
        ticker TEXT,
        date TEXT,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        change_pct REAL, -- 前日比(%)
        volume INTEGER,
        per REAL,
        pbr REAL,
        roe REAL,
        dividend_yield REAL,
        credit_ratio REAL,
        sma_25 REAL,
        sma_75 REAL,
        rsi_14 REAL,
        macd REAL,
        macd_signal REAL,
        bb_upper REAL,
        bb_lower REAL,
        atr_14 REAL,
        PRIMARY KEY (ticker, date),
        FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE
    );
    """)

    # 5. 市場指標テーブル (日経平均・TOPIX・ドル円など)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS market_indices (
        date TEXT PRIMARY KEY,
        nikkei_close REAL,
        nikkei_change_pct REAL,
        topix_close REAL,
        topix_change_pct REAL,
        usdjpy_close REAL,
        usdjpy_change_pct REAL
    );
    """)

    # 6. 日々の自動スクリーニング結果ログ
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS screening_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT,
        date TEXT NOT NULL,
        long_score REAL DEFAULT 0,
        swing_score REAL DEFAULT 0,
        day_score REAL DEFAULT 0,
        primary_strategy TEXT,
        reasons TEXT,
        is_notified INTEGER DEFAULT 0,
        FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE
    );
    """)

    # 7. 売買ログ（約定履歴）テーブル (ナンピン・分割売却対応)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL,
        type TEXT NOT NULL, -- 'BUY' または 'SELL'
        trade_date TEXT NOT NULL,
        price REAL NOT NULL,
        quantity INTEGER NOT NULL,
        memo TEXT,
        created_at TEXT DEFAULT (DATETIME('now', 'localtime')),
        FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE
    );
    """)

    # 8. バックテスト（過去検証）の条件・結果ログ
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS backtest_results (
        backtest_id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_name TEXT NOT NULL,
        parameters TEXT NOT NULL,
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        total_trades INTEGER,
        win_rate REAL,
        total_return REAL,
        profit_factor REAL,
        max_drawdown REAL,
        memo TEXT,
        created_at TEXT DEFAULT (DATETIME('now', 'localtime'))
    );
    """)

    # インデックス作成（検索高速化）
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_prices_date ON daily_prices(date);"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_results_date ON"
        " screening_results(date);"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_trans_ticker ON"
        " transactions(ticker);"
    )

    conn.commit()
    conn.close()
    print(
        "【しばかぶ】全8テーブルの作成が完了しました！(stock_analysis.db)"
    )


if __name__ == "__main__":
    create_tables()