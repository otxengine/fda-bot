"""Sector + quantitative idea-generation screener — replicates the exact
methodology from the user's own Excel/Zacks guide (מדריך ניתוח קוונטיטיבי
באקסל / מדריך להפקת רעיונות באמצעות סינון באקסל), automated against our own
data pipeline instead of a manual Zacks screener + Excel workbook. See
dashboard/pages/6_Idea_Generation.py.

The guide's process, in its own 3 parts:
  Part A (pull data)   -> already automated: connectors/yahoo_finance.py +
                           universe/sp500_fetch.py populate everything below.
  Part B (organize)    -> this module's build_quant_table(): computes the
                           exact same derived columns the guide's Excel
                           formulas do (P/E trailing/F1/F2, P/S trailing/next
                           year, SG1, EG1, EG2, PEG, PEG_NEXT).
  Part C (analyze)      -> sector_growth_overview() (guide's sector-level
                           step) and stocks_in_sector() (guide's stock-level
                           step), both on the dashboard page.

Field name mapping, guide's Zacks names -> our schema:
  F0 (last completed FY EPS actual)   -> fundamentals_history.eps (latest snapshot, i.e. trailingEps)
  F1 (current FY consensus EPS)       -> earnings_estimates period='0y', metric='earnings'
  F2 (next FY consensus EPS)          -> earnings_estimates period='+1y', metric='earnings'
  Annual Sales (last completed FY)    -> fundamentals_history.revenue_ttm (latest snapshot)
  F(1) Consensus Sales Est (next FY)  -> earnings_estimates period='+1y', metric='revenue'
Only populated for symbols fetched with include_estimates=True — currently
BOTH the watchlist/sector priority job and the full S&P 500 universe job
(see scheduler/jobs.py and connectors/yahoo_finance.py) per the user's choice
to prioritize full-market coverage over fetch speed.
"""

from __future__ import annotations

import sqlite3

import pandas as pd

# The guide describes "extreme values distorting the average, usually from a
# very low prior-year base" without giving an exact number — this is our
# chosen, visible, easy-to-retune cutoff (300% growth) for what counts as
# large enough to likely be a base-effect artifact worth a second look,
# rather than a real growth signal.
OUTLIER_GROWTH_THRESHOLD = 3.0


def _latest_fundamentals(conn: sqlite3.Connection) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT f.symbol, f.eps AS f0_eps, f.revenue_ttm AS annual_sales
        FROM fundamentals_history f
        INNER JOIN (
            SELECT symbol, max(as_of) AS max_as_of FROM fundamentals_history GROUP BY symbol
        ) latest ON f.symbol = latest.symbol AND f.as_of = latest.max_as_of
        """
    ).fetchall()
    return pd.DataFrame(rows, columns=["symbol", "f0_eps", "annual_sales"])


def _latest_estimates(conn: sqlite3.Connection) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT e.symbol, e.period, e.metric, e.avg_estimate
        FROM earnings_estimates e
        INNER JOIN (
            SELECT symbol, max(as_of) AS max_as_of FROM earnings_estimates GROUP BY symbol
        ) latest ON e.symbol = latest.symbol AND e.as_of = latest.max_as_of
        WHERE e.period IN ('0y', '+1y')
        """
    ).fetchall()
    df = pd.DataFrame(rows, columns=["symbol", "period", "metric", "avg_estimate"])
    if df.empty:
        return pd.DataFrame(columns=["symbol", "f1_eps", "f2_eps", "next_year_sales"])
    pivoted = df.pivot_table(index="symbol", columns=["metric", "period"], values="avg_estimate")
    out = pd.DataFrame(index=pivoted.index)
    out["f1_eps"] = pivoted[("earnings", "0y")] if ("earnings", "0y") in pivoted.columns else None
    out["f2_eps"] = pivoted[("earnings", "+1y")] if ("earnings", "+1y") in pivoted.columns else None
    out["next_year_sales"] = pivoted[("revenue", "+1y")] if ("revenue", "+1y") in pivoted.columns else None
    return out.reset_index()


