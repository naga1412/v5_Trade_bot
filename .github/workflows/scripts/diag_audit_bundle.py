"""PR-DIAG-AUDIT-BUNDLE — three-part audit diagnostic script.

Executed inside the backend container by the ops-debug `diag-audit-bundle`
probe. Read-only. Prints sectioned output to stdout for capture by the
workflow log.

When invoked as `python /tmp/diag.py`, Python prepends `/tmp` (not /app)
to sys.path — so `import app.config` would fail. Container WORKDIR is
/app, but we run the script from /tmp. Fix: prepend /app explicitly.
"""
import sys
sys.path.insert(0, "/app")

import inspect
import os

import httpx

from app.api.routes.ws import manager
from app.config import Settings, get_settings
from app.ops.worker_registry import WORKER_REGISTRY
from app.ws.manager import _key_matches
from app.ws.shadow_updates import get_last_pnl_tick_at


def _section(name: str) -> None:
    print(f"\n=== {name} ===")


def _try(fn, label):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        print(f"{label} FAILED: {type(e).__name__}: {e}")


# -------------------- SECTION A: UUSDT regression -----------------------

_section("A2: UUSDT klines (Binance /api/v3/klines)")
def a2():
    r = httpx.get(
        "https://api.binance.com/api/v3/klines",
        params={"symbol": "UUSDT", "interval": "1h", "limit": 3},
        timeout=10,
    )
    print(f"klines status={r.status_code}")
    body = r.json() if r.status_code == 200 else r.text
    print(f"klines body sample: {str(body)[:200]}")
_try(a2, "A2")

_section("A2b: UUSDT /api/v3/ticker/price (fetch_spot_prices path)")
def a2b():
    r = httpx.get(
        "https://api.binance.com/api/v3/ticker/price",
        params={"symbol": "UUSDT"}, timeout=10,
    )
    print(f"ticker status={r.status_code}")
    print(f"ticker body: {r.text[:200]}")
_try(a2b, "A2b")

_section("A2c: TRUMPUSDT /api/v3/ticker/price (baseline)")
def a2c():
    r = httpx.get(
        "https://api.binance.com/api/v3/ticker/price",
        params={"symbol": "TRUMPUSDT"}, timeout=10,
    )
    print(f"ticker status={r.status_code}")
    print(f"ticker body: {r.text[:200]}")
_try(a2c, "A2c")

_section("A3: WS ConnectionManager subscription state")
def a3():
    subs = manager._subs  # noqa: SLF001 — diagnostic introspection
    print(f"total subs={len(subs)}")
    per_channel: dict[str, int] = {}
    for sub, _ in subs.values():
        per_channel[sub.channel] = per_channel.get(sub.channel, 0) + 1
    print(f"subs per channel: {per_channel}")
    uusdt_subs = [
        (s.channel, s.params)
        for s, _ in subs.values()
        if "UUSDT" in str(s.params)
    ]
    print(f"UUSDT-targeted subs: {uusdt_subs}")
_try(a3, "A3")

_section("A4c: get_last_pnl_tick_at() snapshot")
def a4c():
    snap = get_last_pnl_tick_at()
    print(f"total symbols emitting: {len(snap)}")
    uusdt = snap.get("UUSDT")
    trump = snap.get("TRUMPUSDT")
    print(f"UUSDT last_pnl_tick: {uusdt if uusdt else '(never)'}")
    print(f"TRUMPUSDT last_pnl_tick: {trump if trump else '(never)'}")
    sample = sorted(snap.keys())[:20]
    print(f"sample of emitters (first 20): {sample}")
_try(a4c, "A4c")

_section("A5: _key_matches deployed source (PR10.6 fix verification)")
def a5():
    print(inspect.getsource(_key_matches))
_try(a5, "A5")

_section("A6: SHADOW_SPOT_BLACKLIST runtime value")
def a6():
    s = get_settings()
    print(f"SHADOW_SPOT_BLACKLIST={s.SHADOW_SPOT_BLACKLIST}")
    print(f"UUSDT in blacklist: {'UUSDT' in s.SHADOW_SPOT_BLACKLIST}")
    print(f"TRUMPUSDT in blacklist: {'TRUMPUSDT' in s.SHADOW_SPOT_BLACKLIST}")
_try(a6, "A6")

# -------------------- SECTION B: dead-code audit ------------------------

print("\n################ SECTION B ################")

_section("B1: Settings runtime dump (operator-tunable fields)")
def b1():
    s = get_settings()
    op_tunable_lowercase = {
        "autonomous_trading_enabled",
        "auto_promote_to_telegram_enabled",
        "auto_promote_to_fullyauto_enabled",
        "auto_promote_consecutive_days",
        "worker_enabled",
        "binance_use_testnet",
        "env",
        "log_level",
    }
    rows = []
    for fname in s.model_fields.keys():
        if fname.startswith("_"):
            continue
        if fname.isupper() or fname in op_tunable_lowercase:
            try:
                val = getattr(s, fname)
            except Exception as e:  # noqa: BLE001
                val = f"<read failed: {e}>"
            # Truncate long values
            sval = str(val)
            if len(sval) > 100:
                sval = sval[:97] + "..."
            rows.append((fname, sval))
    for name, val in sorted(rows):
        print(f"{name}={val}")
_try(b1, "B1")

_section("B4b: worker_registry expected list")
def b4b():
    for w in WORKER_REGISTRY:
        gates = ",".join(w.required_env) if w.required_env else "-"
        print(
            f"{w.name:36s} stateful={str(w.stateful):5s} "
            f"single_shot={str(w.single_shot):5s} "
            f"pending_hb={str(w.pending_heartbeat):5s} "
            f"gates={gates}"
        )
_try(b4b, "B4b")

_section("B6: env vs Settings drift")
def b6():
    declared_upper = {f.upper() for f in Settings.model_fields.keys()}
    declared_native = set(Settings.model_fields.keys())
    sys_vars = {
        "PATH", "HOME", "PWD", "SHLVL", "TERM", "LANG", "HOSTNAME",
        "PYTHONPATH", "LD_LIBRARY_PATH", "HOSTTYPE", "LC_CTYPE", "LC_ALL",
        "LANGUAGE", "GPG_KEY", "PYTHON_VERSION", "PYTHON_SHA256",
        "PYTHON_PIP_VERSION", "PYTHON_GET_PIP_URL",
        "PYTHON_GET_PIP_SHA256", "PYTHON_SETUPTOOLS_VERSION", "LESSCLOSE",
        "LESSOPEN", "OLDPWD", "DEBIAN_FRONTEND", "MAIL", "LOGNAME", "USER",
        "TERM_PROGRAM", "SSH_CLIENT", "SSH_CONNECTION", "SSH_TTY",
        # Postgres/container internals
        "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB",
    }
    env_upper = {
        k for k in os.environ
        if k.isupper() and not k.startswith("_") and k not in sys_vars
    }
    ghosts = sorted(env_upper - declared_upper - {f.upper() for f in declared_native})
    print("IN_ENV_NOT_IN_SETTINGS (potential ghost flags — not bound to runtime):")
    for x in ghosts:
        val = os.environ.get(x, "")
        if len(val) > 60:
            val = val[:57] + "..."
        print(f"  {x}={val}")
    print("")
    not_overridden = sorted(declared_upper - env_upper)
    print(f"IN_SETTINGS_NOT_OVERRIDDEN_BY_ENV (count={len(not_overridden)}, default holds)")
    for x in not_overridden[:30]:
        print(f"  {x}")
    if len(not_overridden) > 30:
        print(f"  ... + {len(not_overridden) - 30} more")
_try(b6, "B6")
