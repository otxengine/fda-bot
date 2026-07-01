"""
Broad biotech universe scanner.

Instead of relying on scrapers that only return a handful of events,
this module maintains a hardcoded universe of ~150 XBI/IBB biotech tickers
and scans them for elevated IV rank + unusual options flow — which are the
strongest proxies for an upcoming FDA catalyst.

Results are returned in the same format as FdaEvent, using
event_type="Catalyst (IV signal)" so they integrate with the existing pipeline.
"""
import logging
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Biotech universe (XBI + IBB components + small/mid-cap FDA catalysts) ──────
BIOTECH_UNIVERSE = [
    # Large / mid cap (XBI/IBB core)
    "MRNA", "REGN", "VRTX", "BIIB", "GILD", "AMGN", "ALNY", "NBIX", "SRPT",
    "BMRN", "EXAS", "RARE", "IONS", "HALO", "RCUS", "AXSM", "CLDX", "EXEL",
    "FATE", "IOVA", "MDGL", "NVAX", "SMMT", "TGTX", "TVTX", "VKTX", "ACAD",
    "AGIO", "AKRO", "ALKS", "ARVN", "ASND", "AVXL", "BBIO", "BCYC", "BGNE",
    "BPMC", "CCCC", "CERE", "CHRS", "CMPS", "CRBU", "DCPH", "DVAX", "EPZM",
    "FDMT", "FGEN", "FUSN", "GOSS", "GTHX", "HRTX", "IBRX", "IMVT", "INO",
    "ITCI", "JAZZ", "KPTI", "KYMR", "LGND", "LNTH", "MGNX", "MNKD", "NKTR",
    "NUVL", "NVCR", "PCRX", "PRAX", "PTCT", "RCKT", "RGEN", "RIGL", "RPTX",
    "RPRX", "SANA", "SGMO", "SLNO", "SURF", "SWTX", "TARS", "TTOO", "UNCY",
    "VCEL", "VSTM", "XOMA", "ZNTL", "AGEN", "ALBO", "ALLO", "ALVO", "ANIK",
    "ARAV", "ARCT", "ARDX", "ARQT", "ATXI", "AUTL", "AVIR", "BCTX", "BDTX",
    "BNGO", "BOLD", "BOLT", "BTAI", "CARA", "CCXI", "CGEM", "CLRB", "CMRX",
    "CNCE", "COGT", "CORT", "CPRX", "CTMX", "DAWN", "DMTK", "DSGN", "ENLV",
    "ERAS", "ETNB", "EVAX", "FLGT", "FNLG", "IGMS", "INBX", "JANX",
    "KDMN", "KNSA", "LHCG", "LUMO", "MRSN", "NRIX", "ORCA", "PGEN", "PNTM",
    "PRME", "PSNL", "RETA", "RLMD", "RMTI", "RNLX", "SAGE", "SCPH", "SEER",
    "SLDB", "SPNV", "SVRA", "TCDA", "THRX", "URGN", "VYNE", "OCGN", "BEAM",
    "CRSP", "NTLA", "EDIT", "AGTC", "AILE", "AURA", "AVRO", "BLUE", "BSGM",
    "CASI", "CDMO", "CEMI", "COCP", "CNTX", "FOLD", "PTGX", "AQST", "OSTX",
    "ACIU", "INBX", "BCYC", "ZNTL",
    # Previously missed — confirmed have options + FDA catalysts
    "ACRS",  # Aclaris Therapeutics ~$800M, dermatology
    "VRDN",  # Viridian Therapeutics ~$2B, ophthalmology
    "EQ",    # Equillium ~$200M, autoimmune
    "TVRD",  # Travere Therapeutics, rare disease
    "VRAX",  # Viracta Therapeutics, oncology
    "NCEL",  # Nuvation Bio, oncology
    "ELAB",  # Elevate Bio / Electra Bio
    "IBIO",  # iBio, oncology biologics
    "NNOX",  # Nano-X Imaging, med devices/AI
    "SHPH",  # Shuttle Pharmaceuticals
    "CLDI",  # Calidi Biotherapeutics
    "MBRX",  # Moleculin Biotech
    "TRIB",  # Triangle Capital/biotech
    "CDIO",  # Cardiol Therapeutics
    "XTLB",  # XTL Biopharmaceuticals
    # Added 2nd round — gainers that were missed + put-dominant decliners to monitor
    "ABVX",  # Abivax SA ~$1-2B, IBD drug (obefazimod) — real FDA catalyst
    "GMAB",  # Genmab $4B+, major oncology/immunology FDA submissions
    "CMMB",  # Chemomab Therapeutics ~$200M, NASH/fibrosis
    "XRTX",  # XORTX Therapeutics, kidney disease
    "NXTC",  # NextCure, oncology immunology
    "CRIS",  # Curis Inc., oncology (hedgehog pathway)
    "VCNX",  # Vaccinex, neurodegeneration + oncology
    "CALC",  # CalciMedica, CRAC channel inhibitor
    "QDEL",  # QuidelOrtho Corp, diagnostics/antigen tests FDA
    "RSLS",  # Reshape Lifesciences, metabolic devices
    "APDN",  # Applied DNA Sciences, biotech
    "EVMN",  # Evommune, autoimmune
    "JSPR",  # Jasper Therapeutics, bone marrow
    "VTGN",  # Vistagen Therapeutics, CNS/anxiety (was -70% binary fail)
    "NVCT",  # Nuvectis Pharma, oncology
    "AGMB",  # AgomAb Therapeutics, HGF/MET
    "CTSO",  # CytoSorbents, critical care devices
    "OMER",  # Omeros Corp, complement system
    "HURA",  # TuHURA Biosciences, oncology
    "ACTU",  # Actuate Therapeutics
    "JSPR",  # Jasper Therapeutics
    "PYXS",  # Pyxis Oncology
    "CUPR",  # Cuprina Holdings
    "BPTH",  # Bio-Path Holdings
    # Small/mid biotech active in 2024-2025 FDA cycle
    "ADMA",  # ADMA Biologics
    "AKBA",  # Akebia Therapeutics
    "AMRX",  # Amneal Pharmaceuticals
    "ANAB",  # AnaptysBio
    "APLS",  # Apellis Pharmaceuticals
    "ARWR",  # Arrowhead Pharmaceuticals
    "ATNF",  # 180 Life Sciences
    "ATRC",  # Atricure
    "BHVN",  # Biohaven
    "BNOX",  # Bioxcel Therapeutics
    "CABA",  # Cabaletta Bio
    "CBPO",  # China Biologic
    "CERS",  # Cerus Corp
    "CHMA",  # Chimera Investment (biotech)
    "CNMD",  # Conmed
    "COGT",  # Cogent Biosciences
    "DAWN",  # Dawn Health
    "DNLI",  # Denali Therapeutics
    "DSGN",  # Design Therapeutics
    "ELVN",  # Enliven Therapeutics
    "ESPR",  # Esperion Therapeutics
    "ETNB",  # 89bio
    "EVLO",  # Evelo Biosciences
    "FATE",  # Fate Therapeutics
    "FBIO",  # Fortress Biotech
    "FHTX",  # Frontier Health Therapy
    "FLNC",  # Fluence Energy
    "FWBI",  # First Wave BioPharma
    "GBIO",  # Generation Bio
    "GKOS",  # Glaukos
    "GRPH",  # Graphite Bio
    "HALO",  # Halozyme
    "HZNP",  # Horizon Therapeutics
    "IDYA",  # IDEAYA Biosciences
    "IMCR",  # Immunocore
    "IMGO",  # Imago Biosciences
    "INVA",  # Innoviva
    "IPSC",  # Century Therapeutics
    "IRON",  # Disc Medicine
    "ITOS",  # iTeos Therapeutics
    "KALA",  # Kala Bio
    "KDNY",  # Chinook Therapeutics
    "KNTE",  # Kineta
    "KROS",  # Keros Therapeutics
    "KYMR",  # Kymera Therapeutics
    "LABP",  # Landos Biopharma
    "LEGN",  # Legend Biotech
    "LYEL",  # Lyell Immunopharma
    "MDXG",  # MiMedx
    "MERUS",  # Merus
    "MGNX",  # MacroGenics
    "MGTX",  # MeiraGTx
    "MIRM",  # Mirum Pharmaceuticals
    "MORF",  # Morphic Therapeutic
    "MRUS",  # Merus NV
    "MYOV",  # Myovant Sciences
    "NERV",  # Minerva Neurosciences
    "NKTR",  # Nektar Therapeutics
    "NUVL",  # Nuvalent
    "NVRO",  # Nevro Corp
    "OABI",  # OcuSense
    "OCUL",  # Ocular Therapeutix
    "OFIX",  # Orthofix
    "ONCT",  # Oncternal Therapeutics
    "ORIC",  # Oric Pharmaceuticals
    "PACB",  # Pacific Biosciences
    "PHAT",  # Phathom Pharmaceuticals
    "PLRX",  # Pliant Therapeutics
    "PMVP",  # PMV Pharmaceuticals
    "PRLD",  # Prelude Therapeutics
    "PRME",  # Prime Medicine
    "PTGX",  # Protagonist Therapeutics
    "RLAY",  # Relay Therapeutics
    "ROIV",  # Roivant Sciences
    "RVMD",  # Revolution Medicines
    "RXRX",  # Recursion Pharmaceuticals
    "SANA",  # Sana Biotechnology
    "SCLX",  # Scilex Holding
    "SDGR",  # Schrodinger
    "SEER",  # Seer Bio
    "SGEN",  # Seagen
    "SILK",  # Silk Road Medical
    "SLNO",  # Soleno Therapeutics
    "SPRY",  # ARS Pharmaceuticals
    "SRRK",  # Scholar Rock
    "STRO",  # Sutro Biopharma
    "SWTX",  # SpringWorks Therapeutics
    "TELA",  # Tela Bio
    "TMCI",  # Treace Medical
    "TNGX",  # Tango Therapeutics
    "TPIC",  # TPI Composites
    "TPTX",  # Turning Point Therapeutics
    "TVTX",  # Travere Therapeutics
    "TYRA",  # Tyra Biosciences
    "UCAR",  # United Auto
    "VERA",  # Vera Therapeutics
    "VNDA",  # Vanda Pharmaceuticals
    "VRTX",  # Vertex (dup — keep)
    "WLDS",  # Weldricks
    "XNCR",  # Xencor
    "YMAB",  # Y-mAbs Therapeutics
    "ZYME",  # Zymeworks
]

