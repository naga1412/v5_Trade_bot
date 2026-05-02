# SP-0 Shipping Runbook — Step-by-Step Playbook

This is a complete, ordered guide for everything **you (the human)** must do
to ship SP-0 to production after Claude Code finished writing the code.

**Total wall-clock time:** ~3-5 hours of your active work, spread across
1-14 days (Oracle free-tier Ampere availability is the main wait).

**Total cost:** $0 — $9 (only cost is an optional domain at ~$9/year from
Cloudflare Registrar; everything else is genuinely free tier).

---

## Phase 0 — One-time setup (do once before you begin)

### 0.1 Sign up for accounts you'll need

You will need free accounts at all four of these. Sign up first; configuration
comes later in the relevant phase.

| Service | Sign-up URL | Why |
|---|---|---|
| GitHub | https://github.com/signup | Code remote + Actions CI/CD |
| Cloudflare | https://dash.cloudflare.com/sign-up | Tunnel + Access (auth) + free DNS |
| Backblaze B2 | https://www.backblaze.com/sign-up/cloud-storage | Off-site backups (10 GB free) |
| Oracle Cloud | https://signup.cloud.oracle.com | Always Free 4-vCPU/24 GB ARM VM |
| TradingView | https://www.tradingview.com/ | Indicator cross-check (Phase 2) |

**Notes:**
- Oracle requires a credit card for verification. You will not be charged for the
  Always Free tier — but Oracle does not let you sign up without a card.
- Backblaze requires phone verification.

### 0.2 Install local tools

Open PowerShell as Administrator once and run:

```powershell
# GitHub CLI (for easy push + PR creation)
winget install --id GitHub.cli -e

# (You already have: git, docker, python 3.11, node 20)
```

After install, **restart your PowerShell window** so PATH picks up `gh`.
Verify:
```powershell
gh --version    # should print: gh version 2.x.x
```

### 0.3 Confirm your local stack is still running

```powershell
docker ps --format "table {{.Names}}\t{{.Status}}"
```

You should see `tr-postgres`, `tr-redis`, `tr-backend`, `tr-frontend` all `Up (healthy)`.

If any are down: `cd a:\v5_Trade_bot\worktrees\sp-0; docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d`

---

## Phase 1 — GitHub remote + push (10 minutes, no waiting)

### 1.1 Create a private GitHub repo and push

```powershell
cd a:\v5_Trade_bot
gh auth login
# Choose: GitHub.com → HTTPS → Login with a web browser → follow the URL it prints
```

After auth completes:

```powershell
gh repo create v5_Trade_bot --private --source=. --remote=origin --description "Trading-radar — zero-cost AI trading research platform"
```

Push everything:

```powershell
git -c safe.directory='A:/v5_Trade_bot' push -u origin main
git -c safe.directory='A:/v5_Trade_bot' push -u origin sp-0/main
git -c safe.directory='A:/v5_Trade_bot' push origin sp-0
```

You should see all 67 commits + the `sp-0` tag uploaded.

### 1.2 Verify on GitHub web

Open `https://github.com/<your-username>/v5_Trade_bot` — you should see:
- 2 branches: `main` (default) and `sp-0/main`
- 1 tag: `sp-0`
- Files: docs/, files/, frontend/, backend/, infra/, etc.

### 1.3 Confirm CI workflow is detected

In the repo, click **Actions** tab. The first push should trigger the `ci`
workflow (backend + frontend tests). It may fail because `aiosqlite` and
`respx` aren't yet on the CI install line — if it fails, that's OK for now,
we'll fix in Phase 7.5.

---

## Phase 2 — O2: TradingView indicator cross-check (30 minutes, no waiting)

This proves our indicator math matches TradingView within 0.1%.

### 2.1 Open the CSV

The file is at: `a:\v5_Trade_bot\worktrees\sp-0\tv_check_btc_1h.csv`

Open in Excel or LibreOffice Calc. Columns:
- `ts`, `close`, `our_rsi14`, `our_ema20`, `our_ema50`, `our_ema200`,
- `our_macd_line`, `our_macd_signal`, `tv_value (FILL MANUALLY)`, `pct_diff`

### 2.2 Open TradingView side-by-side

