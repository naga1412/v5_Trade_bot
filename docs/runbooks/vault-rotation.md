# Vault rotation runbook

**Last updated**: 2026-05-27 (PR-FIX-PR277-SELF-BOOTSTRAP-VAULT)

## Why this exists

The Binance API keys live in `/app/secrets.enc` (Argon2-derived key, AES-GCM ciphertext — see `backend/app/secrets/vault.py`). The encryption passphrase is held only in the `MASTER_PASSPHRASE` env var on the Hetzner host's `/opt/trading-radar/.env`. Rotate the passphrase whenever:

- the passphrase value has been exposed (chat transcript leak, screenshot, accidental log line);
- a host operator with shell access leaves the team;
- the Binance API keys themselves are rotated and you want a clean cipher state;
- the periodic-rotation calendar fires (recommended every 90 days minimum).

The bot can run for years without rotating Binance keys, but the **encryption passphrase** is a credential too — treat it the same way.

## Pre-flight

Confirm the bot is in a state safe to interrupt for ~2 minutes:

```bash
# On Hetzner:
cd /opt/trading-radar

# Check open live_trades — wait for or close before rotating
docker compose exec -T postgres psql -U postgres -d trading_radar -c \
  "SELECT id, symbol, direction, status FROM live_trades
    WHERE status IN ('pending', 'open');"

# If any are 'pending' for > 60s: the reconciler will resolve them.
# If any are 'open': you can rotate — the position stays on Binance
# regardless of vault state; only NEW orders need the keys.
```

## Procedure

### 1. Generate a new passphrase

Use a strong generator on a trusted machine (NOT one whose history might leak):

```bash
# Local machine, NOT shared via chat / screenshots:
python -c "import secrets, string; print(''.join(secrets.choice(string.ascii_letters + string.digits + '_-') for _ in range(48)))"
```

Save it to your password manager. **Do not paste it into the chat with the strategy agent.**

### 2. Decrypt the current secrets.enc with the OLD passphrase

```bash
# On Hetzner:
cd /opt/trading-radar

# Stage the current vault on the host (decrypt only — secrets stay in memory)
docker compose exec -T backend python - <<'PY'
import os
from pathlib import Path
from app.secrets.vault import decrypt_secrets

path = Path(os.environ.get("VAULT_SECRETS_PATH", "/app/secrets.enc"))
secrets = decrypt_secrets(path.read_bytes(), passphrase=os.environ["MASTER_PASSPHRASE"])
print("OK — decrypted keys:", sorted(secrets.keys()))
PY
# Expected: OK — decrypted keys: ['binance_api_key', 'binance_api_secret']
```

If this fails the OLD passphrase is already wrong — abort and reach the operator.

### 3. Re-encrypt with the NEW passphrase

```bash
# Hetzner host. Replace <NEW_PASSPHRASE> with the value from step 1.
docker compose exec -T -e NEW_PP="<NEW_PASSPHRASE>" backend python - <<'PY'
import os
from pathlib import Path
from app.secrets.vault import decrypt_secrets, encrypt_secrets

path = Path(os.environ.get("VAULT_SECRETS_PATH", "/app/secrets.enc"))
old_pp = os.environ["MASTER_PASSPHRASE"]
new_pp = os.environ["NEW_PP"]
secrets = decrypt_secrets(path.read_bytes(), passphrase=old_pp)
new_blob = encrypt_secrets(secrets, passphrase=new_pp)
# Write to a sibling file first; atomic rename on success.
tmp = path.with_suffix(".enc.new")
tmp.write_bytes(new_blob)
print("OK — new ciphertext at", tmp)
PY
```

### 4. Atomic swap + update .env

```bash
# On Hetzner:
cd /opt/trading-radar

# Find the running container's volume-mount path for /app/secrets.enc.
# The default docker-compose.yml mounts ./secrets.enc → /app/secrets.enc
# but verify:
docker compose config | grep -A 2 'secrets.enc'

# Once you've confirmed the host path (e.g. /opt/trading-radar/secrets.enc):
mv /opt/trading-radar/secrets.enc /opt/trading-radar/secrets.enc.bak
mv /opt/trading-radar/secrets.enc.new /opt/trading-radar/secrets.enc

# Update MASTER_PASSPHRASE in the HOST .env (NOT inside the container —
# see the .env edit-discipline note in CLAUDE.md). Use nano/vim:
nano /opt/trading-radar/.env
# Replace MASTER_PASSPHRASE=<old> with MASTER_PASSPHRASE=<new>. Save + exit.
```

### 5. Restart + verify

```bash
docker compose restart backend
docker compose logs backend --since 2m 2>&1 | grep -iE "vault cache initialised|initialize_vault_cache|preflight"
# Expected: "vault cache initialised (2 keys)"
# If you see "decrypt failed" → the new passphrase in .env doesn't
# match the new ciphertext. Restore from .bak:
#   mv /opt/trading-radar/secrets.enc.bak /opt/trading-radar/secrets.enc
#   (then revert MASTER_PASSPHRASE in .env back to old, restart)

# Verify end-to-end via the round-trip test (once HYBRID is at the
# frozen safety value, e.g. 0.99):
docker compose exec -T backend python -m tools.round_trip_test_trade
```

### 6. Destroy the backup

After the round-trip test passes:

```bash
# DELETE the .bak file — it still decrypts with the OLD passphrase
# (which is now considered burned).
shred -u /opt/trading-radar/secrets.enc.bak
# Or, on filesystems without shred:
rm -P /opt/trading-radar/secrets.enc.bak  # POSIX
```

## Rollback (if anything goes wrong)

Within step 4-5 (before destroying the backup):

```bash
cd /opt/trading-radar
# Restore old ciphertext
mv secrets.enc secrets.enc.failed
mv secrets.enc.bak secrets.enc
# Restore old passphrase in .env (use the value from your password manager)
nano .env
# Restart
docker compose restart backend
docker compose logs backend --since 2m 2>&1 | grep "vault cache initialised"
```

Past step 6 the .bak is gone; rollback requires having saved the old passphrase + old ciphertext elsewhere.

## What this rotation does NOT cover

- **Rotating the Binance API keys themselves.** That's a separate Binance-side procedure (revoke old keys, generate new, encrypt+re-pack via the same script). Out of scope here.
- **Re-keying past audit-chain hashes.** The hash chain is keyed on row content + the previous row hash; the Binance keys are not part of the hash. Rotation is invisible to the audit chain.

## Recovery if the passphrase is lost

There is **no recovery** without the passphrase. The ciphertext is AES-GCM with an Argon2-derived key — by design. If the passphrase is permanently lost:

1. Revoke the Binance API keys via the Binance web UI (since the bot can no longer use them, you don't want them sitting active).
2. Generate new Binance keys.
3. Run the original vault-encrypt tool (see `backend/app/secrets/vault.py` + the original setup runbook) to create a fresh `secrets.enc` with a new passphrase.
4. Update `MASTER_PASSPHRASE` in `.env`.
5. Restart + verify per step 5 above.
