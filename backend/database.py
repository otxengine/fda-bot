import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "./fda_scanner.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from backend.models import FdaEvent, OptionsSignal, HistoricalResult, AlertLog, LearningInsight
    Base.metadata.create_all(bind=engine)
    migrate_db()


def migrate_db():
    """Add new columns to existing tables without losing data."""
    from sqlalchemy import text, inspect as sa_inspect

    NEW_COLUMNS = {
        "options_signals": [
            ("event_pinned_ratio",        "FLOAT DEFAULT 0"),
            ("expiration_score",          "FLOAT DEFAULT 0"),
            ("best_expiry",               "TEXT"),
            ("dominant_strike_type",      "TEXT"),
            ("expiration_breakdown_json", "TEXT"),
            ("p_up_5",                    "FLOAT"),
            ("p_up_10",                   "FLOAT"),
            ("p_down_5",                  "FLOAT"),
            ("p_down_10",                 "FLOAT"),
            ("p_calibration_n",           "INTEGER DEFAULT 0"),
            ("p_confidence",              "TEXT"),
            ("expected_move_pct",         "FLOAT"),
            ("entry_window",              "TEXT"),
            ("liquidity_warning",         "INTEGER DEFAULT 0"),
            ("iv_crush_warning",          "INTEGER DEFAULT 0"),
            ("earnings_overlap",          "INTEGER DEFAULT 0"),
            ("flow_velocity",             "FLOAT DEFAULT 0"),
            ("recommended_strategy",      "TEXT"),
            ("strategy_rationale",        "TEXT"),
            ("strategy_conviction",       "TEXT"),
            ("stock_signal",              "TEXT"),
            ("stock_signal_reason",       "TEXT"),
            ("entry_price",               "FLOAT"),
            ("stop_loss_price",           "FLOAT"),
            ("target_date",               "TEXT"),
        ],
    }

    inspector = sa_inspect(engine)
    with engine.connect() as conn:
        for table, cols in NEW_COLUMNS.items():
            try:
                existing = {c["name"] for c in inspector.get_columns(table)}
            except Exception:
                continue
            for col_name, col_def in cols:
                if col_name not in existing:
                    try:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))
                        conn.commit()
                    except Exception:
                        pass  # already exists or table not created yet
