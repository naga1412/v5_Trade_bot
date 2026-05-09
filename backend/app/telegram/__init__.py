"""SP-8 Phase G — Telegram per-trade approval flow.

Spec §7.2 + §7.4. Each candidate trade fires a message to the user
with adjustable-leverage buttons + 30s auto-skip. Builds on the
SP-4 Phase E polling worker (app/ops/telegram_bot.py).
"""