def _growth_with_sign_flip_handling(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Exactly mirrors the guide's EG1/EG2 formula:
    =IF(base<0, IF(next>0, 99%, -99%), IF(next<0, -99%, next/base - 1))
    A negative base makes an ordinary % growth figure meaningless (e.g.
    -$1 -> $1 EPS isn't "a 200% decline"), so the guide caps it at +/-99%
    instead, distinguishing "went from loss to profit" from "stayed in loss"."""
    result = pd.Series(index=numerator.index, dtype=float)
    base_negative = denominator < 0
    next_negative = numerator < 0
    normal = ~base_negative & ~next_negative & (denominator != 0)

    result[base_negative & (numerator > 0)] = 0.99
    result[base_negative & (numerator <= 0)] = -0.99
    result[~base_negative & next_negative] = -0.99
    result[normal] = numerator[normal] / denominator[normal] - 1
    return result


def build_quant_table(conn: sqlite3.Connection) -> pd.DataFrame:
    """One row per symbol in `instruments`, with every metric from the
    guide's Part B. Symbols missing price/fundamentals/estimate data simply
    get NaN in the derived columns rather than being dropped — filtering
    happens downstream (sector_growth_overview / stocks_in_sector)."""
    instruments = pd.DataFrame(
        conn.execute("SELECT symbol, name, sector, industry FROM instruments").fetchall(),
        columns=["symbol", "name", "sector", "industry"],
    )
    quotes = pd.DataFrame(
        conn.execute("SELECT symbol, price, market_cap FROM quotes_latest").fetchall(),
        columns=["symbol", "price", "market_cap"],
    )
    fundamentals = _latest_fundamentals(conn)
    estimates = _latest_estimates(conn)

    df = instruments.merge(quotes, on="symbol", how="left")
    df = df.merge(fundamentals, on="symbol", how="left")
    df = df.merge(estimates, on="symbol", how="left")

    # An entirely empty fundamentals/estimates table (nothing fetched yet)
    # makes _latest_fundamentals/_latest_estimates return object-dtype NaN
    # columns (pandas doesn't infer float64 for an empty frame's columns),
    # which crashes the sign-flip growth math below with a dtype TypeError
    # once merged in. Coerce explicitly so this always behaves as plain
    # missing data, not a crash.
    numeric_cols = ["price", "market_cap", "f0_eps", "annual_sales", "f1_eps", "f2_eps", "next_year_sales"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    def _safe_div(numer: pd.Series, denom: pd.Series) -> pd.Series:
        return numer / denom.where(denom != 0)

    df["pe_trailing"] = _safe_div(df["price"], df["f0_eps"])
    df["pe_f1"] = _safe_div(df["price"], df["f1_eps"])
    df["pe_f2"] = _safe_div(df["price"], df["f2_eps"])
    df["ps_trailing"] = _safe_div(df["market_cap"], df["annual_sales"])
    df["ps_next_year"] = _safe_div(df["market_cap"], df["next_year_sales"])

    df["sales_growth_next_year"] = _safe_div(df["next_year_sales"], df["annual_sales"]) - 1
    df["earnings_growth_f1"] = _growth_with_sign_flip_handling(df["f1_eps"], df["f0_eps"])
    df["earnings_growth_f2"] = _growth_with_sign_flip_handling(df["f2_eps"], df["f1_eps"])

    # PEG ratios: guide uses TRAILING P/E for both (not forward P/E) — matching
    # its formulas exactly: =IF(pe_trailing>0, pe_trailing/(EG*100), "")
    df["peg_ratio"] = df["pe_trailing"].where(df["pe_trailing"] > 0) / (df["earnings_growth_f1"] * 100)
    df["peg_ratio_next"] = df["pe_trailing"].where(df["pe_trailing"] > 0) / (df["earnings_growth_f2"] * 100)

    return df


def _trimmed(series: pd.Series, threshold: float = OUTLIER_GROWTH_THRESHOLD) -> pd.Series:
    return series.where(series.abs() <= threshold)


def sector_growth_overview(quant_df: pd.DataFrame) -> pd.DataFrame:
    """Guide's Part C step 1-3: average SG1/EG1/EG2 per sector, computed
    with base-effect outliers excluded from the average (but never from the
    underlying data — see stocks_in_sector). Sorted by EG1 descending, with
    an 'above_overall_average' flag for the chart the guide describes."""
    valid = quant_df.dropna(subset=["sector"]).copy()
    valid["sg1_trimmed"] = _trimmed(valid["sales_growth_next_year"])
    valid["eg1_trimmed"] = _trimmed(valid["earnings_growth_f1"])
    valid["eg2_trimmed"] = _trimmed(valid["earnings_growth_f2"])

    grouped = valid.groupby("sector").agg(
        avg_sales_growth=("sg1_trimmed", "mean"),
        avg_earnings_growth_f1=("eg1_trimmed", "mean"),
        avg_earnings_growth_f2=("eg2_trimmed", "mean"),
        n_companies=("symbol", "count"),
    ).reset_index()

    overall_avg_eg1 = grouped["avg_earnings_growth_f1"].mean()
    grouped["above_overall_average"] = grouped["avg_earnings_growth_f1"] > overall_avg_eg1
    grouped = grouped.sort_values("avg_earnings_growth_f1", ascending=False).reset_index(drop=True)
    grouped.attrs["overall_avg_earnings_growth_f1"] = overall_avg_eg1
    return grouped


def stocks_in_sector(quant_df: pd.DataFrame, sector: str) -> pd.DataFrame:
    """Guide's Part C step 4-6: every stock in one sector, with a flag for
    whether it beats that SECTOR's own (outlier-trimmed) average growth, and
    a separate 'possible_base_effect_outlier' flag — the guide is explicit
    that an outlier company stays visible in the sheet, it's only excluded
    from the average math."""
    sector_df = quant_df[quant_df["sector"] == sector].copy()
    if sector_df.empty:
        return sector_df

    for col in ("sales_growth_next_year", "earnings_growth_f1", "earnings_growth_f2"):
        sector_df[f"{col}_outlier"] = sector_df[col].abs() > OUTLIER_GROWTH_THRESHOLD

    sg1_avg = _trimmed(sector_df["sales_growth_next_year"]).mean()
    eg1_avg = _trimmed(sector_df["earnings_growth_f1"]).mean()
    eg2_avg = _trimmed(sector_df["earnings_growth_f2"]).mean()

    sector_df["above_sector_avg_sales_growth"] = sector_df["sales_growth_next_year"] > sg1_avg
    sector_df["above_sector_avg_earnings_growth"] = sector_df["earnings_growth_f1"] > eg1_avg

    sector_df.attrs["sector_avg_sales_growth_next_year"] = sg1_avg
    sector_df.attrs["sector_avg_earnings_growth_f1"] = eg1_avg
    sector_df.attrs["sector_avg_earnings_growth_f2"] = eg2_avg
    return sector_df.sort_values("earnings_growth_f1", ascending=False).reset_index(drop=True)
