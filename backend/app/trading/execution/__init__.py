"""SP-8 Phase J — live trade execution layer.

Wires the existing modules (modes, leverage, position sizing, kill
switches, vault, Binance client, Telegram signals, tax events) into
a single dispatcher that turns a bot signal into a real Binance order
when the user's mode permits it.
"""
