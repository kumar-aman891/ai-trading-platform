# Compliance / Broker Constraints

This is an engineering control document, not legal advice. Before enabling live trading, verify current broker and exchange requirements for the exact account, API plan, client type, segment, and algorithm architecture.

SEBI's February 4, 2025 circular established a framework for safer retail participation in algorithmic trading, with implementation standards subsequently published by exchanges. citeturn900239search0turn900239search44

The NSE implementation material includes requirements relevant to client-generated APIs such as mapped static IPs and daily logout of API sessions. citeturn900239search44

NSE FAQs also state that API orders are treated as algo orders and describe OPS/order-type constraints that can matter to system design. citeturn900239search43

Engineering requirements:
- document the applicable broker/API mode
- document static IP configuration
- document session lifecycle
- enforce order-rate limits independent of broker limits
- record strategy/algo identifiers where required
- retain complete order and decision audit logs
- provide a kill switch
- review live deployment configuration against current broker/exchange rules before each material production change