1. Go to https://www.tradingview.com
2. Sign in (free account)
3. Top search → type `BINANCE:BTCUSDT` → press Enter → chart loads
4. Above the chart, set timeframe to **1h**
5. Click **Indicators** (fx button) → add these 4:
   - **RSI** — defaults (length 14)
   - **EMA** — length 20
   - **EMA** — length 50
   - **EMA** — length 200
   - **MACD** — defaults (12, 26, 9)

### 2.3 Pick 10 random rows from the CSV

From rows 200-201 (latest), scroll up to row ~100 (where EMA200 is populated).
Pick any 10 rows. For each:

1. **In TradingView:** scroll left to the timestamp matching the CSV row's `ts`.
   - Tip: TV uses your local timezone by default; the CSV uses UTC. To match,
     either right-click the time scale in TV → Settings → Timezone → "UTC", or
     mentally adjust by your offset.
   - Hover over the candle → TV shows the OHLC values. Match by `close` price.
   - **Important:** use the **second-to-last** bar in the TV view at any moment,
     not the last (live, partial) bar.

2. **For each indicator:** read the indicator's value at that bar from the
   indicator pane below the chart, or hover over the indicator line.

3. **Fill the spreadsheet:**
   - Add a column right of `tv_value`. Fill it with TV's value for whichever
     indicator you're checking.
   - Fill `pct_diff` with formula: `=ABS(C2-K2)/K2*100`  (where C is `our_rsi14`
     and K is the TV value). Adapt the column letters.

4. **Pass criterion:** all 10 spot-checks have `pct_diff ≤ 0.1`.

### 2.4 Record the result

If all pass, append to `docs/superpowers/log.md` (create the file if needed):

```
2026-MM-DD SP-0 indicator cross-check: PASS — 10/10 within 0.1% tolerance on BTCUSDT 1h
```

If any fail by >0.1%: stop and tell Claude — there's a bug in the indicator
implementation that needs to be debugged before SP-0 is truly green.

---

## Phase 3 — Cloudflare account + domain (30 minutes)

### 3.1 If you don't have a domain — buy one

Cheapest: **Cloudflare Registrar** sells `.com` for ~$9/year (no markup).

1. Cloudflare dashboard → Registrar → Register Domains
2. Search for an available name (e.g. `mytrading-radar.com`)
3. Buy. Pay with card.

If you already have a domain elsewhere, transfer the **DNS** (not the registrar)
to Cloudflare:
1. Cloudflare → Add a site → enter your domain → Free plan
2. CF gives you 2 nameservers; go to your registrar (GoDaddy / Namecheap / etc.)
   and replace the nameservers with the 2 CF gave you
3. Wait 1-24 hours for DNS propagation

### 3.2 Get to the Zero Trust dashboard

Once domain is on Cloudflare:
1. Open https://one.dash.cloudflare.com
2. First time: it walks you through creating a "Team Domain" — pick something
   like `<yourname>` (it becomes `<yourname>.cloudflareaccess.com`)
3. Save the team domain — you'll need it for `CF_ACCESS_TEAM_DOMAIN`

### 3.3 Create a Tunnel (placeholder for now — actual config in Phase 7.4)

Don't fully configure this yet. Just verify you can reach:
- Zero Trust → Networks → Tunnels (button: "Create a tunnel" — leave for Phase 7)
- Zero Trust → Access → Applications (button: "Add an application" — leave for Phase 7)
- Zero Trust → Settings → Authentication (configure Google as IdP — do this NOW
  so it's ready in Phase 7)

#### 3.3.1 Add Google as identity provider

Zero Trust → Settings → Authentication → Login methods → Add new
→ pick **Google** → Cloudflare gives instructions:

1. Go to https://console.cloud.google.com (sign in with the Google account
   you want to use to log in)
2. Create a project (any name)
3. APIs & Services → OAuth consent screen → External → fill required fields
4. APIs & Services → Credentials → Create Credentials → OAuth client ID → Web
   application
5. Authorized redirect URIs: paste the URL Cloudflare gave you (looks like
   `https://<yourname>.cloudflareaccess.com/cdn-cgi/access/callback`)
6. Save → copy Client ID + Client Secret → paste into Cloudflare Zero Trust
7. Test: Zero Trust → Settings → Authentication → Test (should pop up Google
   sign-in and succeed)

You'll use this Google account to log in to your trading-radar later.

---

## Phase 4 — Backblaze B2 account + bucket (10 minutes)

### 4.1 Create a private bucket

