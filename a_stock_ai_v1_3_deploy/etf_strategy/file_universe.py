from __future__ import annotations

import pandas as pd


# Static ETF universe transcribed from wufu code.txt. The original dynamic
# all-market expansion is deliberately excluded, but the original global/china
# classification is retained instead of inferring it from product names.
GLOBAL_ETF_SYMBOLS = frozenset(
    {
        "518880.SH", "501018.SH", "161226.SZ", "159985.SZ", "159980.SZ",
        "513310.SH", "159518.SZ", "159509.SZ", "513100.SH", "513520.SH",
        "513500.SH", "159502.SZ", "513400.SH", "513030.SH", "513290.SH",
        "520830.SH", "159529.SZ",
    }
)

CHINA_ETF_SYMBOLS = frozenset(
    {
        "513090.SH", "513120.SH", "513180.SH", "513330.SH", "513750.SH", "159892.SZ",
        "513190.SH", "159605.SZ", "513630.SH", "159323.SZ", "510900.SH", "513920.SH",
        "513970.SH", "511380.SH", "512050.SH", "510500.SH", "159915.SZ", "510300.SH",
        "512100.SH", "159949.SZ", "588080.SH", "159967.SZ", "588220.SH", "563300.SH",
        "510760.SH", "588200.SH", "515880.SH", "159981.SZ", "512880.SH", "513350.SH",
        "159326.SZ", "159516.SZ", "159206.SZ", "512480.SH", "159363.SZ", "159870.SZ",
        "512400.SH", "159755.SZ", "588170.SH", "159992.SZ", "159995.SZ", "512890.SH",
        "515220.SH", "159566.SZ", "159819.SZ", "512800.SH", "512690.SH", "515050.SH",
        "562500.SH", "512170.SH", "517520.SH", "159869.SZ", "512070.SH", "159611.SZ",
        "562800.SH", "515120.SH", "512010.SH", "510880.SH", "515790.SH", "515980.SH",
        "512660.SH", "159928.SZ", "512710.SH", "560860.SH", "515030.SH", "159766.SZ",
        "159218.SZ", "159852.SZ", "516160.SH", "516150.SH", "159227.SZ", "159583.SZ",
        "588790.SH", "159865.SZ", "512980.SH", "159851.SZ", "561360.SH", "561980.SH",
        "562590.SH", "512200.SH", "159732.SZ", "159667.SZ", "516510.SH", "159840.SZ",
        "159998.SZ", "159825.SZ", "512670.SH", "159883.SZ", "515210.SH", "515400.SH",
        "159256.SZ", "561330.SH", "515170.SH", "159638.SZ", "516520.SH", "513360.SH",
        "516190.SH",
    }
)

FILE_ETF_SYMBOLS = GLOBAL_ETF_SYMBOLS | CHINA_ETF_SYMBOLS
DEFENSIVE_ETF_SYMBOL = "511880.SH"


def bucket_for_symbol(symbol: str) -> str:
    normalized = str(symbol or "").strip().upper()
    if normalized in GLOBAL_ETF_SYMBOLS:
        return "global"
    if normalized in CHINA_ETF_SYMBOLS:
        return "china"
    if normalized == DEFENSIVE_ETF_SYMBOL:
        return "defensive"
    return "unknown"


def restrict_to_file_universe(universe: pd.DataFrame) -> pd.DataFrame:
    if universe.empty:
        return universe
    return universe[universe["symbol"].astype(str).str.upper().isin(FILE_ETF_SYMBOLS)].copy()
