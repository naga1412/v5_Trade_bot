# Cloudflare Access Setup (SP-0)

## Prerequisites
- Cloudflare account (free) with a domain (cheapest: $9/yr from Cloudflare Registrar).
- Zero Trust dashboard: https://one.dash.cloudflare.com

## 1. Create the Tunnel
1. Zero Trust → Networks → Tunnels → **Create a tunnel**.
2. Connector: Cloudflared. Name: `trading-radar`. Save tunnel token (used to install on Oracle host).
3. Add a public hostname:
   - Subdomain: `trading-radar`
   - Domain: your domain
   - Service: `HTTP localhost:5173` (frontend)
4. Add a second route via config file (path-based) for `/api/*` and `/ws/*` → `HTTP localhost:8000`.
   - This requires editing `~/.cloudflared/config.yml` on the Oracle host (see `tunnel-config.yml.example`).
5. Install cloudflared on Oracle (Ubuntu ARM64):
   ```bash
   wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64
   sudo mv cloudflared-linux-arm64 /usr/local/bin/cloudflared
   sudo chmod +x /usr/local/bin/cloudflared
   sudo cloudflared service install <YOUR_TUNNEL_TOKEN>
   sudo systemctl enable --now cloudflared
   sudo systemctl status cloudflared
   ```

## 2. Create the Access Application
1. Zero Trust → Access → Applications → **Add an application** → Self-hosted.
2. Name: `trading-radar`.
3. Application domain: `trading-radar.<yourdomain>`.
4. Identity providers: Google (configure in Zero Trust → Settings → Authentication if not already).
5. Set "Application Audience (AUD) Tag" — copy this; it becomes the `CF_ACCESS_AUD` env var.
6. Save.

## 3. Create the Policy
1. In the application → Policies → **Add a policy**.
2. Name: `only-me`. Action: Allow.
3. Include rule: Emails → `your-email@gmail.com`.
4. Save.

## 4. Test
1. Open `https://trading-radar.<yourdomain>` in a private browser window.
2. Expect Cloudflare Access SSO page → sign in with Google → redirected to app.
3. Verify a request to `/api/v1/predict/BTC-USDT/1h` arrives at backend with header `Cf-Access-Jwt-Assertion`.
4. Test rejection: in another browser without the cookie, hit a route directly → 302 to SSO.

## 5. Set backend env vars
- `CF_ACCESS_TEAM_DOMAIN=yourteam.cloudflareaccess.com` (find in Zero Trust → Settings → Custom Pages or General; format is "<teamname>.cloudflareaccess.com")
- `CF_ACCESS_AUD=<aud-tag-from-step-2.5>`

After updating `.env`, restart backend:
```bash
docker compose restart backend
```

## 6. Verification
- 401 on missing JWT: `curl https://trading-radar.<yourdomain>/api/v1/predict/BTC-USDT/1h` (no cookie) → expect 302 from Cloudflare or 401 from backend.
- 200 with browser session: open the app in browser → predict endpoint returns 200.
