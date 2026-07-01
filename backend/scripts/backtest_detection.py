"""
Backtest: would the unified scanner have caught these examples BEFORE the move?

Run:
    python -m backend.scripts.backtest_detection

Checks each ticker N days BEFORE its actual big move day to see if
the volume-spike scanner and/or options scanner would have fired a BUY.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from datetime import date, timedelta, datetime

# ── Example moves from the user's lists ───────────────────────────────────────
# (ticker, approx_move_date, pct_change, has_options_expected)
EXAMPLES = [
    # Gainers — first batch
    ("HOTH",  date(2026, 6, 25), +92.0,  False),  # Hoth Therapeutics
    ("ADTX",  date(2026, 6, 25), +150.5, False),  # Aditxt — penny
    ("LIPO",  date(2026, 6, 25), +97.8,  False),  # Lipella Pharma
    ("SYRA",  date(2026, 6, 25), +48.3,  False),  # Syra Health
    ("CUPR",  date(2026, 6, 25), +47.8,  True),   # Cuprina
    ("ABVX",  date(2026, 6, 25), +38.6,  True),   # Abivax — big cap
    # Gainers — second batch
    ("CMMB",  date(2026, 6, 26), +24.1,  True),   # Chemomab
    ("AKBA",  date(2026, 6, 26), +7.2,   True),   # Akebia
    ("BOLD",  date(2026, 6, 26), +6.9,   True),   # Boundless Bio
    ("NNOX",  date(2026, 6, 26), +24.0,  True),   # Nano-X
    # Gainers — third batch
    ("UPC",   date(2026, 6, 27), +311.5, False),  # Universe Pharma
    ("CRIS",  date(2026, 6, 27), +37.7,  True),   # Curis
    ("DCOY",  date(2026, 6, 27), +73.8,  False),  # Decoy
    ("PYXS",  date(2026, 6, 28), +24.2,  True),   # Pyxis Oncology
    ("CALC",  date(2026, 6, 28), +30.3,  False),  # CalciMedica
    # Decliners (bot should NOT have sent BUY for these)
    ("VTGN",  date(2026, 6, 25), -70.3,  True),   # Vistagen
    ("UNCY",  date(2026, 6, 25), -39.1,  True),   # Unicycive
    ("NVCT",  date(2026, 6, 25), -35.5,  True),   # Nuvectis
]

CHECK_DAYS_BEFORE = [1, 2, 3]   # check on these days before the move


def check_ticker(ticker: str, move_date: date, pct_change: float, has_options: bool):
    """Run detection logic for N days before the move. Report what would have been found."""
    import yfinance as yf

    t = yf.Ticker(ticker)
    info = t.info
    price_on_move = info.get("currentPrice") or info.get("regularMarketPrice") or 0
    market_cap = info.get("marketCap") or 0
    expirations = t.options or []

    print(f"\n{'='*60}")
    direction = "📈 GAINER" if pct_change > 0 else "📉 DECLINER"
    print(f"{direction} {ticker:6s}  {pct_change:+.1f}%  move_date={move_date}")
    print(f"  price=${price_on_move:.4f}  mktcap=${market_cap/1e6:.1f}M  options={len(expirations)} expirations")

    # Determine detection path
    use_options = bool(expirations and price_on_move >= 3.0 and market_cap >= 10_000_000)
    path = "OPTIONS" if use_options else "VOLUME-SPIKE"
    print(f"  → detection path: {path}")

    # For each check day BEFORE the move
    for days_before in CHECK_DAYS_BEFORE:
        check_date = move_date - timedelta(days=days_before)
        print(f"\n  Check {days_before}d before ({check_date}):")

        # Volume check (always)
        try:
            hist = t.history(start=check_date - timedelta(days=25), end=check_date + timedelta(days=1))
            if hist.empty or len(hist) < 3:
                print(f"    ⚠️  Not enough history")
                continue

            # Volume on check_date vs prior 20-day avg
            check_vol = hist["Volume"].iloc[-1]
            avg_vol = hist["Volume"].iloc[:-1].tail(20).mean()
            spike = check_vol / avg_vol if avg_vol > 0 else 0

            # 3-day momentum ending on check_date
            price_now  = hist["Close"].iloc[-1]
            price_3ago = hist["Close"].iloc[-4] if len(hist) >= 4 else hist["Close"].iloc[0]
            mom = ((price_now - price_3ago) / price_3ago * 100) if price_3ago > 0 else 0

            print(f"    volume: {check_vol:,.0f} ({spike:.1f}x avg)")
            print(f"    momentum 3d: {mom:+.1f}%  price: ${price_now:.4f}")

            if use_options:
                # Check options C/P ratio
                if expirations:
                    try:
                        chain = t.option_chain(expirations[0])
                        call_vol = chain.calls["volume"].sum()
                        put_vol  = chain.puts["volume"].sum()
                        cp = call_vol / put_vol if put_vol > 0 else (2.0 if call_vol > 0 else 1.0)
                        iv_calls = chain.calls["impliedVolatility"].median() * 100
                        print(f"    C/P ratio: {cp:.2f}  IV: {iv_calls:.0f}%  call_vol={call_vol:.0f}  put_vol={put_vol:.0f}")
                        if pct_change > 0 and cp >= 1.8 and spike >= 1.5:
                            print(f"    ✅ WOULD HAVE CAUGHT: options C/P={cp:.2f} + vol×{spike:.1f}")
                        elif pct_change > 0 and cp < 1.0 and spike >= 1.5:
                            print(f"    ⚡ volume spike detected but PUT-heavy — ambiguous")
                        elif pct_change < 0 and cp < 0.65:
                            print(f"    ✅ PUT flow would have filtered out (C/P={cp:.2f})")
                        else:
                            print(f"    ❌ would NOT have caught (score too low)")
                    except Exception as ex:
                        print(f"    options fetch error: {ex}")
            else:
                # Volume-spike path
                from backend.scrapers.penny_catalyst_scanner import (
                    _volume_score, _momentum_score, _proximity_score
                )
                days_until = (move_date - check_date).days
                s_vol  = _volume_score(spike)
                s_mom  = _momentum_score(mom)
                s_prox = _proximity_score(days_until)
                composite = s_vol*0.40 + s_mom*0.25 + s_prox*0.15
                print(f"    score: vol={s_vol:.0f} mom={s_mom:.0f} prox={s_prox:.0f} → composite={composite:.0f}")
                if pct_change > 0 and spike >= 2.0 and composite >= 45:
                    print(f"    ✅ WOULD HAVE CAUGHT: vol×{spike:.1f} score={composite:.0f}")
                elif pct_change > 0 and spike < 2.0:
                    print(f"    ❌ volume spike too low (×{spike:.1f}) — no signal")
                elif pct_change > 0:
                    print(f"    ❌ composite score too low ({composite:.0f} < 45)")

        except Exception as e:
            print(f"    error: {e}")

    # Summary for decliners
    if pct_change < 0:
        print(f"\n  ℹ️  DECLINER — bot should NOT have alerted. C/P < 1.0 would have suppressed BUY.")


def main():
    print("=" * 60)
    print("BACKTEST: Would the unified scanner have caught these?")
    print(f"Checking {len(EXAMPLES)} tickers ({sum(1 for _,_,p,_ in EXAMPLES if p>0)} gainers, "
          f"{sum(1 for _,_,p,_ in EXAMPLES if p<0)} decliners)")
    print("=" * 60)

    caught = 0
    missed = 0
    false_positives = 0
    correctly_skipped = 0

    for ticker, move_date, pct, has_options in EXAMPLES:
        check_ticker(ticker, move_date, pct, has_options)
        # (tallying done manually from output for now)

    print("\n\nDone. Review output above.")
    print("Look for ✅ = would catch, ❌ = would miss, ℹ️ = correctly ignored.")


if __name__ == "__main__":
    main()
