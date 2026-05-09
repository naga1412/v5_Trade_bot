"""SP-8 Phase H — Indian tax compliance for crypto futures trades.

Spec §8. Each closed live_trade produces one tax_events row:
- FIFO cost basis matched against prior buys per (user, asset, exchange)
- INR-converted via cached USD/INR spot rate
- 1% TDS computed and tracked (not paid — caller deposits to govt portal)
- ITR-3 Schedule VDA CSV export at year-end
"""
