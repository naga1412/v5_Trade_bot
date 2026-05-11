"""Fast multi-asset scanner — deterministic indicator-only scoring.

Distinct from the ML-driven live predictor in :mod:`app.ws.live_prediction`.
The scanner is built for breadth (200+ symbols in seconds) at the cost
of depth (no neural network, no per-bar audit chain). It's the right
tool for "survey the whole market", not "deep view of one asset".
"""
