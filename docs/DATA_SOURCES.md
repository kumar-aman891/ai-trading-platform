# Data Source Strategy

## Primary: Zerodha/Kite

Use Kite as the canonical broker-facing source for Indian instruments, account state, quotes, OHLC and historical candles.
The official Kite MCP currently exposes real-time quotes, LTP, OHLC, historical data, instrument search, holdings, positions, margins, orders, trades and GTT operations. citeturn101632view0

Kite's historical API supports minute, 3/5/10/15/30/60-minute and daily candles. citeturn670511search1

Kite WebSocket provides live quotes including OHLC and market depth; up to 3,000 instruments can be subscribed per connection and a single API key may have up to 3 WebSocket connections. citeturn399458search1

Instrument master should be refreshed daily. Zerodha describes the instrument dump as a daily generated, import-ready CSV and identifies instrument_token as the key for streaming. citeturn399458search2

## Secondary research source: yfinance

Useful for broad market research, global assets, cross-checks and additional fundamentals where available. Do not use it as the canonical execution price feed.

It is subject to Yahoo-side rate limiting and occasional availability issues, so the adapter must implement caching, backoff and a freshness status. yfinance's own issue/help material documents rate limiting and the client code explicitly handles HTTP 429 responses. citeturn301624search0turn301624search1

## Technical indicators

Use pandas-ta-classic as the default indicator library. Current documentation lists 224 indicators plus 62 native candlestick patterns, covering momentum, trend, volatility, volume, cycles and statistics. citeturn670511search0turn670511search3

Keep an internal indicator interface so the library can be replaced or supplemented by TA-Lib or custom numpy implementations later.

## News

Use a provider abstraction. Start with a low-cost/free source where terms permit, plus official company/exchange announcements where available. Store headline, publisher, URL/reference, published time, retrieved time, sentiment/topic tags, and source reliability.

Do not scrape a website merely because it is technically possible. Data rights/licensing must be evaluated before redistribution or commercial use. NSE publishes a data sharing and usage policy that should be reviewed for any NSE-derived distribution use. citeturn670511search6

## Fundamentals

Prefer structured provider data or official filings/annual reports. Store raw evidence references and normalized metrics separately.

Never merge fundamentals across vendors without recording fiscal period, currency, units, and restatement/adjustment status.

## Data-quality policy

Every dataset carries:
- source
- fetched_at
- event_time
- freshness_seconds
- timezone
- adjustment_mode
- confidence/quality state
- checksum/version where practical
