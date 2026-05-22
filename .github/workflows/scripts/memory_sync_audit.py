"""PR-WEEK1-MEGA-BATCH Part A — memory sync audit script.

Read-only introspection inside the backend container. Prints sectioned
output for the workflow runner to capture + ship back to controller.
"""
import sys
sys.path.insert(0, "/app")

import os

import httpx

from app.config import Settings, get_settings


def _section(name: str) -> None:
    print(f"\n=== {name} ===")


def _try(fn, label):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        print(f"{label} FAILED: {type(e).__name__}: {e}")


# A1 — runtime settings + outbound IP
_section("A1.runtime: target flag values")
def a1_runtime():
    s = get_settings()
    fields = [
        "DISABLE_SHORT_SIGNALS",
        "LIVE_COOLDOWN_ENABLED",
        "HOLD_TP_SCALING_ENABLED",
        "autonomous_trading_enabled",
        "binance_use_testnet",
        "MIN_ENTRY_SCORE_LONG",
        "SYMBOL_ALLOWLIST_ENABLED",
        "DYNAMIC_SIZING_ENABLED",
    ]
    for f in fields:
        try:
            print(f"{f}={getattr(s, f)}")
        except AttributeError as e:
            print(f"{f}=<missing: {e}>")
_try(a1_runtime, "A1.runtime")


_section("A1.outbound_ip")
def a1_ip():
    try:
        r = httpx.get("https://ifconfig.me", timeout=5)
        print(f"outbound_ip={r.text.strip()}")
    except Exception as e:  # noqa: BLE001
        print(f"outbound_ip=<error: {type(e).__name__}: {e}>")
_try(a1_ip, "A1.ip")


# A4 — full Settings field dump (just sorted names)
_section("A4: Settings.model_fields (sorted)")
def a4_fields():
    fields = sorted(Settings.model_fields.keys())
    print(f"count={len(fields)}")
    for f in fields:
        print(f"  {f}")
_try(a4_fields, "A4")


# A8 — brain infra verification
_section("A8.brain_infra: module imports")
def a8_modules():
    modules = [
        "app.rl.adapter", "app.rl.audit", "app.rl.checkpoints",
        "app.rl.inference", "app.rl.obs", "app.rl.policy",
        "app.rl.ppo", "app.rl.predictor_glue", "app.rl.replay_buffer",
        "app.rl.reward", "app.rl.safety",
    ]
    import importlib
    for m in modules:
        try:
            importlib.import_module(m)
            print(f"  {m}: IMPORTED OK")
        except Exception as e:  # noqa: BLE001
            print(f"  {m}: FAILED — {type(e).__name__}: {e}")
_try(a8_modules, "A8.modules")


_section("A8.brain_infra: get_active_policy_and_checkpoint() state")
def a8_state():
    from app.rl.checkpoints import get_active_policy_and_checkpoint
    state = get_active_policy_and_checkpoint()
    print(f"active_policy_and_checkpoint = {state}")
    print(f"interpreted = {'has_active_checkpoint' if state is not None else 'no_active_checkpoint (no-op fast path)'}")
_try(a8_state, "A8.state")