1. Sign in at https://secure.backblaze.com
2. B2 Cloud Storage → Buckets → Create a Bucket
3. Bucket Name: `trading-radar-backups` (must be globally unique — if taken,
   add a suffix like `-yourname`)
4. Files in Bucket are: **Private**
5. Default Encryption: enable
6. Object Lock: leave off
7. Create Bucket

### 4.2 Get an Application Key

1. App Keys → Add a New Application Key
2. Name: `trading-radar-backup-write`
3. Allow access to Bucket(s): only `trading-radar-backups`
4. Type of Access: **Read and Write**
5. Create New Key
6. **IMPORTANT:** Backblaze shows you the `keyID` and `applicationKey` ONCE.
   Copy both into a temporary text file or password manager. You can't view
   the secret again.

You'll plug `keyID` into `B2_ACCOUNT_ID` and `applicationKey` into
`B2_APPLICATION_KEY` in Phase 7.

---

## Phase 5 — Oracle Cloud Always Free + try to provision Ampere (15 min signup; 1-14 days waiting)

This is the **long pole**. Oracle's free Ampere instances are often
"out of capacity" — you may need to retry over many days.

### 5.1 Sign up

1. https://signup.cloud.oracle.com
2. Pick your **home region** carefully — this is permanent. For India users:
   **Mumbai** or **Hyderabad**. For others: closest to you.
3. Enter credit card. They'll do a small reservation hold (refunded).
4. Wait for "Account active" email.

### 5.2 Configure OCI CLI on your laptop

```powershell
# Install OCI CLI (one-time)
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
iex ((New-Object System.Net.WebClient).DownloadString('https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.ps1'))
```

After install, restart PowerShell and run:
```powershell
oci setup config
```

It walks you through:
- User OCID (find at OCI console → top-right profile → User Settings → OCID)
- Tenancy OCID (top-right profile → Tenancy)
- Region (e.g. `ap-mumbai-1`)
- Generate API key pair: yes
- Upload the public key: copy the path it prints, then in OCI console →
  User Settings → API Keys → Add API Key → paste the key

Test:
```powershell
oci iam region list
```
Should print a list of regions.

### 5.3 Try to provision an Ampere A1 instance

Use the open-source polling script. From PowerShell:

```powershell
cd a:\
git clone https://github.com/hitrov/oci-arm-host-capacity.git
cd oci-arm-host-capacity
npm install
```

