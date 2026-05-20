"""Shared decision gates that filter signals before they reach the
shadow worker or live dispatcher.

Each gate is a pure function with no I/O. Call sites:
  * `app.shadow.worker._maybe_open_position` (shadow open path)
  * `app.trading.execution.dispatcher.dispatch` (live/telegram path)

Settings flags default OFF — gates are no-ops on a fresh deploy.
"""
