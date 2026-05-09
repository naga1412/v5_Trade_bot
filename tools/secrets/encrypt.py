"""Operator-side helper to write secrets.enc.

Run this on your laptop, NOT on the server. The plaintext keys never
touch a remote disk:

    py -3.11 tools/secrets/encrypt.py --out secrets.enc

The script prompts for each key + a passphrase. Output is a binary
blob that is safe to commit to the repo. The passphrase is the only
thing that decrypts it — store in your password manager AND your head.

Default keys (skip with empty input — those entries are omitted from
the vault):

    binance_api_key
    binance_api_secret
    telegram_bot_token
    telegram_chat_id

You can add more with ``--key NAME`` (repeatable).
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

# Path setup so this script can be run from the repo root without installing
# the backend package.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.secrets.vault import (  # noqa: E402
    WeakPassphraseError, encrypt_secrets,
)


_DEFAULT_KEYS = (
    "binance_api_key",
    "binance_api_secret",
    "telegram_bot_token",
    "telegram_chat_id",
)


def _prompt_secret(key: str) -> str | None:
    """Read a single secret value. Empty input → skip that key."""
    val = getpass.getpass(f"  {key} (empty to skip): ")
    return val if val else None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tools.secrets.encrypt")
    p.add_argument(
        "--out", default="secrets.enc",
        help="output path for the encrypted blob (default: ./secrets.enc)",
    )
    p.add_argument(
        "--key", action="append", default=None,
        help="additional secret key name to prompt for (repeatable)",
    )
    args = p.parse_args(argv)

    keys = list(_DEFAULT_KEYS)
    if args.key:
        for k in args.key:
            if k not in keys:
                keys.append(k)

    print(f"Writing {args.out} with up to {len(keys)} secrets.")
    print("Empty input skips that key. Plaintext is read via getpass — "
          "your terminal will not echo.")
    print()

    secrets: dict[str, str] = {}
    for k in keys:
        v = _prompt_secret(k)
        if v is not None:
            secrets[k] = v

    if not secrets:
        print("No secrets entered; aborting.")
        return 1

    print()
    print("Now choose a passphrase. Min 16 chars; mix case + special chars.")
    print("This is the ONLY way to decrypt the vault — store securely.")
    while True:
        p1 = getpass.getpass("Passphrase: ")
        p2 = getpass.getpass("Passphrase (again): ")
        if p1 != p2:
            print("✗ passphrases differ; try again.")
            continue
        try:
            blob = encrypt_secrets(secrets, passphrase=p1)
        except WeakPassphraseError as e:
            print(f"✗ {e}")
            continue
        break

    out = Path(args.out)
    out.write_bytes(blob)
    print()
    print(f"✓ wrote {out} ({len(blob)} bytes, {len(secrets)} keys encrypted)")
    print()
    print("Next steps:")
    print(f"  git add {out} && git commit -m 'rotate vault'  # safe to commit")
    print("  Then on the server:")
    print("    set MASTER_PASSPHRASE in /opt/trading-radar/.env")
    print("    docker compose restart backend")
    return 0


if __name__ == "__main__":
    sys.exit(main())