Edit `config.yml` with these values (see the repo's README for full schema):

```yaml
shape: VM.Standard.A1.Flex
ocpus: 4
memory_in_gbs: 24
instance_name: trading-radar
boot_volume_size_in_gbs: 100
operating_system: "Canonical Ubuntu"
os_version: "22.04"
ssh_authorized_keys: "ssh-rsa AAAA... your-pub-key"
# Plus tenancy_id, user_id, fingerprint, key_file, region from `oci setup`
```

Generate an SSH key for the VM (one-time):
```powershell
ssh-keygen -t ed25519 -C "oracle-trading-radar" -f $HOME\.ssh\oracle_key
type $HOME\.ssh\oracle_key.pub    # paste this into ssh_authorized_keys above
```

Run the poller:
```powershell
node index.js
```

Leave it running. It will retry every 60 s, in multiple availability domains,
until capacity opens. Expect anywhere from 1 hour to 14 days. **Do not stop the
script.** When it succeeds, it will print the new instance's public IP.

### 5.4 First SSH in

```powershell
ssh -i $HOME\.ssh\oracle_key ubuntu@<public-ip>
```

If accepted (you should see the Ubuntu welcome banner), you're in.

### 5.5 Restrict SSH to your IP only

In OCI console → Networking → Virtual Cloud Networks → your VCN → Subnets →
Default Security List → Ingress Rules:
- Find the rule for port 22
- Change Source CIDR from `0.0.0.0/0` to `<your-public-IP>/32` (find your IP at
  https://whatismyip.com)
- Save

This locks SSH to only your laptop's current IP. If your home IP changes
later you'll need to update this rule.

---

## Phase 6 — Configure GitHub Actions secrets (5 minutes; do anytime after Phase 1 + Phase 5)

Once Oracle is provisioned and you have the public IP:

1. Open https://github.com/<you>/v5_Trade_bot/settings/secrets/actions
2. New repository secret → `ORACLE_HOST` → value: the Oracle public IP
3. New repository secret → `ORACLE_SSH_KEY` → value: paste the **private** key
   from `$HOME\.ssh\oracle_key` (the file WITHOUT `.pub` — the long
   `-----BEGIN OPENSSH PRIVATE KEY-----...` block)

The auto-deploy workflow can now SSH into Oracle on every push to main.

---

## Phase 7 — Deploy the stack to Oracle (1-2 hours, after Phase 5 succeeds)

The full runbook is in `infra/oracle/provision-runbook.md`. Below is the
condensed version. Run all commands inside the Oracle SSH session.

### 7.1 OS hardening

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y ufw fail2ban
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw enable
sudo systemctl enable --now fail2ban
```

### 7.2 Install Docker on Oracle

```bash
sudo apt install -y apt-transport-https ca-certificates curl gnupg lsb-release git rsync postgresql-client
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
```

Log out and back in for the group change to apply:
```bash
exit
ssh -i $HOME\.ssh\oracle_key ubuntu@<public-ip>
docker run --rm hello-world    # should print "Hello from Docker!"
```

### 7.3 Generate a deploy key + add to GitHub

```bash
# On Oracle:
ssh-keygen -t ed25519 -C "oracle-trading-radar" -f ~/.ssh/github_deploy
cat ~/.ssh/github_deploy.pub    # copy this output
```

In GitHub repo → Settings → Deploy keys → Add deploy key:
- Title: `oracle-deploy`
- Key: paste the public key from above
- Allow write access: leave UNCHECKED (read-only is enough for clone+pull)

Test:
```bash
GIT_SSH_COMMAND="ssh -i ~/.ssh/github_deploy" git clone git@github.com:<you>/v5_Trade_bot.git ~/trading-radar
cd ~/trading-radar
```

If the clone succeeds, you're set up.

### 7.4 Configure environment

```bash
cd ~/trading-radar
cp .env.example .env
nano .env
```

Fill in:
- `POSTGRES_PASSWORD=` → generate a random strong password (e.g.
  `openssl rand -base64 24`)
- `DATABASE_URL=` → update password to match
- `SECRET_KEY=` → `python3 -c "import secrets; print(secrets.token_urlsafe(64))"`
- `CF_ACCESS_TEAM_DOMAIN=` → your team domain from Phase 3.2 (e.g.
  `myname.cloudflareaccess.com`)
- `CF_ACCESS_AUD=` → leave empty for now; Phase 7.6 fills it
- `B2_ACCOUNT_ID=` → keyID from Phase 4.2
- `B2_APPLICATION_KEY=` → applicationKey from Phase 4.2
- `B2_BUCKET=` → `trading-radar-backups`
- Keep `ENV=production`

Lock it down:
```bash
chmod 600 .env
```

### 7.5 Bring the stack up

```bash
docker compose up -d --build
docker compose ps
# All 4 should be "healthy" or "running"
docker compose exec -T backend bash -c "cd /app && alembic upgrade head"
# Should print: Running upgrade -> 0001_initial -> 0002_audit_chain
curl http://localhost:8000/api/v1/health
# Should return: {"status":"ok","service":"trading-radar","version":"0.1.0-sp-0"}
```

### 7.6 Set up Cloudflare Tunnel + Access

#### 7.6.1 Install cloudflared

```bash
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64
sudo mv cloudflared-linux-arm64 /usr/local/bin/cloudflared
sudo chmod +x /usr/local/bin/cloudflared
```

#### 7.6.2 Create the tunnel in Cloudflare

In Zero Trust dashboard → Networks → Tunnels → Create a tunnel:
- Connector: **Cloudflared**
- Tunnel name: `trading-radar`
- Save → CF gives you an `install` command for Linux. Run it on Oracle:

```bash
sudo cloudflared service install <YOUR_TUNNEL_TOKEN>
sudo systemctl enable --now cloudflared
sudo systemctl status cloudflared    # should be active (running)
```

#### 7.6.3 Add public hostname routes

Back in CF tunnel config (web UI):
- Public Hostname tab → Add a public hostname:
  - Subdomain: `trading-radar`
  - Domain: your domain
  - Service: `HTTP localhost:5173` (frontend)
  - Save

This handles the frontend. For backend `/api/*` and `/ws/*`, edit the tunnel
config file:
```bash
sudo nano /etc/cloudflared/config.yml
```

Replace contents with (substituting your tunnel UUID):
```yaml
tunnel: <YOUR_TUNNEL_UUID>
credentials-file: /etc/cloudflared/<YOUR_TUNNEL_UUID>.json

ingress:
  - hostname: trading-radar.<yourdomain>
    path: /api/.*
    service: http://localhost:8000
  - hostname: trading-radar.<yourdomain>
    path: /ws/.*
    service: http://localhost:8000
  - hostname: trading-radar.<yourdomain>
    service: http://localhost:5173
  - service: http_status:404
```

Restart cloudflared:
```bash
sudo systemctl restart cloudflared
```

#### 7.6.4 Create the Access application

Zero Trust → Access → Applications → Add an application → Self-hosted:
- Name: `trading-radar`
- Application domain: `trading-radar.<yourdomain>`
- Identity providers: Google (configured in Phase 3.3.1)
- Save → click into the app → copy the **Application Audience (AUD) Tag**

Back on Oracle, update `.env`:
```bash
nano ~/trading-radar/.env
# Set CF_ACCESS_AUD=<the-aud-tag-you-just-copied>
docker compose restart backend
```

#### 7.6.5 Add an Access policy (only-you)

Same Access app → Policies → Add a policy:
- Name: `only-me`
- Action: Allow
- Include rule: Emails → `<your-google-email>`
- Save

### 7.7 First end-to-end test

In your laptop browser, visit `https://trading-radar.<yourdomain>`:
- Should redirect to Google SSO
- Sign in with your Google account
- After auth, the trading-radar UI loads
- Chart updates with BTC/USDT 1h candles
- 4 panels populate within 5 seconds

If you see a Cloudflare 502 or 521 error: check `sudo systemctl status cloudflared`
on Oracle and `docker compose ps`.

---

## Phase 8 — Set up backups on Oracle (30 minutes)

### 8.1 Install backup scripts as cron jobs

```bash
cd ~/trading-radar
sudo cp infra/backup/pg_dump_hourly.sh /usr/local/bin/tr_pg_dump_hourly.sh
sudo cp infra/backup/pg_basebackup_nightly.sh /usr/local/bin/tr_pg_basebackup_nightly.sh
sudo cp infra/backup/b2_upload.sh /usr/local/bin/tr_b2_upload.sh
sudo chmod +x /usr/local/bin/tr_*.sh
sudo mkdir -p /var/log/trading-radar /var/backups/trading-radar
```

Install cron entries:
```bash
( crontab -l 2>/dev/null; \
  echo "0 * * * * /usr/local/bin/tr_pg_dump_hourly.sh"; \
  echo "30 2 * * * /usr/local/bin/tr_pg_basebackup_nightly.sh" ) | crontab -
crontab -l    # verify both lines are present
```

### 8.2 Install + configure rclone for B2

```bash
curl -fsSL https://rclone.org/install.sh | sudo bash
rclone config
```

Walkthrough:
- `n` (New remote)
- name: `b2`
- Storage: `b2`
- account: paste `B2_ACCOUNT_ID` (keyID from Phase 4.2)
- key: paste `B2_APPLICATION_KEY`
- hard delete: `false` (default)
- Edit advanced: `n`
- Confirm: `y`
- Quit config: `q`

Test:
```bash
rclone ls b2:trading-radar-backups   # empty for now
```

### 8.3 Manual run to verify both scripts work

```bash
sudo /usr/local/bin/tr_pg_dump_hourly.sh
ls -lh /var/backups/trading-radar/    # should see hourly_*.sql.gz file

sudo /usr/local/bin/tr_pg_basebackup_nightly.sh
# This takes a few minutes
ls /var/backups/trading-radar/        # should see full_*/ directory
rclone ls b2:trading-radar-backups   # should now show files
```

If both succeed, the cron will keep them rolling.

---

## Phase 9 — O3: Mobile real-device test on phone over LTE (15 minutes)

This proves the platform works on your phone away from your home Wi-Fi.

### 9.1 Test on iPhone Safari

1. **Turn off Wi-Fi on your iPhone** (forces LTE)
2. Open Safari
3. Navigate to `https://trading-radar.<yourdomain>`
4. You should see Cloudflare Access SSO → sign in with Google → app loads
5. Verify:
   - Page loads under 5 seconds
   - No horizontal scroll at any orientation (rotate phone)
   - Tap the **≡** menu button (top-left) → sidebar drawer slides in
   - Tap the **✕** to close
   - Tap timeframe pills (1m / 5m / 15m / 1h / 4h / 1d) — each switches without
     a layout shift
   - All 4 panels (Trade Status, Master Bias, Momentum, Trade Setup) are
     readable, no text cut off
   - Chart shows live BTC/USDT candles

### 9.2 Test on Android Chrome (if you have access to Android)

Same steps. Note any Android-specific issues.

### 9.3 Lighthouse mobile audit

On your laptop:
```powershell
npx lighthouse https://trading-radar.<yourdomain>/ --only-categories=performance,accessibility,best-practices --form-factor=mobile --view
```

Pass criteria:
- Performance ≥ 80
- Accessibility ≥ 90

If Performance < 80: typically the chart bundle is heavy; SP-1 will optimize.

### 9.4 Record result

Append to `docs/superpowers/log.md`:
```
2026-MM-DD SP-0 mobile test: PASS — iPhone Safari + Android Chrome over LTE; Lighthouse mobile perf 8X / a11y 9X
```

---

## Phase 10 — O4: Crash recovery test (15 minutes)

### 10.1 Simulate Oracle host crash

```bash
# SSH to Oracle:
cd ~/trading-radar
docker compose down
docker compose up -d
# Wait ~30 seconds:
docker compose ps     # all 4 healthy
curl http://localhost:8000/api/v1/health   # 200 OK
```

### 10.2 Verify audit chain still intact

```bash
docker compose exec -T backend python <<'EOF'
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_engine
from app.db.audit_verify import verify_chain

async def main():
    async with AsyncSession(get_engine()) as s:
        r1 = await verify_chain(s, 'predictions', columns=[
            'symbol','timeframe','ts','layer_scores','final_score',
            'direction','confidence','inputs_hash','model_version','cold_start',
        ])
        r2 = await verify_chain(s, 'paper_trades', columns=[
            'symbol','direction','entry_price','exit_price','stop_loss',
            'take_profit','position_size','opened_at','closed_at','pnl_pct',
            'max_drawdown_during','bars_held','exit_reason','reasoning','model_version',
        ])
        print('predictions ok:', r1.ok, 'violations:', len(r1.violations))
        print('paper_trades ok:', r2.ok, 'violations:', len(r2.violations))

asyncio.run(main())
EOF
```

Expected: both `ok: True`, both `violations: 0`.

### 10.3 Full reboot

```bash
sudo reboot
# Wait ~2 min, then SSH back in:
ssh -i $HOME\.ssh\oracle_key ubuntu@<public-ip>
docker compose -f ~/trading-radar/docker-compose.yml ps
curl http://localhost:8000/api/v1/health
```

Containers should auto-start (because `restart: unless-stopped` in compose).

### 10.4 Record result

Append to `docs/superpowers/log.md`:
```
2026-MM-DD SP-0 crash recovery: PASS — docker down/up + sudo reboot, audit chain intact
```

---

## Phase 11 — O5: Laptop independence test (1 hour wait)

Proves the platform truly runs on Oracle, independent of your laptop.

### 11.1 Close the laptop

Close the lid on your Windows laptop. Walk away. **Do not touch it for 1 hour.**

### 11.2 From your phone (Wi-Fi off, LTE on), check production

After 1 hour:
- Visit `https://trading-radar.<yourdomain>` — site loads
- Chart still updates with new candles
- Trade Status panel still updates

### 11.3 Confirm DB grew during the hour

Open laptop, SSH to Oracle:
```bash
docker compose exec -T postgres psql -U postgres trading_radar -c \
  "SELECT count(*) FROM predictions WHERE ts > NOW() - INTERVAL '1 hour';"
```
Should be > 0 (the live worker has been writing predictions while you were away).

### 11.4 Record result

```
2026-MM-DD SP-0 laptop independence: PASS — laptop closed 1h, production unaffected, N predictions written
```

---

## Phase 12 — O6: 24-hour soak (24 hours wait + 30 minutes check)

### 12.1 Let production run untouched for 24 hours

Just leave it. Don't change anything.

### 12.2 After 24h, run all checks

```bash
# 1. Data quality alerts in last 24h:
docker compose exec -T postgres psql -U postgres trading_radar -c \
  "SELECT check_name, count(*) FROM data_quality_alerts WHERE ts > NOW() - INTERVAL '24 hours' GROUP BY check_name;"
# Should be empty OR have only known-explainable rows (e.g. Binance maintenance)

# 2. Audit violations:
docker compose exec -T postgres psql -U postgres trading_radar -c \
  "SELECT count(*) FROM audit_violations;"
# Must be 0

# 3. Backup logs ran:
ls -lh /var/backups/trading-radar/
# Should see ~24 hourly_*.sql.gz and 1 full_* directory from last night

# 4. B2 upload succeeded:
rclone ls b2:trading-radar-backups | tail -5
# Should show last night's full_* files

# 5. Disk usage:
df -h /
# Used should be < 30 GB (10 GB postgres + 5 GB backups + 15 GB OS)

# 6. Memory:
free -h
# Used should be < 21 GB (within budget)

# 7. Container uptime:
docker compose ps
# All STATUS should show ~24h uptime
```

### 12.3 Record result

```
2026-MM-DD SP-0 24h soak: PASS — 0 audit violations, 0 dq alerts, all crons ran, mem within budget
```

---

## Phase 13 — Open the SP-0 PR + ship (10 minutes)

You already have `sp-0/main` merged into `main` locally (Claude did this).
The remote may not have the merge yet. Push it:

```powershell
cd a:\v5_Trade_bot
git -c safe.directory='A:/v5_Trade_bot' push origin main
```

Auto-deploy workflow on GitHub Actions will SSH to Oracle and run
`docker compose up -d --build && alembic upgrade head` — confirming end-to-end
CI/CD.

If you want a real GitHub PR (recommended for the audit trail):

```powershell
# Reset local main to before the merge:
git -c safe.directory='A:/v5_Trade_bot' reset --hard 226d128
git -c safe.directory='A:/v5_Trade_bot' push --force-with-lease origin main

# Now open a PR from sp-0/main → main:
gh pr create --base main --head sp-0/main \
  --title "SP-0: Tracer bullet" \
  --body "$(cat docs/superpowers/plans/2026-05-01-SP-0-tracer-bullet-plan.md | head -100)"
```

(Skip the reset if you're happy with the local merge being the source of truth;
just push.)

---

## End-state check

When all phases pass, SP-0 is shipped. The state is:

- ✅ Code merged to `main` and pushed to GitHub
- ✅ CI workflow runs on PRs
- ✅ Auto-deploy workflow ships every merge to Oracle
- ✅ Production at `https://trading-radar.<yourdomain>` reachable from phone over LTE
- ✅ Cloudflare Access gates traffic with Google SSO
- ✅ Hourly + nightly backups landing on Oracle disk + Backblaze B2
- ✅ 89 backend tests + 10 frontend tests + Playwright E2E pass
- ✅ Audit hash chain intact across crash + reboot
- ✅ All 6 manual validation gates (O2-O6) signed off in `docs/superpowers/log.md`

You are then ready for **SP-1 (ML Data Pipeline + Ghost Candles)** — the
highest-risk sub-project per the meta-plan.

---

## Troubleshooting common errors

| Symptom | Cause | Fix |
|---|---|---|
| `gh repo create` fails with auth error | Browser flow timed out | Re-run `gh auth login` |
| `docker compose ps` shows backend "unhealthy" | DB or Redis not yet ready | Wait 30s; check `docker compose logs backend` |
| Cloudflare 502 at the URL | Tunnel down or wrong port | `sudo systemctl restart cloudflared`; check `/etc/cloudflared/config.yml` |
| Cloudflare 521 | App on Oracle is down | `docker compose ps`; restart what's down |
| `oci-arm-host-capacity` runs forever | Oracle has no free Ampere capacity in your region | Try a second region in `config.yml`; keep polling; consider Hetzner CX22 (€4.50/mo) as fallback |
| `pg_basebackup` fails inside container | Postgres user lacks `REPLICATION` priv | Add to Postgres init: `ALTER USER postgres WITH REPLICATION;` then re-run |
| GitHub Actions deploy fails: "Permission denied (publickey)" | Wrong key in `ORACLE_SSH_KEY` secret | Re-paste the FULL key including `-----BEGIN/END-----` lines |
| Mobile UI looks broken | Tailwind not built | `docker compose exec -T frontend npm run build`; restart frontend |

If you get stuck, take a screenshot of the error and tell Claude — most
errors map to a one-line fix.
