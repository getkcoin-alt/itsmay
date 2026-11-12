"""Seed catalogue of simulated instruments.

Names refer to well-known listed companies, indices, currencies and crypto
assets purely so the demo is legible. Every price is SIMULATED and has no
relationship to any live market.
"""

# symbol, name, sector, base_price, annual vol, base daily volume
STOCKS = [
    ("RELIANCE", "Reliance Industries", "Energy", 1418.60, 0.24, 9_800_000),
    ("TCS", "Tata Consultancy Services", "IT Services", 3046.20, 0.22, 2_600_000),
    ("HDFCBANK", "HDFC Bank", "Banking", 988.35, 0.20, 14_000_000),
    ("INFY", "Infosys", "IT Services", 1482.90, 0.25, 6_900_000),
    ("ICICIBANK", "ICICI Bank", "Banking", 1361.40, 0.22, 11_000_000),
    ("SBIN", "State Bank of India", "Banking", 881.75, 0.27, 12_500_000),
    ("BHARTIARTL", "Bharti Airtel", "Telecom", 1952.10, 0.23, 5_300_000),
    ("ITC", "ITC", "FMCG", 409.85, 0.19, 13_200_000),
    ("LT", "Larsen & Toubro", "Capital Goods", 3812.40, 0.24, 1_900_000),
    ("HINDUNILVR", "Hindustan Unilever", "FMCG", 2451.30, 0.18, 1_600_000),
    ("BAJFINANCE", "Bajaj Finance", "Financial Services", 1012.55, 0.31, 6_100_000),
    ("MARUTI", "Maruti Suzuki India", "Automobile", 15980.00, 0.23, 450_000),
    ("SUNPHARMA", "Sun Pharmaceutical", "Pharma", 1702.45, 0.21, 2_300_000),
    ("TITAN", "Titan Company", "Consumer Durables", 3598.70, 0.25, 1_100_000),
    ("ASIANPAINT", "Asian Paints", "Consumer Durables", 2398.15, 0.22, 1_300_000),
    ("WIPRO", "Wipro", "IT Services", 245.60, 0.26, 9_400_000),
    ("AXISBANK", "Axis Bank", "Banking", 1198.25, 0.24, 7_800_000),
    ("KOTAKBANK", "Kotak Mahindra Bank", "Banking", 2104.80, 0.22, 3_200_000),
    ("NTPC", "NTPC", "Power", 334.90, 0.24, 15_000_000),
    ("ADANIPORTS", "Adani Ports & SEZ", "Infrastructure", 1447.35, 0.33, 3_500_000),
]

# symbol, name, base INR price, vol, decimals, min qty, base volume
CRYPTO = [
    ("BTC", "Bitcoin", 9_512_000.0, 0.52, 2, "0.0001", 380),
    ("ETH", "Ethereum", 341_800.0, 0.66, 2, "0.001", 5_200),
    ("BNB", "BNB", 95_400.0, 0.55, 2, "0.01", 9_800),
    ("SOL", "Solana", 16_480.0, 0.82, 2, "0.01", 61_000),
    ("XRP", "XRP", 219.45, 0.78, 2, "1", 9_300_000),
    ("ADA", "Cardano", 55.32, 0.80, 2, "1", 18_000_000),
    ("DOGE", "Dogecoin", 15.84, 0.92, 4, "1", 64_000_000),
    ("AVAX", "Avalanche", 2_296.0, 0.86, 2, "0.01", 210_000),
    ("DOT", "Polkadot", 298.60, 0.80, 2, "0.1", 1_400_000),
    ("LINK", "Chainlink", 1_498.0, 0.82, 2, "0.01", 520_000),
    ("LTC", "Litecoin", 8_520.0, 0.70, 2, "0.01", 110_000),
]

# symbol, name, quote ccy, base price, vol, decimals, tick, base volume (lots)
FOREX = [
    ("USDINR", "US Dollar / Indian Rupee", "INR", 88.4750, 0.045, 4, "0.0025", 2_400_000),
    ("EURINR", "Euro / Indian Rupee", "INR", 102.6400, 0.065, 4, "0.0025", 420_000),
    ("GBPINR", "British Pound / Indian Rupee", "INR", 117.7200, 0.075, 4, "0.0025", 310_000),
    ("EURUSD", "Euro / US Dollar", "USD", 1.16020, 0.075, 5, "0.00001", 9_500_000),
    ("GBPUSD", "British Pound / US Dollar", "USD", 1.33050, 0.085, 5, "0.00001", 6_200_000),
    ("USDJPY", "US Dollar / Japanese Yen", "JPY", 152.340, 0.095, 3, "0.001", 7_400_000),
    ("AUDUSD", "Australian Dollar / US Dollar", "USD", 0.65720, 0.095, 5, "0.00001", 3_100_000),
    ("USDCHF", "US Dollar / Swiss Franc", "CHF", 0.80150, 0.080, 5, "0.00001", 2_000_000),
    ("USDCAD", "US Dollar / Canadian Dollar", "CAD", 1.40120, 0.065, 5, "0.00001", 2_600_000),
]

# symbol, name, base level, vol, option config
INDICES = [
    ("NIFTY", "NIFTY 50", 25_720.0, 0.13, {"lot_size": 75, "strike_step": 50, "base_iv": 0.125, "weekly": True}),
    ("SENSEX", "BSE SENSEX", 84_110.0, 0.13, None),
    ("BANKNIFTY", "NIFTY BANK", 57_780.0, 0.15, {"lot_size": 35, "strike_step": 100, "base_iv": 0.145, "weekly": False}),
    ("FINNIFTY", "NIFTY FIN SERVICE", 27_350.0, 0.15, None),
    ("INDIAVIX", "India VIX", 12.45, 0.85, None),
]

STOCK_OPTIONS = {
    "RELIANCE": {"lot_size": 500, "strike_step": 20, "base_iv": 0.235, "weekly": False},
}
