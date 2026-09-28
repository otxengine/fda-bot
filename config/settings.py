"""Central settings: API keys, DB path, and paths to the YAML config files.

Loaded once via get_settings() (lru_cache'd) from a .env file at the project
root (see .env.example). Never hardcode a key or path anywhere else.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = Path(__file__).parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Official free APIs
    fred_api_key: str = ""
    bls_api_key: str = ""
    bea_api_key: str = ""

    # Phase 3, optional
    nasdaq_data_link_api_key: str = ""
    benzinga_api_key: str = ""

    # Chat/advisor page (paid, usage-based — see .env.example note)
    anthropic_api_key: str = ""

    # The user's own separate fda-bot service (github.com/otxengine/fda-bot),
    # deployed on Render — see connectors/fda_bot.py. No API key needed, its
    # /api/* surface is public; overridable only if the user ever redeploys
    # it under a different URL.
    fda_bot_base_url: str = "https://fda-bot.onrender.com"

    # Storage (SQLite, WAL mode — see storage/db.py's module docstring for why
    # this isn't DuckDB, which was the original plan's first choice)
    finresearch_db_path: str = "data/finresearch.db"

    @property
    def db_path(self) -> Path:
        p = Path(self.finresearch_db_path)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def connectors_config_path(self) -> Path:
        return CONFIG_DIR / "connectors.yaml"

    @property
    def macro_thresholds_path(self) -> Path:
        return CONFIG_DIR / "macro_thresholds.yaml"

    @property
    def screener_criteria_path(self) -> Path:
        return CONFIG_DIR / "screener_criteria.yaml"

    @property
    def additional_indicators_path(self) -> Path:
        return CONFIG_DIR / "additional_indicators.yaml"


@lru_cache
def get_settings() -> Settings:
    return Settings()