# Remove duplicates, keep order
BIOTECH_UNIVERSE = list(dict.fromkeys(BIOTECH_UNIVERSE))


def scan_broad_biotech(
    iv_rank_threshold: float = 60.0,
    max_tickers: int = 150,
    days_lookforward: int = 30,
) -> list[dict]:
    """
    Scan the biotech universe for elevated IV rank + unusual options flow.
    Returns event-like dicts for tickers that look like upcoming catalysts.

    Filters:
        iv_rank >= iv_rank_threshold  (options are pricing in a big move)
        30-day IV significantly above 52-week average
    """
    import yfinance as yf
    from datetime import datetime

    today = date.today()
    results = []
    universe = BIOTECH_UNIVERSE[:max_tickers]

    logger.info(f"Broad biotech scan: checking {len(universe)} tickers...")

    # Batch download basic info via yfinance
    for ticker in universe:
        try:
            t = yf.Ticker(ticker)
            info = t.info

            # Skip if no meaningful options data
            market_cap = info.get("marketCap") or 0
            if market_cap < 10_000_000:  # skip micro-caps < $10M
                continue

            # Check for elevated IV via options chain
            expirations = t.options
            if not expirations:
                continue

            # Look at next 2 expirations for IV
            ivs = []
            for exp in expirations[:2]:
                try:
                    chain = t.option_chain(exp)
                    calls = chain.calls
                    puts = chain.puts
                    if not calls.empty:
                        atm_iv = calls["impliedVolatility"].median()
                        if atm_iv and atm_iv > 0:
                            ivs.append(atm_iv * 100)
                except Exception:
                    continue

            if not ivs:
                continue

            avg_iv = sum(ivs) / len(ivs)

            # Get 52-week historical vol as baseline
            hist = t.history(period="1y")
            if hist.empty or len(hist) < 30:
                continue

            hist["returns"] = hist["Close"].pct_change()
            hist_vol = hist["returns"].std() * (252 ** 0.5) * 100

            if hist_vol <= 0:
                continue

            # IV rank proxy: current IV vs historical vol
            iv_rank_proxy = min(100, (avg_iv / max(hist_vol, 1)) * 50)

            if iv_rank_proxy < iv_rank_threshold:
                continue

            company = info.get("longName") or info.get("shortName") or ticker
            stock_price = info.get("currentPrice") or info.get("regularMarketPrice") or 0

            # Estimate event date: find expiry with highest total OI
            # (the market clusters OI around the catalyst date)
            event_date = today + timedelta(days=days_lookforward)  # fallback
            best_oi = 0
            for exp in expirations[:6]:  # check first 6 expirations
                try:
                    exp_date = date.fromisoformat(exp)
                    if exp_date <= today:
                        continue
                    chain = t.option_chain(exp)
                    total_oi = 0
                    if not chain.calls.empty:
                        total_oi += chain.calls["openInterest"].sum()
                    if not chain.puts.empty:
                        total_oi += chain.puts["openInterest"].sum()
                    if total_oi > best_oi:
                        best_oi = total_oi
                        # Event is likely just before expiry (1-2 days before)
                        event_date = exp_date - timedelta(days=1)
                except Exception:
                    continue

            results.append({
                "ticker":     ticker,
                "company":    company,
                "event_type": "Catalyst (IV signal)",
                "drug_name":  None,
                "indication": None,
                "event_date": event_date,
                "source":     "broad_scan/iv",
                "_iv":        round(avg_iv, 1),
                "_hist_vol":  round(hist_vol, 1),
                "_iv_rank":   round(iv_rank_proxy, 1),
                "_market_cap": market_cap,
            })
            logger.debug(f"  {ticker}: IV={avg_iv:.0f}% hist_vol={hist_vol:.0f}% rank_proxy={iv_rank_proxy:.0f}")

        except Exception as e:
            logger.debug(f"Broad scan skip {ticker}: {e}")
            continue

    # Sort by IV rank proxy descending
    results.sort(key=lambda x: x.get("_iv_rank", 0), reverse=True)
    logger.info(f"Broad biotech scan complete: {len(results)} elevated-IV tickers found")
    return results
