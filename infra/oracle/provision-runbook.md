# Oracle Cloud Always Free — Ampere A1 Provisioning

## 1. Account & VM

### Account
1. Sign up at https://www.oracle.com/cloud/free/ (requires credit card; you will not be charged for Always Free).
2. Pick **home region** carefully: Mumbai or Hyderabad for India users (low latency). Cannot be changed later.

### Provision the VM
The "Always Free Eligible" Ampere A1 shape is regularly out of capacity. Use the polling script:

```bash
git clone https://github.com/hitrov/oci-arm-host-capacity.git
cd oci-arm-host-capacity
# Follow the README to set up OCI CLI keys and config
nano config.yml   # Set shape, OCPU, memory, region, image
node index.js     # Polls every 60s, creates instance when capacity available
```

Recommended config:
```yaml
shape: VM.Standard.A1.Flex
ocpus: 4
memory_gb: 24
image: <Canonical-Ubuntu-22.04-aarch64-image-OCID>
boot_volume_size_gb: 100
```

Expected wait: 1–14 days. Develop on laptop dev mirror in the meantime.

### Once provisioned
1. Note the public IP (e.g., `132.226.45.123`).
2. SSH in:
   ```bash
   ssh -i ~/.ssh/oracle_key ubuntu@<public-ip>
   ```
3. Open ingress rules in OCI Networking → VCN → Security List:
   - Inbound: TCP 22 from your IP only (not 0.0.0.0/0).
   - **Do not open 8000 / 5173 publicly.** Cloudflare Tunnel goes outbound from this host.

## 2. OS hardening

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y ufw fail2ban
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw enable
sudo systemctl enable --now fail2ban
```

## 3. Install Docker + dependencies

```bash
# On Oracle host:
sudo apt update && sudo apt install -y \
    apt-transport-https ca-certificates curl gnupg lsb-release git rsync postgresql-client

# Docker (official repo for ARM64)
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
newgrp docker

# Verify
docker --version
docker compose version
```

Verification:

```bash
docker run --rm hello-world
```

Expected: "Hello from Docker!" message.

## 4. Clone repo

```bash
# On Oracle:
ssh-keygen -t ed25519 -C "oracle-trading-radar" -f ~/.ssh/github_deploy
cat ~/.ssh/github_deploy.pub
# Add this key to GitHub repo as a Deploy Key (read-only).
```

```bash
GIT_SSH_COMMAND="ssh -i ~/.ssh/github_deploy" \
  git clone git@github.com:<your-username>/v5_Trade_bot.git ~/trading-radar
cd ~/trading-radar
git checkout sp-0/main
```

## 5. Configure environment

```bash
cp .env.example .env
nano .env
# Fill in:
# - POSTGRES_PASSWORD (strong random)
# - SECRET_KEY (python -c "import secrets; print(secrets.token_urlsafe(64))")
# - CF_ACCESS_TEAM_DOMAIN, CF_ACCESS_AUD (from Phase L runbook)
# - B2 credentials (after Phase N)
# Set ENV=production
chmod 600 .env
```

## 6. Build + start stack

```bash
docker compose up -d --build
docker compose ps
```

Expected: 4 containers all "healthy".

## 7. Run migrations

```bash
docker compose exec backend alembic upgrade head
```

Expected: `Running upgrade 0001_initial -> 0002_audit_chain`.

## 8. Smoke check

```bash
curl http://localhost:8000/api/v1/health
# Expected: {"status":"ok","service":"trading-radar","version":"0.1.0-sp-0"}
```
