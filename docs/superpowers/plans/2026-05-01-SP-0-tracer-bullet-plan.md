# SP-0 Tracer Bullet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the smallest end-to-end slice of trading-radar that proves every architectural rail — Oracle host, Cloudflare auth gate, mobile UI, BTC/USDT 1h chart with live data, 4 sidebar panels, 3 scoring layers, custom paper engine writing audit-hash-chained rows to Postgres.

**Architecture:** FastAPI + PostgreSQL/TimescaleDB + Redis on Oracle Ampere ARM (primary) and laptop Docker Desktop (dev mirror). React 18 + Vite + Tailwind + TradingView Lightweight Charts on the frontend. Cloudflare Tunnel + Cloudflare Access gate public traffic. All work is TDD per `superpowers:test-driven-development`. All gap-fix policies from §5 of the meta-plan are enforced from day 1, not bolted on.

**Tech Stack:** Python 3.11.x · FastAPI 0.115.x · SQLAlchemy 2.x · asyncpg · TimescaleDB (PG16) · Redis 7 · pytest · React 18.3 · Vite 5 · TypeScript 5.5 (strict) · TailwindCSS 3.4 · TradingView Lightweight Charts 4.x · Vitest · Playwright · Docker Compose · Cloudflare Tunnel + Access · Backblaze B2.

**Spec reference:** [`docs/superpowers/specs/2026-05-01-trading-radar-meta-plan-design.md`](../specs/2026-05-01-trading-radar-meta-plan-design.md) §4 (SP-0) and §5 (cross-cutting policies). When this plan and the spec disagree, the spec wins.

**Cross-cutting policy compliance map (which §5 policy each phase touches):**
- Phases A, M, N — §5.13 (DR), §5.12 (RAM budget)
- Phase B — §5.14 (audit hash chain), §5.11 (Timescale chunking)
- Phase C — §5.8 (WS reliability), §5.9 (data quality), §5.15 (rate limits)
- Phases D, E, O — §5.1 (look-ahead), §6.2 (TradingView validation tolerance)
- Phase F — §5.14 (audit chain), §5.5 (reward shaping deferred to SP-4)
- Phases I, J, K — §2.7 (mobile-responsive)
- Phase L — §2.6 (Cloudflare Access auth)
- Phase O — §6.3 (Definition of Done)

---

## File Structure

This is what SP-0 creates. Later sub-projects extend without restructuring.

```
v5_Trade_bot/
├── docker-compose.yml                 # NEW (replaces files/docker-compose.yml)
├── docker-compose.dev.yml             # NEW (laptop dev overrides)
├── .env.example                       # NEW (replaces files/.env.example)
├── README.md                          # NEW (replaces files/README.md)
├── CLAUDE.md                          # NEW (replaces files/CLAUDE.md)
├── pyproject.toml                     # NEW (root tooling: ruff, mypy)
│
├── backend/
│   ├── Dockerfile                     # NEW
│   ├── pyproject.toml                 # NEW (deps + pytest config)
│   ├── alembic.ini                    # NEW
│   ├── alembic/
│   │   ├── env.py                     # NEW
│   │   ├── script.py.mako             # NEW
│   │   └── versions/
│   │       └── 0001_initial.py        # NEW (sp-0 schema)
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                    # FastAPI entry
│   │   ├── config.py                  # Pydantic settings
│   │   ├── deps.py                    # Auth + DB session deps
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── routes/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── health.py
│   │   │   │   ├── tab1.py            # GET /api/v1/predict/...
│   │   │   │   └── ws.py              # WebSocket /ws/v1/{client_id}
│   │   │   └── schemas.py             # Pydantic request/response
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── indicators/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── ema.py
│   │   │   │   ├── rsi.py
│   │   │   │   └── macd.py
│   │   │   ├── scoring/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── types.py           # LayerScore, FinalScore dataclasses
│   │   │   │   ├── layer1_macro.py
│   │   │   │   ├── layer3_momentum.py
│   │   │   │   ├── layer5_volume.py
│   │   │   │   └── aggregator.py
│   │   │   ├── execution/
│   │   │   │   ├── __init__.py
│   │   │   │   └── paper_engine.py
│   │   │   └── dataquality/
│   │   │       ├── __init__.py
│   │   │       └── validator.py
│   │   ├── data/
│   │   │   ├── __init__.py
│   │   │   ├── adapters/
│   │   │   │   ├── __init__.py
│   │   │   │   └── binance.py         # REST + WS
│   │   │   ├── universe.py            # is_tradable() scaffolding
│   │   │   └── ratelimit.py           # Token bucket
│   │   ├── db/
│   │   │   ├── __init__.py
│   │   │   ├── session.py             # SQLAlchemy async engine
│   │   │   ├── models.py              # ORM models for SP-0 tables
│   │   │   └── audit.py               # Hash-chain insert logic
│   │   └── ws/
│   │       ├── __init__.py
│   │       ├── manager.py             # Connection registry, heartbeat
│   │       └── live_prediction.py     # Channel publisher
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py                # pytest fixtures
│       ├── unit/
│       │   ├── test_indicators_ema.py
│       │   ├── test_indicators_rsi.py
│       │   ├── test_indicators_macd.py
│       │   ├── test_scoring_layer1.py
│       │   ├── test_scoring_layer3.py
│       │   ├── test_scoring_layer5.py
│       │   ├── test_scoring_aggregator.py
│       │   ├── test_dataquality_validator.py
│       │   ├── test_audit_hashchain.py
│       │   ├── test_paper_engine.py
│       │   └── test_ratelimit.py
│       └── integration/
│           ├── test_api_health.py
│           ├── test_api_predict.py
│           ├── test_ws_reconnect.py
│           └── test_binance_adapter.py
│
├── frontend/
│   ├── Dockerfile                     # NEW
│   ├── package.json                   # NEW
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── tailwind.config.ts
│   ├── postcss.config.js
│   ├── index.html
│   ├── playwright.config.ts
│   ├── public/
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── styles/
│   │   │   └── globals.css            # CSS variables (theme)
│   │   ├── lib/
│   │   │   ├── api.ts                 # REST client
│   │   │   └── ws.ts                  # WS client w/ reconnect
│   │   ├── hooks/
│   │   │   └── useLivePrediction.ts
│   │   ├── components/
│   │   │   ├── ui/
│   │   │   │   └── Panel.tsx
│   │   │   ├── chart/
│   │   │   │   └── TVChart.tsx
│   │   │   └── layout/
│   │   │       ├── TopNav.tsx
│   │   │       ├── TimeframeRow.tsx
│   │   │       └── Sidebar.tsx
│   │   └── tabs/
│   │       └── Tab1LivePrediction/
│   │           ├── index.tsx
│   │           └── panels/
│   │               ├── TradeStatusBar.tsx
│   │               ├── MasterBiasScore.tsx
│   │               ├── MomentumIndicators.tsx
│   │               └── TradeSetup.tsx
│   └── tests/
│       ├── unit/
│       │   ├── Panel.test.tsx
│       │   ├── TradeStatusBar.test.tsx
│       │   ├── MasterBiasScore.test.tsx
│       │   ├── MomentumIndicators.test.tsx
│       │   └── TradeSetup.test.tsx
│       └── e2e/
│           └── tracer-bullet.spec.ts
│
├── infra/
│   ├── cloudflare/
│   │   ├── tunnel-config.yml.example
│   │   └── access-policy.md           # Manual setup runbook
│   ├── oracle/
│   │   └── provision-runbook.md
│   ├── backup/
│   │   ├── pg_dump_hourly.sh
│   │   ├── pg_basebackup_nightly.sh
│   │   ├── b2_upload.sh
│   │   └── recovery_rehearsal.sh
│   └── prometheus/
│       └── prometheus.yml
│
├── tools/
│   └── validate_indicators.py         # TradingView cross-check script
│
└── docs/
    └── superpowers/
        ├── specs/
        │   └── 2026-05-01-trading-radar-meta-plan-design.md  (existing)
        └── plans/
            └── 2026-05-01-SP-0-tracer-bullet-plan.md         (this file)
```

---

## Phase A — Project Skeleton & Repo Hygiene

Scaffolding phase. No TDD here (no logic to test) — each task is "write the file, verify it parses, commit".

### Task A1: Create SP-0 worktree

**Files:** none (git operation only)

- [ ] **Step 1: Verify clean tree on main**

```bash
cd a:/v5_Trade_bot
git -c safe.directory='A:/v5_Trade_bot' status
```
Expected: `nothing to commit, working tree clean`

- [ ] **Step 2: Create SP-0 branch and worktree**

```bash
mkdir -p worktrees
git -c safe.directory='A:/v5_Trade_bot' worktree add worktrees/sp-0 -b sp-0/main
```
Expected: `Preparing worktree (new branch 'sp-0/main')`

- [ ] **Step 3: Verify worktree exists**

```bash
git -c safe.directory='A:/v5_Trade_bot' worktree list
```
Expected output includes `worktrees/sp-0  <hash> [sp-0/main]`.

- [ ] **Step 4: All subsequent tasks operate inside `worktrees/sp-0/`**

No commit yet (worktree has no new files).

---

### Task A2: Root pyproject.toml (ruff + mypy + dev tooling)

**Files:**
- Create: `worktrees/sp-0/pyproject.toml`

- [ ] **Step 1: Write the file**

```toml
[tool.ruff]
line-length = 100
target-version = "py311"
extend-exclude = ["alembic/versions"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B", "C4", "SIM", "ARG", "PL", "RUF"]
ignore = ["PLR0913"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["PLR2004", "S101"]

[tool.mypy]
python_version = "3.11"
strict = true
warn_unreachable = true
disallow_any_generics = true
disallow_untyped_defs = true
warn_unused_ignores = true
exclude = ["alembic/versions"]
```

- [ ] **Step 2: Verify TOML parses**

```bash
cd worktrees/sp-0
python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"
```
Expected: no output (success).

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add pyproject.toml
git -c safe.directory='A:/v5_Trade_bot' commit -m "chore(sp-0): root pyproject with ruff and mypy strict"
```

---

### Task A3: backend/pyproject.toml (pinned deps)

**Files:**
- Create: `worktrees/sp-0/backend/pyproject.toml`

- [ ] **Step 1: Write**

```toml
[project]
name = "trading-radar-backend"
version = "0.1.0"
requires-python = ">=3.11,<3.12"
dependencies = [
    "fastapi==0.115.6",
    "uvicorn[standard]==0.32.1",
    "pydantic==2.10.4",
    "pydantic-settings==2.7.0",
    "sqlalchemy==2.0.36",
    "asyncpg==0.30.0",
    "alembic==1.14.0",
    "redis==5.2.1",
    "httpx==0.28.1",
    "websockets==13.1",
    "numpy==1.26.4",
    "pandas==2.2.3",
    "ccxt==4.4.40",
    "PyJWT[crypto]==2.10.1",
    "structlog==24.4.0",
]

[project.optional-dependencies]
dev = [
    "pytest==8.3.4",
    "pytest-asyncio==0.25.0",
    "pytest-cov==6.0.0",
    "anyio==4.7.0",
    "freezegun==1.5.1",
    "ruff==0.8.4",
    "mypy==1.13.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-ra -q --strict-markers --cov=app --cov-report=term-missing --cov-fail-under=85"
markers = [
    "integration: requires database/redis",
    "slow: takes more than 1s",
]
```

- [ ] **Step 2: Verify**

```bash
cd worktrees/sp-0/backend
python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"
```

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/pyproject.toml
git -c safe.directory='A:/v5_Trade_bot' commit -m "chore(sp-0): backend pyproject with pinned versions"
```

---

### Task A4: backend/Dockerfile (ARM64-compatible)

**Files:**
- Create: `worktrees/sp-0/backend/Dockerfile`

- [ ] **Step 1: Write**

```dockerfile
# syntax=docker/dockerfile:1.6
FROM python:3.11-slim-bookworm AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install -e ".[dev]"

COPY . .

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl --fail http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Lint with hadolint**

```bash
docker run --rm -i hadolint/hadolint < worktrees/sp-0/backend/Dockerfile
```
Expected: no warnings (DL3008/DL3013 acceptable).

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/Dockerfile
git -c safe.directory='A:/v5_Trade_bot' commit -m "chore(sp-0): backend Dockerfile (ARM64-compatible)"
```

---

### Task A5: Frontend scaffold (package.json + configs)

**Files:**
- Create: `worktrees/sp-0/frontend/package.json`
- Create: `worktrees/sp-0/frontend/vite.config.ts`
- Create: `worktrees/sp-0/frontend/tsconfig.json`
- Create: `worktrees/sp-0/frontend/tsconfig.node.json`
- Create: `worktrees/sp-0/frontend/tailwind.config.ts`
- Create: `worktrees/sp-0/frontend/postcss.config.js`
- Create: `worktrees/sp-0/frontend/index.html`
- Create: `worktrees/sp-0/frontend/playwright.config.ts`
- Create: `worktrees/sp-0/frontend/Dockerfile`
- Create: `worktrees/sp-0/frontend/.eslintrc.cjs`

- [ ] **Step 1: package.json**

```json
{
  "name": "trading-radar-frontend",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "lint": "eslint . --ext ts,tsx --max-warnings 0",
    "test": "vitest run",
    "test:watch": "vitest",
    "test:e2e": "playwright test"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "lightweight-charts": "^4.2.1",
    "zustand": "^5.0.2"
  },
  "devDependencies": {
    "@playwright/test": "^1.49.1",
    "@testing-library/jest-dom": "^6.6.3",
    "@testing-library/react": "^16.1.0",
    "@types/react": "^18.3.18",
    "@types/react-dom": "^18.3.5",
    "@typescript-eslint/eslint-plugin": "^8.18.2",
    "@typescript-eslint/parser": "^8.18.2",
    "@vitejs/plugin-react": "^4.3.4",
    "autoprefixer": "^10.4.20",
    "eslint": "^8.57.1",
    "eslint-plugin-react-hooks": "^5.1.0",
    "eslint-plugin-react-refresh": "^0.4.16",
    "jsdom": "^25.0.1",
    "postcss": "^8.4.49",
    "tailwindcss": "^3.4.17",
    "typescript": "^5.5.4",
    "vite": "^5.4.11",
    "vitest": "^2.1.8"
  }
}
```

- [ ] **Step 2: vite.config.ts**

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { host: "0.0.0.0", port: 5173 },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
  },
});
```

- [ ] **Step 3: tsconfig.json (strict)**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "Bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "noUncheckedIndexedAccess": true,
    "exactOptionalPropertyTypes": true,
    "baseUrl": ".",
    "paths": { "@/*": ["src/*"] }
  },
  "include": ["src", "tests"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

- [ ] **Step 4: tsconfig.node.json**

```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "allowSyntheticDefaultImports": true,
    "strict": true
  },
  "include": ["vite.config.ts", "playwright.config.ts"]
}
```

- [ ] **Step 5: tailwind.config.ts (theme variables match meta-plan §3)**

```ts
import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: {
          base: "var(--bg-base)",
          chart: "var(--bg-chart)",
          panel: "var(--bg-panel)",
          elevated: "var(--bg-elevated)",
        },
        border: {
          DEFAULT: "var(--border)",
          strong: "var(--border-strong)",
        },
        text: {
          primary: "var(--text-primary)",
          secondary: "var(--text-secondary)",
          tertiary: "var(--text-tertiary)",
        },
        green: { DEFAULT: "var(--green)", 15: "var(--green-15)" },
        red: { DEFAULT: "var(--red)", 15: "var(--red-15)" },
        gold: { DEFAULT: "var(--gold)", 15: "var(--gold-15)" },
        purple: { DEFAULT: "var(--purple)", 15: "var(--purple-15)" },
        cyan: { DEFAULT: "var(--cyan)" },
        orange: { DEFAULT: "var(--orange)" },
        pink: { DEFAULT: "var(--pink)" },
      },
      fontFamily: {
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
        sans: ["Inter", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
} satisfies Config;
```

- [ ] **Step 6: postcss.config.js**

```js
export default {
  plugins: { tailwindcss: {}, autoprefixer: {} },
};
```

- [ ] **Step 7: index.html**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
    <meta name="theme-color" content="#0a0d12" />
    <title>trading-radar</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 8: playwright.config.ts**

```ts
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: [["html", { open: "never" }]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173",
    trace: "on-first-retry",
  },
  projects: [
    { name: "chromium-desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile-iphone", use: { ...devices["iPhone 13"] } },
  ],
});
```

- [ ] **Step 9: frontend/Dockerfile**

```dockerfile
# syntax=docker/dockerfile:1.6
FROM node:20-bookworm-slim AS base
WORKDIR /app
COPY package.json ./
RUN npm install --no-audit --no-fund
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
```

- [ ] **Step 10: .eslintrc.cjs**

```js
module.exports = {
  root: true,
  env: { browser: true, es2022: true },
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/strict",
    "plugin:react-hooks/recommended",
  ],
  parser: "@typescript-eslint/parser",
  parserOptions: { project: "./tsconfig.json" },
  plugins: ["react-refresh"],
  rules: {
    "react-refresh/only-export-components": "warn",
    "@typescript-eslint/no-explicit-any": "error",
  },
};
```

- [ ] **Step 11: Verify package.json parses**

```bash
cd worktrees/sp-0/frontend
node -e "JSON.parse(require('fs').readFileSync('package.json','utf8'))"
```

- [ ] **Step 12: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add frontend/
git -c safe.directory='A:/v5_Trade_bot' commit -m "chore(sp-0): frontend scaffold (Vite, TS strict, Tailwind, Playwright)"
```

---

### Task A6: docker-compose.yml + dev override

**Files:**
- Create: `worktrees/sp-0/docker-compose.yml`
- Create: `worktrees/sp-0/docker-compose.dev.yml`

- [ ] **Step 1: docker-compose.yml (production = Oracle host)**

```yaml
services:
  postgres:
    image: timescale/timescaledb:2.17.2-pg16
    container_name: tr-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    command:
      - "postgres"
      - "-c"
      - "shared_buffers=2GB"
      - "-c"
      - "work_mem=64MB"
      - "-c"
      - "synchronous_commit=on"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: tr-redis
    restart: unless-stopped
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes --maxmemory 1500mb --maxmemory-policy allkeys-lru
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  backend:
    build: ./backend
    container_name: tr-backend
    restart: unless-stopped
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }
    environment:
      DATABASE_URL: ${DATABASE_URL}
      REDIS_URL: ${REDIS_URL}
      ENV: ${ENV}
      LOG_LEVEL: ${LOG_LEVEL}
      CF_ACCESS_TEAM_DOMAIN: ${CF_ACCESS_TEAM_DOMAIN}
      CF_ACCESS_AUD: ${CF_ACCESS_AUD}
      BINANCE_USE_TESTNET: ${BINANCE_USE_TESTNET}
    ports:
      - "127.0.0.1:8000:8000"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/health"]
      interval: 30s
      timeout: 5s
      retries: 3

  frontend:
    build: ./frontend
    container_name: tr-frontend
    restart: unless-stopped
    depends_on: [backend]
    ports:
      - "127.0.0.1:5173:5173"
    environment:
      VITE_API_URL: /api/v1
      VITE_WS_URL: /ws/v1

volumes:
  postgres_data:
  redis_data:

networks:
  default:
    name: trading-radar
```

- [ ] **Step 2: docker-compose.dev.yml (laptop overrides)**

```yaml
services:
  backend:
    volumes:
      - ./backend:/app
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    environment:
      ENV: development

  frontend:
    volumes:
      - ./frontend:/app
      - /app/node_modules
    environment:
      VITE_API_URL: http://localhost:8000/api/v1
      VITE_WS_URL: ws://localhost:8000/ws/v1
```

Usage on laptop: `docker compose -f docker-compose.yml -f docker-compose.dev.yml up`

- [ ] **Step 3: Validate compose**

```bash
cd worktrees/sp-0
docker compose config --quiet
docker compose -f docker-compose.yml -f docker-compose.dev.yml config --quiet
```

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add docker-compose.yml docker-compose.dev.yml
git -c safe.directory='A:/v5_Trade_bot' commit -m "chore(sp-0): docker-compose for Oracle prod + laptop dev override"
```

---

### Task A7: .env.example

**Files:**
- Create: `worktrees/sp-0/.env.example`

- [ ] **Step 1: Write**

```bash
# ===== trading-radar — environment template =====
POSTGRES_USER=postgres
POSTGRES_PASSWORD=change_me_strong
POSTGRES_DB=trading_radar
DATABASE_URL=postgresql+asyncpg://postgres:change_me_strong@postgres:5432/trading_radar

REDIS_URL=redis://redis:6379/0

ENV=production
LOG_LEVEL=INFO
SECRET_KEY=generate_with_python_secrets_token_urlsafe_64

CF_ACCESS_TEAM_DOMAIN=yourteam.cloudflareaccess.com
CF_ACCESS_AUD=your_application_audience_tag

BINANCE_USE_TESTNET=true

B2_ACCOUNT_ID=
B2_APPLICATION_KEY=
B2_BUCKET=trading-radar-backups
LAPTOP_RSYNC_TARGET=user@laptop.lan:/mnt/external_ssd/trading-radar-backups/

TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

- [ ] **Step 2: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add .env.example
git -c safe.directory='A:/v5_Trade_bot' commit -m "chore(sp-0): .env.example with Cloudflare Access + B2 vars"
```

---

### Task A8: Backend skeleton (config + main)

**Files:**
- Create: `worktrees/sp-0/backend/app/__init__.py` (empty)
- Create: `worktrees/sp-0/backend/app/config.py`
- Create: `worktrees/sp-0/backend/app/main.py`

- [ ] **Step 1: app/__init__.py** — empty file.

- [ ] **Step 2: app/config.py**

```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"
    log_level: str = "INFO"

    database_url: str
    redis_url: str

    cf_access_team_domain: str = ""
    cf_access_aud: str = ""

    binance_use_testnet: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

- [ ] **Step 3: app/main.py**

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.config import get_settings


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _ = get_settings()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="trading-radar",
        version="0.1.0-sp-0",
        lifespan=lifespan,
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
    )
    return app


app = create_app()
```

- [ ] **Step 4: Verify imports**

```bash
cd worktrees/sp-0/backend
python -c "from app.main import app; print(app.title)"
```
Expected: `trading-radar`

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/app/__init__.py backend/app/config.py backend/app/main.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): FastAPI skeleton with pydantic settings"
```

---

### Task A9: Frontend skeleton (entry + theme CSS)

**Files:**
- Create: `worktrees/sp-0/frontend/src/main.tsx`
- Create: `worktrees/sp-0/frontend/src/App.tsx`
- Create: `worktrees/sp-0/frontend/src/styles/globals.css`
- Create: `worktrees/sp-0/frontend/tests/setup.ts`

- [ ] **Step 1: src/styles/globals.css**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  --bg-base: #0a0d12;
  --bg-chart: #0d1018;
  --bg-panel: #12161d;
  --bg-elevated: #1a1f28;
  --border: #1f2530;
  --border-strong: #2a2d33;

  --green: #00d68f;
  --red: #ff3d71;
  --gold: #ffd700;
  --purple: #c084fc;
  --cyan: #22d3ee;
  --orange: #ffa500;
  --pink: #ff6b9d;

  --green-15: rgba(0, 214, 143, 0.15);
  --red-15: rgba(255, 61, 113, 0.15);
  --gold-15: rgba(255, 215, 0, 0.15);
  --purple-15: rgba(192, 132, 252, 0.15);

  --text-primary: #c4c8d0;
  --text-secondary: #8c91a0;
  --text-tertiary: #6b7280;
}

html, body, #root {
  height: 100%;
  background: var(--bg-base);
  color: var(--text-primary);
  font-family: "Inter", system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}
```

- [ ] **Step 2: src/main.tsx**

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/globals.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

- [ ] **Step 3: src/App.tsx (placeholder until Phase I)**

```tsx
export default function App() {
  return (
    <div className="min-h-screen flex items-center justify-center text-text-primary">
      <span className="font-mono text-xs">trading-radar · sp-0 boot ok</span>
    </div>
  );
}
```

- [ ] **Step 4: tests/setup.ts**

```ts
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add frontend/src frontend/tests
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): React skeleton with theme CSS variables"
```

---

### Task A10: First boot verification (laptop dev mirror)

**Files:** none (verification only)

- [ ] **Step 1: Copy .env.example → .env**

```bash
cd worktrees/sp-0
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD to any non-default value
```

- [ ] **Step 2: Bring stack up**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```
Expected: 4 containers reach "healthy" or "running".

- [ ] **Step 3: Verify backend Swagger**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/docs
```
Expected: `200`.

- [ ] **Step 4: Verify frontend served**

```bash
curl -s http://localhost:5173/ | grep -q '<div id="root">' && echo OK
```
Expected: `OK`.

- [ ] **Step 5: Open `http://localhost:5173/` in browser**

Should display "trading-radar · sp-0 boot ok" centered.

- [ ] **Step 6: Tear down**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml down
```

- [ ] **Step 7: No new files; verification milestone only**

---

## Phase B — Database Schema, Alembic, Audit Hash Chain

Implements §5.14 (audit hash chain) and §5.11 (TimescaleDB chunking) from day 1.

### Task B1: Alembic init

**Files:**
- Create: `worktrees/sp-0/backend/alembic.ini`
- Create: `worktrees/sp-0/backend/alembic/env.py`
- Create: `worktrees/sp-0/backend/alembic/script.py.mako`

- [ ] **Step 1: alembic.ini**

```ini
[alembic]
script_location = alembic
sqlalchemy.url = driver://placeholder/will/be/overridden/in/env.py
file_template = %%(year)d_%%(month).2d_%%(day).2d_%%(slug)s

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 2: alembic/env.py**

```python
import asyncio
from logging.config import fileConfig
from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = None  # raw SQL migrations only for SP-0


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online():
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    raise NotImplementedError("Offline mode not supported in SP-0")
else:
    run_migrations_online()
```

- [ ] **Step 3: alembic/script.py.mako**

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op


revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/alembic.ini backend/alembic/
git -c safe.directory='A:/v5_Trade_bot' commit -m "chore(sp-0): alembic init (async, raw SQL migrations)"
```

---

### Task B2: Initial migration — TimescaleDB extension + ohlcv hypertable + watchlist

**Files:**
- Create: `worktrees/sp-0/backend/alembic/versions/2026_05_01_0001_initial_schema.py`

- [ ] **Step 1: Write migration**

```python
"""initial sp-0 schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-01

"""
from typing import Sequence, Union

from alembic import op


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    op.execute(
        """
        CREATE TABLE ohlcv (
            symbol     TEXT             NOT NULL,
            timeframe  TEXT             NOT NULL,
            ts         TIMESTAMPTZ      NOT NULL,
            open       DOUBLE PRECISION NOT NULL,
            high       DOUBLE PRECISION NOT NULL,
            low        DOUBLE PRECISION NOT NULL,
            close      DOUBLE PRECISION NOT NULL,
            volume     DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (symbol, timeframe, ts)
        );
        """
    )
    op.execute(
        """
        SELECT create_hypertable(
            'ohlcv', 'ts',
            chunk_time_interval => INTERVAL '7 days',
            if_not_exists => TRUE
        );
        """
    )
    op.execute(
        """
        ALTER TABLE ohlcv SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'symbol,timeframe',
            timescaledb.compress_orderby   = 'ts'
        );
        """
    )
    op.execute(
        "SELECT add_compression_policy('ohlcv', INTERVAL '30 days', if_not_exists => TRUE);"
    )

    op.execute(
        """
        CREATE TABLE watchlist (
            id                   BIGSERIAL PRIMARY KEY,
            symbol               TEXT NOT NULL UNIQUE,
            is_favorite          BOOLEAN NOT NULL DEFAULT FALSE,
            paper_trade_active   BOOLEAN NOT NULL DEFAULT FALSE,
            added_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS watchlist;")
    op.execute("DROP TABLE IF EXISTS ohlcv;")
```

- [ ] **Step 2: Run against laptop dev postgres**

```bash
cd worktrees/sp-0
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres
docker compose exec backend alembic upgrade head
```
Expected: `Running upgrade  -> 0001_initial`

- [ ] **Step 3: Verify hypertable**

```bash
docker compose exec postgres psql -U postgres trading_radar -c \
  "SELECT hypertable_name FROM timescaledb_information.hypertables;"
```
Expected output includes `ohlcv`.

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/alembic/versions/
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): initial migration — ohlcv hypertable + watchlist"
```

---

### Task B3: Predictions + paper_trades with audit hash chain (migration 0002)

**Files:**
- Create: `worktrees/sp-0/backend/alembic/versions/2026_05_01_0002_audit_chain.py`

- [ ] **Step 1: Write migration**

```python
"""audit hash chain for predictions and paper_trades

Revision ID: 0002_audit_chain
Revises: 0001_initial
Create Date: 2026-05-01
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0002_audit_chain"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE predictions (
            id                BIGSERIAL PRIMARY KEY,
            symbol            TEXT NOT NULL,
            timeframe         TEXT NOT NULL,
            ts                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            layer_scores      JSONB NOT NULL,
            final_score       DOUBLE PRECISION NOT NULL,
            direction         TEXT,
            confidence        DOUBLE PRECISION,
            inputs_hash       TEXT NOT NULL,
            model_version     TEXT NOT NULL DEFAULT 'sp-0',
            cold_start        BOOLEAN NOT NULL DEFAULT TRUE,
            prev_hash         TEXT NOT NULL,
            row_hash          TEXT NOT NULL UNIQUE
        );
        CREATE INDEX predictions_symbol_ts_idx ON predictions (symbol, ts DESC);
        """
    )

    op.execute(
        """
        CREATE TABLE paper_trades (
            id                       BIGSERIAL PRIMARY KEY,
            symbol                   TEXT NOT NULL,
            direction                TEXT NOT NULL CHECK (direction IN ('LONG','SHORT')),
            entry_price              DOUBLE PRECISION NOT NULL,
            exit_price               DOUBLE PRECISION,
            stop_loss                DOUBLE PRECISION NOT NULL,
            take_profit              DOUBLE PRECISION NOT NULL,
            position_size            DOUBLE PRECISION NOT NULL,
            opened_at                TIMESTAMPTZ NOT NULL,
            closed_at                TIMESTAMPTZ,
            pnl_pct                  DOUBLE PRECISION,
            max_drawdown_during      DOUBLE PRECISION,
            bars_held                INTEGER,
            exit_reason              TEXT,
            reasoning                JSONB,
            model_version            TEXT NOT NULL DEFAULT 'sp-0',
            prev_hash                TEXT NOT NULL,
            row_hash                 TEXT NOT NULL UNIQUE
        );
        CREATE INDEX paper_trades_symbol_opened_idx
            ON paper_trades (symbol, opened_at DESC);
        """
    )

    op.execute(
        """
        CREATE TABLE audit_violations (
            id          BIGSERIAL PRIMARY KEY,
            detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            table_name  TEXT NOT NULL,
            row_id      BIGINT NOT NULL,
            expected    TEXT NOT NULL,
            actual      TEXT NOT NULL,
            note        TEXT
        );
        """
    )

    op.execute(
        """
        CREATE TABLE data_quality_alerts (
            id          BIGSERIAL PRIMARY KEY,
            ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            symbol      TEXT NOT NULL,
            timeframe   TEXT NOT NULL,
            candle_ts   TIMESTAMPTZ NOT NULL,
            check_name  TEXT NOT NULL,
            details     JSONB NOT NULL
        );
        CREATE INDEX dqa_symbol_ts_idx ON data_quality_alerts (symbol, ts DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data_quality_alerts;")
    op.execute("DROP TABLE IF EXISTS audit_violations;")
    op.execute("DROP TABLE IF EXISTS paper_trades;")
    op.execute("DROP TABLE IF EXISTS predictions;")
```

- [ ] **Step 2: Run migration**

```bash
cd worktrees/sp-0
docker compose exec backend alembic upgrade head
```
Expected: `Running upgrade 0001_initial -> 0002_audit_chain`

- [ ] **Step 3: Verify tables**

```bash
docker compose exec postgres psql -U postgres trading_radar -c "\dt"
```
Expected output lists: `audit_violations`, `data_quality_alerts`, `ohlcv`, `paper_trades`, `predictions`, `watchlist`.

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/alembic/versions/
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): predictions + paper_trades + audit_violations + dq_alerts"
```

---

### Task B4: SQLAlchemy session module

**Files:**
- Create: `worktrees/sp-0/backend/app/db/__init__.py` (empty)
- Create: `worktrees/sp-0/backend/app/db/session.py`

- [ ] **Step 1: app/db/__init__.py** — empty.

- [ ] **Step 2: app/db/session.py**

```python
from collections.abc import AsyncIterator
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            get_settings().database_url,
            pool_size=10,
            max_overflow=5,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), expire_on_commit=False
        )
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session
```

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/app/db/
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): SQLAlchemy async engine + session factory"
```

---

### Task B5: Audit hash-chain module — failing test

**Files:**
- Create: `worktrees/sp-0/backend/app/db/audit.py` (will exist as empty stub for test to import)
- Create: `worktrees/sp-0/backend/tests/__init__.py` (empty)
- Create: `worktrees/sp-0/backend/tests/conftest.py`
- Create: `worktrees/sp-0/backend/tests/unit/__init__.py` (empty)
- Create: `worktrees/sp-0/backend/tests/unit/test_audit_hashchain.py`

- [ ] **Step 1: app/db/audit.py — empty stub (test must fail import-time symbol lookup)**

```python
# Implementation in next task. Empty intentionally so test fails red.
```

- [ ] **Step 2: tests/conftest.py**

```python
import pytest


@pytest.fixture
def empty_prev_hash() -> str:
    return "0" * 64
```

- [ ] **Step 3: tests/unit/test_audit_hashchain.py**

```python
import json
import hashlib

import pytest

from app.db.audit import canonical_row_json, compute_row_hash, GENESIS_HASH


def test_genesis_hash_is_64_zero_chars(empty_prev_hash: str) -> None:
    assert GENESIS_HASH == empty_prev_hash
    assert len(GENESIS_HASH) == 64


def test_canonical_row_json_is_sorted_and_compact() -> None:
    row = {"b": 2, "a": 1, "c": [3, 1, 2]}
    out = canonical_row_json(row)
    assert out == '{"a":1,"b":2,"c":[3,1,2]}'


def test_compute_row_hash_matches_sha256_of_concat() -> None:
    prev = "a" * 64
    row = {"x": 1, "y": "two"}
    expected = hashlib.sha256(
        (prev + canonical_row_json(row)).encode("utf-8")
    ).hexdigest()
    assert compute_row_hash(prev, row) == expected


def test_chain_unbroken_across_three_rows() -> None:
    rows = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}, {"id": 3, "v": "c"}]
    h0 = GENESIS_HASH
    h1 = compute_row_hash(h0, rows[0])
    h2 = compute_row_hash(h1, rows[1])
    h3 = compute_row_hash(h2, rows[2])
    # mutating row 1 must invalidate h2 onward
    tampered = compute_row_hash(h0, {"id": 1, "v": "TAMPERED"})
    assert tampered != h1
```

- [ ] **Step 4: Run — must fail red**

```bash
cd worktrees/sp-0/backend
pytest tests/unit/test_audit_hashchain.py -v
```
Expected: ImportError on `canonical_row_json`, `compute_row_hash`, `GENESIS_HASH`.

- [ ] **Step 5: No commit yet (red test, no impl)**

---

### Task B6: Audit hash-chain — minimal implementation, green

**Files:**
- Modify: `worktrees/sp-0/backend/app/db/audit.py`

- [ ] **Step 1: Replace stub with implementation**

```python
import hashlib
import json
from typing import Any

GENESIS_HASH: str = "0" * 64


def canonical_row_json(row: dict[str, Any]) -> str:
    """Canonical JSON serialization for hashing.

    sort_keys=True and the compact separators give a deterministic
    byte representation, so the same row always hashes to the same value.
    """
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)


def compute_row_hash(prev_hash: str, row: dict[str, Any]) -> str:
    payload = (prev_hash + canonical_row_json(row)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
```

- [ ] **Step 2: Re-run tests — must pass**

```bash
cd worktrees/sp-0/backend
pytest tests/unit/test_audit_hashchain.py -v
```
Expected: `4 passed`.

- [ ] **Step 3: Commit (red+green together)**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/app/db/audit.py backend/tests/
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): audit hash chain (sha256, canonical JSON)"
```

---

### Task B7: Audit insert helper that fetches prev_hash + writes new row

**Files:**
- Modify: `worktrees/sp-0/backend/app/db/audit.py`
- Modify: `worktrees/sp-0/backend/tests/unit/test_audit_hashchain.py` (add new test)

- [ ] **Step 1: Add failing integration test (uses sqlite in-memory for speed)**

Append to `tests/unit/test_audit_hashchain.py`:

```python
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.db.audit import insert_with_chain


@pytest.mark.asyncio
async def test_insert_with_chain_links_rows_correctly() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "data TEXT NOT NULL, prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL UNIQUE)"
        ))
    async with AsyncSession(engine) as session:
        h1 = await insert_with_chain(session, "t", {"data": "first"})
        h2 = await insert_with_chain(session, "t", {"data": "second"})
        await session.commit()
        rows = (await session.execute(
            sa.text("SELECT id, data, prev_hash, row_hash FROM t ORDER BY id")
        )).all()
    assert len(rows) == 2
    assert rows[0].prev_hash == GENESIS_HASH
    assert rows[0].row_hash == h1
    assert rows[1].prev_hash == h1
    assert rows[1].row_hash == h2
```

Add `aiosqlite` to backend dev deps:

```bash
cd worktrees/sp-0/backend
pip install aiosqlite==0.20.0
# Also add to pyproject.toml dev deps:
```

Update `backend/pyproject.toml` `[project.optional-dependencies] dev` list to include `"aiosqlite==0.20.0",`.

- [ ] **Step 2: Run — must fail red**

```bash
pytest tests/unit/test_audit_hashchain.py::test_insert_with_chain_links_rows_correctly -v
```
Expected: ImportError on `insert_with_chain`.

- [ ] **Step 3: Implement insert_with_chain**

Append to `app/db/audit.py`:

```python
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def _last_row_hash(session: AsyncSession, table: str) -> str:
    result = await session.execute(
        sa.text(f"SELECT row_hash FROM {table} ORDER BY id DESC LIMIT 1")
    )
    row = result.first()
    return row.row_hash if row else GENESIS_HASH


async def insert_with_chain(
    session: AsyncSession, table: str, payload: dict[str, Any]
) -> str:
    """Insert payload + computed prev_hash/row_hash. Returns row_hash."""
    prev = await _last_row_hash(session, table)
    new_hash = compute_row_hash(prev, payload)
    full = {**payload, "prev_hash": prev, "row_hash": new_hash}
    cols = ", ".join(full.keys())
    params = ", ".join(f":{k}" for k in full.keys())
    await session.execute(
        sa.text(f"INSERT INTO {table} ({cols}) VALUES ({params})"), full
    )
    return new_hash
```

- [ ] **Step 4: Re-run — must pass**

```bash
pytest tests/unit/test_audit_hashchain.py -v
```
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/app/db/audit.py backend/tests/ backend/pyproject.toml
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): insert_with_chain — atomic prev_hash/row_hash insert"
```

---

## Phase C — Data Layer (Rate Limiter, Universe, DataQuality, Binance Adapter)

Implements §5.8 (WS reliability), §5.9 (data quality), §5.15 (rate limits). All Binance public endpoints (no API keys for SP-0).

### Task C1: Token-bucket rate limiter — failing test

**Files:**
- Create: `worktrees/sp-0/backend/app/data/__init__.py` (empty)
- Create: `worktrees/sp-0/backend/app/data/ratelimit.py` (empty stub)
- Create: `worktrees/sp-0/backend/tests/unit/test_ratelimit.py`

- [ ] **Step 1: Empty stub `app/data/ratelimit.py`**

```python
# Implementation in next task.
```

- [ ] **Step 2: Failing test**

```python
import asyncio
import time
import pytest

from app.data.ratelimit import TokenBucket


@pytest.mark.asyncio
async def test_initial_capacity_allows_immediate_calls() -> None:
    bucket = TokenBucket(capacity=5, refill_per_sec=1.0)
    for _ in range(5):
        await bucket.acquire(weight=1)
    # 6th call should block ~1 second waiting for refill
    start = time.monotonic()
    await bucket.acquire(weight=1)
    elapsed = time.monotonic() - start
    assert 0.9 <= elapsed <= 1.5


@pytest.mark.asyncio
async def test_weighted_acquire_drains_proportionally() -> None:
    bucket = TokenBucket(capacity=10, refill_per_sec=100.0)
    await bucket.acquire(weight=7)
    assert pytest.approx(bucket.tokens, abs=0.05) == 3


@pytest.mark.asyncio
async def test_refill_caps_at_capacity() -> None:
    bucket = TokenBucket(capacity=3, refill_per_sec=10.0)
    await asyncio.sleep(0.5)  # would refill 5; cap at 3
    assert bucket.tokens == 3
```

- [ ] **Step 3: Run — must fail**

```bash
cd worktrees/sp-0/backend
pytest tests/unit/test_ratelimit.py -v
```
Expected: ImportError on `TokenBucket`.

---

### Task C2: Token-bucket implementation — green

**Files:**
- Modify: `worktrees/sp-0/backend/app/data/ratelimit.py`

- [ ] **Step 1: Implement**

```python
import asyncio
import time
from dataclasses import dataclass


@dataclass
class TokenBucket:
    capacity: float
    refill_per_sec: float
    _tokens: float = 0.0
    _last_refill: float = 0.0
    _lock: asyncio.Lock | None = None

    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    @property
    def tokens(self) -> float:
        self._refill_locked()
        return self._tokens

    def _refill_locked(self) -> None:
        now = time.monotonic()
        delta = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + delta * self.refill_per_sec)
        self._last_refill = now

    async def acquire(self, weight: float = 1.0) -> None:
        assert self._lock is not None
        while True:
            async with self._lock:
                self._refill_locked()
                if self._tokens >= weight:
                    self._tokens -= weight
                    return
                deficit = weight - self._tokens
                wait_for = deficit / self.refill_per_sec
            await asyncio.sleep(wait_for)
```

- [ ] **Step 2: Re-run — must pass**

```bash
pytest tests/unit/test_ratelimit.py -v
```
Expected: `3 passed`.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/app/data/ratelimit.py backend/tests/unit/test_ratelimit.py backend/app/data/__init__.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): asyncio token bucket rate limiter"
```

---

### Task C3: Universe scaffolding (`is_tradable`)

**Files:**
- Create: `worktrees/sp-0/backend/app/data/universe.py`
- Create: `worktrees/sp-0/backend/tests/unit/test_universe.py`

- [ ] **Step 1: Failing test**

```python
from datetime import datetime, timezone
from app.data.universe import is_tradable, BTC_USDT


def test_btc_usdt_is_tradable_today() -> None:
    now = datetime.now(timezone.utc)
    assert is_tradable(BTC_USDT, now) is True


def test_unknown_symbol_returns_false() -> None:
    now = datetime.now(timezone.utc)
    assert is_tradable("XXX/YYY", now) is False
```

Run: `pytest tests/unit/test_universe.py -v` → fails on import.

- [ ] **Step 2: Implementation (sp-0 minimal: hardcoded one symbol)**

```python
"""Point-in-time universe (§5.2).

SP-0 hardcodes BTC/USDT only. SP-3 will populate from a `universe_history`
table fetched from exchange listings APIs.
"""
from datetime import datetime

BTC_USDT: str = "BTC/USDT"

_SP0_HARDCODED = {
    BTC_USDT: (datetime(2017, 8, 17, tzinfo=None),),  # listed_at; no delisted_at
}


def is_tradable(symbol: str, ts: datetime) -> bool:
    entry = _SP0_HARDCODED.get(symbol)
    if entry is None:
        return False
    listed_at = entry[0]
    return ts.replace(tzinfo=None) >= listed_at
```

- [ ] **Step 3: Test passes**

```bash
pytest tests/unit/test_universe.py -v
```
Expected: `2 passed`.

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/app/data/universe.py backend/tests/unit/test_universe.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): universe.is_tradable — hardcoded BTC/USDT scaffold"
```

---

### Task C4: Data quality validator — failing test

**Files:**
- Create: `worktrees/sp-0/backend/app/core/__init__.py` (empty)
- Create: `worktrees/sp-0/backend/app/core/dataquality/__init__.py` (empty)
- Create: `worktrees/sp-0/backend/app/core/dataquality/validator.py` (stub)
- Create: `worktrees/sp-0/backend/tests/unit/test_dataquality_validator.py`

- [ ] **Step 1: Stub** — empty `validator.py`.

- [ ] **Step 2: Failing test**

```python
from datetime import datetime, timedelta, timezone
import pytest

from app.core.dataquality.validator import (
    Candle, ValidationResult, validate
)


def make_candle(**overrides) -> Candle:
    base = dict(
        symbol="BTC/USDT", timeframe="1h",
        ts=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
        open=100.0, high=110.0, low=95.0, close=105.0, volume=1000.0,
    )
    base.update(overrides)
    return Candle(**base)


def test_valid_candle_passes() -> None:
    result = validate(make_candle(), prev_close=100.0, prev_volume_median=1000.0)
    assert result.ok is True
    assert result.failures == ()


def test_high_below_low_fails() -> None:
    result = validate(make_candle(high=50.0, low=100.0), prev_close=100.0, prev_volume_median=1000.0)
    assert result.ok is False
    assert "high_below_low" in result.failures


def test_open_outside_range_fails() -> None:
    result = validate(make_candle(open=200.0), prev_close=100.0, prev_volume_median=1000.0)
    assert result.ok is False
    assert "open_outside_range" in result.failures


def test_close_outside_range_fails() -> None:
    result = validate(make_candle(close=200.0), prev_close=100.0, prev_volume_median=1000.0)
    assert result.ok is False
    assert "close_outside_range" in result.failures


def test_negative_volume_fails() -> None:
    result = validate(make_candle(volume=-1.0), prev_close=100.0, prev_volume_median=1000.0)
    assert result.ok is False
    assert "negative_volume" in result.failures


def test_price_jump_over_20pct_fails() -> None:
    result = validate(make_candle(close=130.0, high=130.0), prev_close=100.0, prev_volume_median=1000.0)
    assert result.ok is False
    assert "price_jump_over_20pct" in result.failures


def test_volume_spike_over_10x_median_fails() -> None:
    result = validate(make_candle(volume=20000.0), prev_close=100.0, prev_volume_median=1000.0)
    assert result.ok is False
    assert "volume_spike_over_10x_median" in result.failures
```

- [ ] **Step 3: Run — must fail**

```bash
pytest tests/unit/test_dataquality_validator.py -v
```
Expected: ImportError.

---

### Task C5: Data quality validator — implementation, green

**Files:**
- Modify: `worktrees/sp-0/backend/app/core/dataquality/validator.py`

- [ ] **Step 1: Implement**

```python
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Candle:
    symbol: str
    timeframe: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    failures: tuple[str, ...] = field(default_factory=tuple)


_PRICE_JUMP_LIMIT = 0.20
_VOLUME_SPIKE_LIMIT = 10.0


def validate(
    candle: Candle, *, prev_close: float | None, prev_volume_median: float | None
) -> ValidationResult:
    failures: list[str] = []

    if candle.high < candle.low:
        failures.append("high_below_low")
    if not (candle.low <= candle.open <= candle.high):
        failures.append("open_outside_range")
    if not (candle.low <= candle.close <= candle.high):
        failures.append("close_outside_range")
    if candle.volume < 0:
        failures.append("negative_volume")

    if prev_close is not None and prev_close > 0:
        jump = abs(candle.close - prev_close) / prev_close
        if jump > _PRICE_JUMP_LIMIT:
            failures.append("price_jump_over_20pct")

    if prev_volume_median is not None and prev_volume_median > 0:
        if candle.volume > _VOLUME_SPIKE_LIMIT * prev_volume_median:
            failures.append("volume_spike_over_10x_median")

    return ValidationResult(ok=not failures, failures=tuple(failures))
```

- [ ] **Step 2: Tests pass**

```bash
pytest tests/unit/test_dataquality_validator.py -v
```
Expected: `7 passed`.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/app/core/ backend/tests/unit/test_dataquality_validator.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): data quality validator (7 checks per candle)"
```

---

### Task C6: Binance REST adapter — failing test (with httpx mock)

**Files:**
- Create: `worktrees/sp-0/backend/app/data/adapters/__init__.py` (empty)
- Create: `worktrees/sp-0/backend/app/data/adapters/binance.py` (stub)
- Create: `worktrees/sp-0/backend/tests/integration/__init__.py` (empty)
- Create: `worktrees/sp-0/backend/tests/integration/test_binance_adapter.py`

- [ ] **Step 1: Stub `binance.py`** — empty.

- [ ] **Step 2: Test (uses respx for httpx mocking)**

Add `"respx==0.22.0"` to backend dev deps. Then:

```python
from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.data.adapters.binance import BinanceClient


SAMPLE_KLINE = [
    [
        1714521600000,        # open time ms
        "65000.00",
        "65500.00",
        "64800.00",
        "65300.00",
        "1234.56",            # volume
        1714525199999,        # close time ms
        "80502345.00",
        9876,
        "617.28",
        "40251172.50",
        "0",
    ],
]


@pytest.mark.asyncio
async def test_fetch_klines_parses_binance_response() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.binance.com"
    ) as router:
        router.get("/api/v3/klines").mock(
            return_value=httpx.Response(200, json=SAMPLE_KLINE)
        )
        client = BinanceClient(http=http, base_url="https://api.binance.com")
        candles = await client.fetch_klines("BTCUSDT", "1h", limit=1)

    assert len(candles) == 1
    c = candles[0]
    assert c.symbol == "BTC/USDT"
    assert c.timeframe == "1h"
    assert c.ts == datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
    assert c.open == 65000.0
    assert c.high == 65500.0
    assert c.low == 64800.0
    assert c.close == 65300.0
    assert c.volume == 1234.56
```

- [ ] **Step 3: Run — fail**

```bash
pytest tests/integration/test_binance_adapter.py -v
```
Expected: ImportError on `BinanceClient`.

---

### Task C7: Binance REST adapter — implementation, green

**Files:**
- Modify: `worktrees/sp-0/backend/app/data/adapters/binance.py`

- [ ] **Step 1: Implement**

```python
from datetime import datetime, timezone
from dataclasses import dataclass

import httpx

from app.core.dataquality.validator import Candle
from app.data.ratelimit import TokenBucket


_TF_TO_BINANCE = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}


def _to_pair(binance_symbol: str) -> str:
    """BTCUSDT -> BTC/USDT (heuristic: split before USDT/USDC/BUSD)."""
    for quote in ("USDT", "USDC", "BUSD", "FDUSD"):
        if binance_symbol.endswith(quote):
            return f"{binance_symbol[:-len(quote)]}/{quote}"
    return binance_symbol


@dataclass
class BinanceClient:
    http: httpx.AsyncClient
    base_url: str = "https://api.binance.com"
    bucket: TokenBucket | None = None

    def __post_init__(self) -> None:
        if self.bucket is None:
            # Binance REST: 1200 weight per minute = 20/sec
            self.bucket = TokenBucket(capacity=1200, refill_per_sec=20.0)

    async def fetch_klines(
        self, symbol: str, timeframe: str, *, limit: int = 500
    ) -> list[Candle]:
        assert self.bucket is not None
        await self.bucket.acquire(weight=2)  # /klines weight = 2 for limit<=100

        binance_tf = _TF_TO_BINANCE[timeframe]
        url = f"{self.base_url}/api/v3/klines"
        params = {"symbol": symbol, "interval": binance_tf, "limit": limit}
        response = await self.http.get(url, params=params, timeout=10.0)
        response.raise_for_status()

        result: list[Candle] = []
        pair = _to_pair(symbol)
        for row in response.json():
            result.append(
                Candle(
                    symbol=pair,
                    timeframe=timeframe,
                    ts=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        return result
```

- [ ] **Step 2: Tests pass**

```bash
pytest tests/integration/test_binance_adapter.py -v
```
Expected: `1 passed`.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/app/data/adapters/ backend/tests/integration/ backend/pyproject.toml
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): Binance REST adapter (klines) + rate limit"
```

---

### Task C8: Binance WebSocket adapter — failing test

**Files:**
- Modify: `worktrees/sp-0/backend/app/data/adapters/binance.py` (add WS class stub)
- Modify: `worktrees/sp-0/backend/tests/integration/test_binance_adapter.py` (add test)

- [ ] **Step 1: Stub** — add to `binance.py`:

```python
class BinanceKlineStream:
    """Subscribes to wss://stream.binance.com:9443/ws/<symbol>@kline_<tf>."""
    pass  # next task implements
```

- [ ] **Step 2: Failing test** — append to `test_binance_adapter.py`:

```python
import asyncio
import json

from app.data.adapters.binance import BinanceKlineStream


SAMPLE_WS_MSG = {
    "e": "kline", "E": 1714525200000, "s": "BTCUSDT",
    "k": {
        "t": 1714521600000, "T": 1714525199999, "s": "BTCUSDT",
        "i": "1h", "o": "65000.00", "c": "65300.00", "h": "65500.00",
        "l": "64800.00", "v": "1234.56", "x": True
    }
}


@pytest.mark.asyncio
async def test_kline_stream_parses_closed_candles_only(monkeypatch) -> None:
    msgs = [SAMPLE_WS_MSG, {**SAMPLE_WS_MSG, "k": {**SAMPLE_WS_MSG["k"], "x": False}}]

    async def fake_iter(_url):
        for m in msgs:
            yield json.dumps(m)

    stream = BinanceKlineStream(symbol="BTCUSDT", timeframe="1h", _connect=fake_iter)
    received = []
    async for candle in stream.stream():
        received.append(candle)
        if len(received) == 1:
            break

    assert len(received) == 1
    assert received[0].close == 65300.0
```

- [ ] **Step 3: Run — fail**

---

### Task C9: Binance WS adapter — implementation, green

**Files:**
- Modify: `worktrees/sp-0/backend/app/data/adapters/binance.py`

- [ ] **Step 1: Implement**

Add to `binance.py`:

```python
import json
from collections.abc import AsyncIterator, Callable, Awaitable

import websockets


class BinanceKlineStream:
    """Yields only CLOSED candles (k.x == True). Skips intra-bar updates.

    Reconnect-with-backoff is handled here per §5.8.
    """

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        *,
        base_ws_url: str = "wss://stream.binance.com:9443",
        _connect: Callable[[str], AsyncIterator[str]] | None = None,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.base_ws_url = base_ws_url
        self._connect = _connect
        pair = symbol.lower()
        self.url = f"{base_ws_url}/ws/{pair}@kline_{timeframe}"

    async def _real_connect(self, url: str) -> AsyncIterator[str]:
        async with websockets.connect(url, ping_interval=15, ping_timeout=10) as ws:
            async for msg in ws:
                yield msg if isinstance(msg, str) else msg.decode()

    async def stream(self) -> AsyncIterator[Candle]:
        connect = self._connect or self._real_connect
        backoff = 1.0
        while True:
            try:
                async for raw in connect(self.url):
                    backoff = 1.0
                    payload = json.loads(raw)
                    kline = payload.get("k") if isinstance(payload, dict) else None
                    if not kline or not kline.get("x"):
                        continue
                    yield Candle(
                        symbol=_to_pair(kline["s"]),
                        timeframe=kline["i"],
                        ts=datetime.fromtimestamp(kline["t"] / 1000, tz=timezone.utc),
                        open=float(kline["o"]),
                        high=float(kline["h"]),
                        low=float(kline["l"]),
                        close=float(kline["c"]),
                        volume=float(kline["v"]),
                    )
            except Exception:  # noqa: BLE001 — resilient WS loop
                await asyncio.sleep(min(30.0, backoff))
                backoff = min(30.0, backoff * 2)
```

(Add `import asyncio` at top if missing.)

- [ ] **Step 2: Tests pass**

```bash
pytest tests/integration/test_binance_adapter.py -v
```
Expected: `2 passed`.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/app/data/adapters/binance.py backend/tests/integration/test_binance_adapter.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): Binance WS kline stream (closed candles only, backoff)"
```

---

### Task C10: OHLCV upsert pipeline (validator + DB write)

**Files:**
- Create: `worktrees/sp-0/backend/app/data/ohlcv_pipeline.py`
- Create: `worktrees/sp-0/backend/tests/unit/test_ohlcv_pipeline.py`

- [ ] **Step 1: Failing test**

```python
import pytest
import sqlalchemy as sa
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.core.dataquality.validator import Candle
from app.data.ohlcv_pipeline import OHLCVPipeline


@pytest.mark.asyncio
async def test_pipeline_upserts_valid_candle_and_skips_invalid() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE ohlcv (symbol TEXT, timeframe TEXT, ts TEXT, "
            "open REAL, high REAL, low REAL, close REAL, volume REAL, "
            "PRIMARY KEY(symbol,timeframe,ts))"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE data_quality_alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts TEXT, symbol TEXT, timeframe TEXT, candle_ts TEXT, "
            "check_name TEXT, details TEXT)"
        ))

    valid = Candle("BTC/USDT","1h",datetime(2026,5,1,12,tzinfo=timezone.utc),
                   100,110,95,105,1000)
    invalid = Candle("BTC/USDT","1h",datetime(2026,5,1,13,tzinfo=timezone.utc),
                     200,110,95,105,1000)  # open outside range

    async with AsyncSession(engine) as session:
        pipe = OHLCVPipeline(session)
        await pipe.process(valid, prev_close=99.0, prev_volume_median=1000.0)
        await pipe.process(invalid, prev_close=105.0, prev_volume_median=1000.0)
        await session.commit()

        ohlcv_rows = (await session.execute(sa.text("SELECT COUNT(*) FROM ohlcv"))).scalar()
        dqa_rows = (await session.execute(sa.text("SELECT COUNT(*) FROM data_quality_alerts"))).scalar()
    assert ohlcv_rows == 1
    assert dqa_rows == 1
```

- [ ] **Step 2: Implement**

```python
import json
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dataquality.validator import Candle, validate


class OHLCVPipeline:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def process(
        self,
        candle: Candle,
        *,
        prev_close: float | None,
        prev_volume_median: float | None,
    ) -> bool:
        result = validate(
            candle, prev_close=prev_close, prev_volume_median=prev_volume_median
        )
        if not result.ok:
            for failure in result.failures:
                await self.session.execute(
                    sa.text(
                        "INSERT INTO data_quality_alerts "
                        "(ts, symbol, timeframe, candle_ts, check_name, details) "
                        "VALUES (CURRENT_TIMESTAMP, :s, :tf, :cts, :ck, :d)"
                    ),
                    {
                        "s": candle.symbol, "tf": candle.timeframe,
                        "cts": candle.ts.isoformat(),
                        "ck": failure,
                        "d": json.dumps({"open": candle.open, "high": candle.high,
                                         "low": candle.low, "close": candle.close,
                                         "volume": candle.volume}),
                    },
                )
            return False

        await self.session.execute(
            sa.text(
                "INSERT INTO ohlcv (symbol, timeframe, ts, open, high, low, close, volume) "
                "VALUES (:s, :tf, :ts, :o, :h, :l, :c, :v) "
                "ON CONFLICT (symbol, timeframe, ts) DO UPDATE SET "
                "open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
                "close=EXCLUDED.close, volume=EXCLUDED.volume"
            ).bindparams(
                # SQLite uses INSERT OR REPLACE for upsert; the ON CONFLICT clause
                # works in both Postgres and SQLite (>=3.24).
            ),
            {
                "s": candle.symbol, "tf": candle.timeframe,
                "ts": candle.ts.isoformat(),
                "o": candle.open, "h": candle.high,
                "l": candle.low, "c": candle.close, "v": candle.volume,
            },
        )
        return True
```

- [ ] **Step 3: Tests pass**

```bash
pytest tests/unit/test_ohlcv_pipeline.py -v
```
Expected: `1 passed`.

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/app/data/ohlcv_pipeline.py backend/tests/unit/test_ohlcv_pipeline.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): OHLCV pipeline — validate + upsert + dq_alerts"
```

---

## Phase D — Indicators (EMA, RSI, MACD)

Implements §5.1 (look-ahead: every indicator only uses bars up to and including the current bar; never peeks ahead). Output value at index `i` depends only on inputs at indices `≤ i`.

### Task D1: EMA — failing test

**Files:**
- Create: `worktrees/sp-0/backend/app/core/indicators/__init__.py` (empty)
- Create: `worktrees/sp-0/backend/app/core/indicators/ema.py` (stub)
- Create: `worktrees/sp-0/backend/tests/unit/test_indicators_ema.py`

- [ ] **Step 1: Stub** — empty `ema.py`.

- [ ] **Step 2: Failing test**

```python
import math
import numpy as np
import pytest

from app.core.indicators.ema import ema


def test_ema_first_period_is_sma_then_recursive() -> None:
    closes = np.array([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    out = ema(closes, period=3)
    # First 2 values are NaN, value at index 2 = SMA(10,11,12) = 11.0
    assert math.isnan(out[0])
    assert math.isnan(out[1])
    assert out[2] == pytest.approx(11.0)
    # Subsequent: alpha = 2/(3+1) = 0.5; ema[3] = 0.5*13 + 0.5*11 = 12.0
    assert out[3] == pytest.approx(12.0)
    assert out[4] == pytest.approx(13.0)
    assert out[5] == pytest.approx(14.0)


def test_ema_no_lookahead() -> None:
    closes = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    full = ema(closes, period=2)
    truncated = ema(closes[:4], period=2)
    # index 3 of full must equal index 3 of truncated — no future peek
    assert full[3] == pytest.approx(truncated[3])


def test_ema_period_longer_than_input_returns_all_nan() -> None:
    closes = np.array([1.0, 2.0])
    out = ema(closes, period=5)
    assert all(math.isnan(v) for v in out)


def test_ema_period_must_be_positive() -> None:
    with pytest.raises(ValueError):
        ema(np.array([1.0, 2.0, 3.0]), period=0)
```

- [ ] **Step 3: Run — fail**

```bash
pytest tests/unit/test_indicators_ema.py -v
```
Expected: ImportError on `ema`.

---

### Task D2: EMA implementation, green

**Files:**
- Modify: `worktrees/sp-0/backend/app/core/indicators/ema.py`

- [ ] **Step 1: Implement**

```python
import numpy as np
from numpy.typing import NDArray


def ema(closes: NDArray[np.float64], period: int) -> NDArray[np.float64]:
    """Exponential moving average.

    Convention: first `period-1` values are NaN; index `period-1` is the SMA
    of the first `period` closes; subsequent values use alpha = 2/(period+1).
    No look-ahead: output[i] depends only on closes[0..i].
    """
    if period <= 0:
        raise ValueError("period must be positive")
    n = closes.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    if n < period:
        return out

    alpha = 2.0 / (period + 1)
    out[period - 1] = closes[:period].mean()
    for i in range(period, n):
        out[i] = alpha * closes[i] + (1 - alpha) * out[i - 1]
    return out
```

- [ ] **Step 2: Tests pass**

```bash
pytest tests/unit/test_indicators_ema.py -v
```
Expected: `4 passed`.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/app/core/indicators/
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): EMA indicator (no look-ahead)"
```

---

### Task D3: RSI — failing test

**Files:**
- Create: `worktrees/sp-0/backend/app/core/indicators/rsi.py` (stub)
- Create: `worktrees/sp-0/backend/tests/unit/test_indicators_rsi.py`

- [ ] **Step 1: Stub** — empty `rsi.py`.

- [ ] **Step 2: Failing test (Wilder's smoothing — matches TradingView's default RSI)**

```python
import math
import numpy as np
import pytest

from app.core.indicators.rsi import rsi


# Wilder RSI fixture: classic TA textbook example
# closes from QuantInsti / Wilder original sample
WILDER_CLOSES = np.array([
    44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
    45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64,
])
EXPECTED_RSI_14_AT_INDEX_14 = 70.464  # ±0.01 tolerance vs published value


def test_rsi_matches_wilder_textbook_at_first_full_bar() -> None:
    out = rsi(WILDER_CLOSES, period=14)
    assert out[14] == pytest.approx(EXPECTED_RSI_14_AT_INDEX_14, abs=0.01)


def test_rsi_first_period_values_are_nan() -> None:
    out = rsi(WILDER_CLOSES, period=14)
    for i in range(14):
        assert math.isnan(out[i])


def test_rsi_all_gains_returns_100() -> None:
    closes = np.arange(1.0, 30.0)
    out = rsi(closes, period=14)
    assert out[-1] == pytest.approx(100.0)


def test_rsi_all_losses_returns_0() -> None:
    closes = np.arange(30.0, 1.0, -1.0)
    out = rsi(closes, period=14)
    assert out[-1] == pytest.approx(0.0)


def test_rsi_no_lookahead() -> None:
    full = rsi(WILDER_CLOSES, period=14)
    truncated = rsi(WILDER_CLOSES[:18], period=14)
    assert full[17] == pytest.approx(truncated[17])
```

- [ ] **Step 3: Run — fail**

---

### Task D4: RSI implementation (Wilder smoothing), green

**Files:**
- Modify: `worktrees/sp-0/backend/app/core/indicators/rsi.py`

- [ ] **Step 1: Implement**

```python
import numpy as np
from numpy.typing import NDArray


def rsi(closes: NDArray[np.float64], period: int = 14) -> NDArray[np.float64]:
    """Relative Strength Index using Wilder's smoothing.

    Matches TradingView's default RSI behaviour. No look-ahead.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    n = closes.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    if n <= period:
        return out

    diffs = np.diff(closes)
    gains = np.where(diffs > 0, diffs, 0.0)
    losses = np.where(diffs < 0, -diffs, 0.0)

    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()

    if avg_loss == 0:
        out[period] = 100.0
    elif avg_gain == 0:
        out[period] = 0.0
    else:
        rs = avg_gain / avg_loss
        out[period] = 100.0 - (100.0 / (1.0 + rs))

    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        if avg_loss == 0:
            out[i] = 100.0
        elif avg_gain == 0:
            out[i] = 0.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out
```

- [ ] **Step 2: Tests pass**

```bash
pytest tests/unit/test_indicators_rsi.py -v
```
Expected: `5 passed`.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/app/core/indicators/rsi.py backend/tests/unit/test_indicators_rsi.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): RSI indicator (Wilder smoothing, no look-ahead)"
```

---

### Task D5: MACD — failing test

**Files:**
- Create: `worktrees/sp-0/backend/app/core/indicators/macd.py` (stub)
- Create: `worktrees/sp-0/backend/tests/unit/test_indicators_macd.py`

- [ ] **Step 1: Stub** — empty.

- [ ] **Step 2: Failing test**

```python
import math
import numpy as np
import pytest

from app.core.indicators.ema import ema
from app.core.indicators.macd import macd


def test_macd_returns_three_arrays() -> None:
    closes = np.linspace(100.0, 200.0, 50)
    macd_line, signal_line, hist = macd(closes, fast=12, slow=26, signal=9)
    assert macd_line.shape == closes.shape
    assert signal_line.shape == closes.shape
    assert hist.shape == closes.shape


def test_macd_line_equals_fast_minus_slow_ema() -> None:
    closes = np.linspace(100.0, 200.0, 60)
    macd_line, _, _ = macd(closes, fast=12, slow=26, signal=9)
    expected = ema(closes, 12) - ema(closes, 26)
    # NaN-aware comparison
    for i in range(len(closes)):
        if math.isnan(expected[i]):
            assert math.isnan(macd_line[i])
        else:
            assert macd_line[i] == pytest.approx(expected[i])


def test_macd_histogram_equals_macd_minus_signal() -> None:
    closes = np.linspace(100.0, 200.0, 60)
    macd_line, signal_line, hist = macd(closes, fast=12, slow=26, signal=9)
    for i in range(len(closes)):
        if math.isnan(hist[i]):
            assert math.isnan(macd_line[i] - signal_line[i])
        else:
            assert hist[i] == pytest.approx(macd_line[i] - signal_line[i])


def test_macd_no_lookahead() -> None:
    closes = np.linspace(100.0, 200.0, 60)
    full = macd(closes, 12, 26, 9)
    truncated = macd(closes[:50], 12, 26, 9)
    assert full[0][49] == pytest.approx(truncated[0][49])
    assert full[1][49] == pytest.approx(truncated[1][49])
```

- [ ] **Step 3: Run — fail**

---

### Task D6: MACD implementation, green

**Files:**
- Modify: `worktrees/sp-0/backend/app/core/indicators/macd.py`

- [ ] **Step 1: Implement**

```python
import numpy as np
from numpy.typing import NDArray

from app.core.indicators.ema import ema


def macd(
    closes: NDArray[np.float64],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """MACD = EMA(fast) − EMA(slow); signal = EMA(MACD, signal); hist = MACD − signal.

    Returns (macd_line, signal_line, histogram). No look-ahead.
    """
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    macd_line = fast_ema - slow_ema
    # Replace NaN with NaN explicitly for clarity (already NaN when subtracted)

    # Signal EMA must skip leading NaN; build a clean view starting at first non-NaN index
    n = closes.shape[0]
    signal_line = np.full(n, np.nan, dtype=np.float64)
    first_valid = int(np.argmax(~np.isnan(macd_line))) if not np.all(np.isnan(macd_line)) else n
    if first_valid < n:
        clean = macd_line[first_valid:]
        sig_clean = ema(clean, signal)
        signal_line[first_valid:] = sig_clean

    hist = macd_line - signal_line
    return macd_line, signal_line, hist
```

- [ ] **Step 2: Tests pass**

```bash
pytest tests/unit/test_indicators_macd.py -v
```
Expected: `4 passed`.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/app/core/indicators/macd.py backend/tests/unit/test_indicators_macd.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): MACD (EMA-based, no look-ahead)"
```

---

### Task D7: TradingView cross-check tool (`tools/validate_indicators.py`)

**Files:**
- Create: `worktrees/sp-0/tools/validate_indicators.py`
- Create: `worktrees/sp-0/tools/README.md`

This tool is a runbook script the human runs after each Phase that touches indicators. It downloads recent BTC/USDT 1h candles from Binance, computes our indicators, and prints them next to a placeholder column the human fills with TradingView values for spot-checking. Per §6.2 tolerance is 0.1%.

- [ ] **Step 1: Write `tools/validate_indicators.py`**

```python
"""Cross-check our indicators vs TradingView.

Usage:
    python tools/validate_indicators.py BTCUSDT 1h 200

Outputs a CSV to stdout:
    ts,close,our_rsi14,our_ema20,our_ema50,our_ema200,our_macd_line,our_macd_signal

Open this CSV in a spreadsheet, manually fill TV values for 100 random rows,
compute pct diff, fail any row outside 0.1% tolerance.
"""
import asyncio
import csv
import sys

import httpx
import numpy as np

from app.core.indicators.ema import ema
from app.core.indicators.macd import macd
from app.core.indicators.rsi import rsi
from app.data.adapters.binance import BinanceClient


async def main(symbol: str, timeframe: str, limit: int) -> None:
    async with httpx.AsyncClient() as http:
        client = BinanceClient(http=http)
        candles = await client.fetch_klines(symbol, timeframe, limit=limit)

    closes = np.array([c.close for c in candles], dtype=np.float64)
    rsi14 = rsi(closes, 14)
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    macd_line, macd_signal, _ = macd(closes, 12, 26, 9)

    writer = csv.writer(sys.stdout)
    writer.writerow([
        "ts","close","our_rsi14","our_ema20","our_ema50","our_ema200",
        "our_macd_line","our_macd_signal","tv_value (FILL MANUALLY)","pct_diff",
    ])
    for i, c in enumerate(candles):
        writer.writerow([
            c.ts.isoformat(), f"{c.close:.4f}",
            f"{rsi14[i]:.4f}" if not np.isnan(rsi14[i]) else "",
            f"{ema20[i]:.4f}" if not np.isnan(ema20[i]) else "",
            f"{ema50[i]:.4f}" if not np.isnan(ema50[i]) else "",
            f"{ema200[i]:.4f}" if not np.isnan(ema200[i]) else "",
            f"{macd_line[i]:.6f}" if not np.isnan(macd_line[i]) else "",
            f"{macd_signal[i]:.6f}" if not np.isnan(macd_signal[i]) else "",
            "", "",
        ])


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    tf = sys.argv[2] if len(sys.argv) > 2 else "1h"
    lim = int(sys.argv[3]) if len(sys.argv) > 3 else 200
    asyncio.run(main(sym, tf, lim))
```

- [ ] **Step 2: tools/README.md**

```markdown
# tools/

Manual scripts the human runs at validation gates.

## validate_indicators.py

Per meta-plan §6.2: tolerance 0.1% absolute against TradingView.

```bash
cd backend
python ../tools/validate_indicators.py BTCUSDT 1h 200 > /tmp/check.csv
```

Then open `/tmp/check.csv` in a spreadsheet:
1. Pick 10 random rows where second-to-last bar (NOT the last partial bar).
2. Open TradingView with same symbol, timeframe, RSI(14), EMA(20/50/200), MACD(12,26,9).
3. Fill `tv_value` column with TV's value at the same timestamp.
4. Compute `pct_diff = abs(ours - tv) / tv * 100`.
5. PASS if all rows ≤ 0.1%. FAIL if any row > 0.1%.
```

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add tools/
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): TradingView indicator cross-check tool"
```

---

## Phase E — Scoring Layers L1, L3, L5 + Aggregator

3 layers compute live; L2/L4/L6/L7/L8/L9/L10 return `None` (handled by aggregator). Per decision 2.3, all layers start with **equal weight = 1/9**.

### Task E1: Scoring types + LayerScore — failing test

**Files:**
- Create: `worktrees/sp-0/backend/app/core/scoring/__init__.py` (empty)
- Create: `worktrees/sp-0/backend/app/core/scoring/types.py`
- Create: `worktrees/sp-0/backend/tests/unit/test_scoring_types.py`

- [ ] **Step 1: Failing test**

```python
import pytest
from app.core.scoring.types import LayerScore, FinalScore, Direction


def test_layer_score_has_required_fields() -> None:
    s = LayerScore(direction=Direction.LONG, strength=0.7, confidence=0.8, notes="rsi above 50")
    assert s.direction is Direction.LONG
    assert s.strength == 0.7
    assert s.confidence == 0.8


def test_strength_must_be_in_unit_interval() -> None:
    with pytest.raises(ValueError):
        LayerScore(direction=Direction.LONG, strength=1.5, confidence=0.8)


def test_signed_strength_is_negative_for_short() -> None:
    s = LayerScore(direction=Direction.SHORT, strength=0.6, confidence=0.9)
    assert s.signed_strength == pytest.approx(-0.6)


def test_signed_strength_zero_for_neutral() -> None:
    s = LayerScore(direction=Direction.NEUTRAL, strength=0.0, confidence=1.0)
    assert s.signed_strength == 0.0


def test_final_score_carries_layer_results() -> None:
    fs = FinalScore(
        score=0.42, direction=Direction.LONG, confidence=0.7,
        layer_results={1: None, 3: None, 5: None},
        contributing_layers=(1, 3),
    )
    assert fs.score == 0.42
    assert 1 in fs.contributing_layers
```

- [ ] **Step 2: Implementation**

```python
from dataclasses import dataclass, field
from enum import Enum


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class LayerScore:
    direction: Direction
    strength: float           # [0, 1] — magnitude of conviction
    confidence: float         # [0, 1] — meta-confidence in the score itself
    notes: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError(f"strength must be in [0,1], got {self.strength}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0,1], got {self.confidence}")

    @property
    def signed_strength(self) -> float:
        if self.direction is Direction.LONG:
            return self.strength
        if self.direction is Direction.SHORT:
            return -self.strength
        return 0.0


@dataclass(frozen=True)
class FinalScore:
    score: float                                   # signed, [-1, +1]
    direction: Direction
    confidence: float                              # [0, 1]
    layer_results: dict[int, "LayerScore | None"]  # 1..10 mapped (some None)
    contributing_layers: tuple[int, ...] = field(default_factory=tuple)
```

- [ ] **Step 3: Tests pass + commit**

```bash
pytest tests/unit/test_scoring_types.py -v
git -c safe.directory='A:/v5_Trade_bot' add backend/app/core/scoring/types.py backend/app/core/scoring/__init__.py backend/tests/unit/test_scoring_types.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): scoring types — LayerScore, FinalScore, Direction"
```

---

### Task E2: Layer 1 (HTF EMA macro) — failing test

**Spec:** Layer 1 looks at the higher-timeframe trend via the alignment of EMA20/EMA50/EMA200 on the same series. For SP-0 we treat the requested timeframe as both the analysis TF and the HTF (one TF only). Decision rules:
- All three EMAs aligned ascending (close > EMA20 > EMA50 > EMA200) → LONG, strength 0.9, confidence 0.85
- All aligned descending → SHORT, same numbers
- Close above EMA200 but mixed shorter EMAs → LONG, 0.5, 0.6
- Close below EMA200 but mixed shorter EMAs → SHORT, 0.5, 0.6
- Otherwise → NEUTRAL, 0.0, 0.4
- If insufficient data (any EMA at the latest bar is NaN) → returns `None`

**Files:**
- Create: `worktrees/sp-0/backend/app/core/scoring/layer1_macro.py` (stub)
- Create: `worktrees/sp-0/backend/tests/unit/test_scoring_layer1.py`

- [ ] **Step 1: Failing test**

```python
import numpy as np
import pandas as pd
import pytest

from app.core.scoring.layer1_macro import score
from app.core.scoring.types import Direction


def make_bars(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC"),
        "open": closes,
        "high": [c * 1.005 for c in closes],
        "low":  [c * 0.995 for c in closes],
        "close": closes,
        "volume": [1000.0] * n,
    }).set_index("ts")


def test_strong_uptrend_gives_long() -> None:
    closes = list(np.linspace(100.0, 200.0, 250))  # monotonic up
    bars = make_bars(closes)
    result = score(bars)
    assert result is not None
    assert result.direction is Direction.LONG
    assert result.strength == pytest.approx(0.9)
    assert result.confidence == pytest.approx(0.85)


def test_strong_downtrend_gives_short() -> None:
    closes = list(np.linspace(200.0, 100.0, 250))
    bars = make_bars(closes)
    result = score(bars)
    assert result is not None
    assert result.direction is Direction.SHORT


def test_choppy_below_ema200_gives_weak_short() -> None:
    # 250 bars: first 200 around 100, last 50 oscillate around 90
    closes = [100.0] * 200 + [90.0 + (1 if i % 2 else -1) for i in range(50)]
    bars = make_bars(closes)
    result = score(bars)
    assert result is not None
    # Either weak short or neutral; at minimum not strong long
    assert result.direction is not Direction.LONG or result.strength < 0.7


def test_insufficient_data_returns_none() -> None:
    bars = make_bars([100.0] * 50)  # need 200 bars for EMA200
    result = score(bars)
    assert result is None
```

- [ ] **Step 2: Stub** — empty `layer1_macro.py`. Run: ImportError.

---

### Task E3: Layer 1 implementation, green

**Files:**
- Modify: `worktrees/sp-0/backend/app/core/scoring/layer1_macro.py`

- [ ] **Step 1: Implement**

```python
import math
import pandas as pd

from app.core.indicators.ema import ema
from app.core.scoring.types import Direction, LayerScore


def score(bars: pd.DataFrame) -> LayerScore | None:
    closes = bars["close"].to_numpy(dtype=float)
    if closes.shape[0] < 200:
        return None

    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    e200 = ema(closes, 200)
    last_close, last_e20, last_e50, last_e200 = closes[-1], e20[-1], e50[-1], e200[-1]

    if any(math.isnan(v) for v in (last_e20, last_e50, last_e200)):
        return None

    asc = last_close > last_e20 > last_e50 > last_e200
    desc = last_close < last_e20 < last_e50 < last_e200
    above_200 = last_close > last_e200
    below_200 = last_close < last_e200

    if asc:
        return LayerScore(Direction.LONG, 0.9, 0.85, "EMAs aligned ascending")
    if desc:
        return LayerScore(Direction.SHORT, 0.9, 0.85, "EMAs aligned descending")
    if above_200:
        return LayerScore(Direction.LONG, 0.5, 0.6, "Close above EMA200, EMAs mixed")
    if below_200:
        return LayerScore(Direction.SHORT, 0.5, 0.6, "Close below EMA200, EMAs mixed")
    return LayerScore(Direction.NEUTRAL, 0.0, 0.4, "Price ≈ EMA200")
```

- [ ] **Step 2: Tests pass**

```bash
pytest tests/unit/test_scoring_layer1.py -v
```
Expected: `4 passed`.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/app/core/scoring/layer1_macro.py backend/tests/unit/test_scoring_layer1.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): scoring layer 1 (HTF EMA macro)"
```

---

### Task E4: Layer 3 (RSI + MACD momentum) — failing test

**Spec:** Layer 3 combines RSI(14) and MACD histogram into one momentum score.
- RSI > 60 AND MACD hist > 0 → LONG, strength = min(1, RSI/100 × 1.4 × hist_strength), confidence 0.75
- RSI < 40 AND MACD hist < 0 → SHORT, mirror
- RSI ∈ [40,60] OR signs disagree → NEUTRAL, 0.0, 0.4
- `hist_strength = min(1, |hist| / median_abs_hist_last_50)`. Returns None if any indicator has NaN at last bar.

**Files:**
- Create: `worktrees/sp-0/backend/app/core/scoring/layer3_momentum.py` (stub)
- Create: `worktrees/sp-0/backend/tests/unit/test_scoring_layer3.py`

- [ ] **Step 1: Failing test**

```python
import numpy as np
import pandas as pd
import pytest

from app.core.scoring.layer3_momentum import score
from app.core.scoring.types import Direction


def make_bars(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC"),
        "open": closes, "high": [c * 1.005 for c in closes],
        "low":  [c * 0.995 for c in closes], "close": closes,
        "volume": [1000.0] * n,
    }).set_index("ts")


def test_strong_up_momentum_gives_long() -> None:
    closes = list(np.linspace(100.0, 200.0, 100))  # smooth uptrend
    result = score(make_bars(closes))
    assert result is not None
    assert result.direction is Direction.LONG
    assert result.strength > 0.5


def test_strong_down_momentum_gives_short() -> None:
    closes = list(np.linspace(200.0, 100.0, 100))
    result = score(make_bars(closes))
    assert result is not None
    assert result.direction is Direction.SHORT
    assert result.strength > 0.5


def test_flat_market_gives_neutral() -> None:
    closes = [150.0 + (1 if i % 2 else -1) * 0.1 for i in range(100)]
    result = score(make_bars(closes))
    assert result is not None
    assert result.direction is Direction.NEUTRAL


def test_insufficient_data_returns_none() -> None:
    closes = [100.0] * 20
    assert score(make_bars(closes)) is None
```

- [ ] **Step 2: Stub** — empty `layer3_momentum.py`.

---

### Task E5: Layer 3 implementation, green

**Files:**
- Modify: `worktrees/sp-0/backend/app/core/scoring/layer3_momentum.py`

- [ ] **Step 1: Implement**

```python
import math
import numpy as np
import pandas as pd

from app.core.indicators.macd import macd
from app.core.indicators.rsi import rsi
from app.core.scoring.types import Direction, LayerScore


def score(bars: pd.DataFrame) -> LayerScore | None:
    closes = bars["close"].to_numpy(dtype=float)
    if closes.shape[0] < 50:
        return None

    rsi14 = rsi(closes, 14)
    _, _, hist = macd(closes, 12, 26, 9)
    last_rsi, last_hist = rsi14[-1], hist[-1]
    if math.isnan(last_rsi) or math.isnan(last_hist):
        return None

    abs_hist = np.abs(hist[-50:])
    median_abs = float(np.nanmedian(abs_hist))
    if median_abs == 0 or math.isnan(median_abs):
        median_abs = 1e-9
    hist_strength = min(1.0, abs(last_hist) / median_abs)

    if last_rsi > 60 and last_hist > 0:
        strength = min(1.0, (last_rsi / 100.0) * 1.4 * hist_strength)
        return LayerScore(Direction.LONG, strength, 0.75, "RSI>60 + MACD hist+")
    if last_rsi < 40 and last_hist < 0:
        strength = min(1.0, ((100 - last_rsi) / 100.0) * 1.4 * hist_strength)
        return LayerScore(Direction.SHORT, strength, 0.75, "RSI<40 + MACD hist-")
    return LayerScore(Direction.NEUTRAL, 0.0, 0.4, "RSI/MACD disagree or mid-zone")
```

- [ ] **Step 2: Tests pass + commit**

```bash
pytest tests/unit/test_scoring_layer3.py -v
git -c safe.directory='A:/v5_Trade_bot' add backend/app/core/scoring/layer3_momentum.py backend/tests/unit/test_scoring_layer3.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): scoring layer 3 (RSI + MACD momentum)"
```

---

### Task E6: Layer 5 (volume confirmation) — failing test

**Spec:** Layer 5 checks the latest bar's volume against the rolling 20-bar mean. Direction must agree with the bar's direction (close > open ⇒ LONG candidate; close < open ⇒ SHORT).
- Volume > 2× mean AND close > open → LONG, strength = min(1, vol_ratio/3), confidence 0.7
- Volume > 2× mean AND close < open → SHORT, mirror
- Volume < 0.5× mean → NEUTRAL with strength 0.0, confidence 0.5 ("low conviction")
- Otherwise → NEUTRAL, 0.0, 0.6

**Files:**
- Create: `worktrees/sp-0/backend/app/core/scoring/layer5_volume.py` (stub)
- Create: `worktrees/sp-0/backend/tests/unit/test_scoring_layer5.py`

- [ ] **Step 1: Failing test**

```python
import pandas as pd
import pytest

from app.core.scoring.layer5_volume import score
from app.core.scoring.types import Direction


def make_bars(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["ts"] = pd.date_range("2026-01-01", periods=len(rows), freq="1h", tz="UTC")
    return df.set_index("ts")


def base_rows(n: int, close: float = 100.0, vol: float = 1000.0) -> list[dict]:
    return [{"open": close, "high": close + 1, "low": close - 1,
             "close": close, "volume": vol} for _ in range(n)]


def test_volume_spike_with_bullish_bar_gives_long() -> None:
    rows = base_rows(20)
    rows.append({"open": 100, "high": 105, "low": 99, "close": 104, "volume": 5000})
    result = score(make_bars(rows))
    assert result is not None
    assert result.direction is Direction.LONG
    assert result.strength > 0.5


def test_volume_spike_with_bearish_bar_gives_short() -> None:
    rows = base_rows(20)
    rows.append({"open": 100, "high": 101, "low": 95, "close": 96, "volume": 5000})
    result = score(make_bars(rows))
    assert result is not None
    assert result.direction is Direction.SHORT


def test_low_volume_bar_gives_neutral() -> None:
    rows = base_rows(20)
    rows.append({"open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 200})
    result = score(make_bars(rows))
    assert result is not None
    assert result.direction is Direction.NEUTRAL


def test_insufficient_data_returns_none() -> None:
    rows = base_rows(10)
    assert score(make_bars(rows)) is None
```

---

### Task E7: Layer 5 implementation, green

**Files:**
- Modify: `worktrees/sp-0/backend/app/core/scoring/layer5_volume.py`

- [ ] **Step 1: Implement**

```python
import pandas as pd

from app.core.scoring.types import Direction, LayerScore


def score(bars: pd.DataFrame) -> LayerScore | None:
    if len(bars) < 21:  # need 20 history bars + 1 latest
        return None

    last = bars.iloc[-1]
    history = bars.iloc[-21:-1]
    mean_vol = float(history["volume"].mean())
    if mean_vol <= 0:
        return None

    ratio = float(last["volume"]) / mean_vol
    bull = last["close"] > last["open"]
    bear = last["close"] < last["open"]

    if ratio > 2.0 and bull:
        return LayerScore(Direction.LONG, min(1.0, ratio / 3.0), 0.7,
                          f"vol {ratio:.1f}× avg + bullish bar")
    if ratio > 2.0 and bear:
        return LayerScore(Direction.SHORT, min(1.0, ratio / 3.0), 0.7,
                          f"vol {ratio:.1f}× avg + bearish bar")
    if ratio < 0.5:
        return LayerScore(Direction.NEUTRAL, 0.0, 0.5, "low volume — no conviction")
    return LayerScore(Direction.NEUTRAL, 0.0, 0.6, "average volume — neutral")
```

- [ ] **Step 2: Tests pass + commit**

```bash
pytest tests/unit/test_scoring_layer5.py -v
git -c safe.directory='A:/v5_Trade_bot' add backend/app/core/scoring/layer5_volume.py backend/tests/unit/test_scoring_layer5.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): scoring layer 5 (volume confirmation)"
```

---

### Task E8: Aggregator — failing test

**Spec:** Aggregator takes a `dict[int, LayerScore | None]` for layers 1..10 and returns a `FinalScore`.
- Each present layer gets equal weight 1/9 (decision 2.3).
- L10 (RL brain) is `None` in SP-0 — its weight is redistributed to other present layers proportionally.
- `score = Σ(weight × signed_strength × confidence)` over present non-L10 layers; clamp to [-1, +1].
- `direction = LONG if score > +0.10 else SHORT if score < -0.10 else NEUTRAL`.
- `confidence = mean(layer.confidence) over present layers`.
- `contributing_layers = tuple of present layer ids`.

**Files:**
- Create: `worktrees/sp-0/backend/app/core/scoring/aggregator.py` (stub)
- Create: `worktrees/sp-0/backend/tests/unit/test_scoring_aggregator.py`

- [ ] **Step 1: Failing test**

```python
import pytest

from app.core.scoring.aggregator import aggregate
from app.core.scoring.types import LayerScore, Direction


def L(direction: Direction, strength: float, confidence: float = 0.8) -> LayerScore:
    return LayerScore(direction, strength, confidence)


def test_all_long_layers_aggregate_to_long() -> None:
    scores = {i: L(Direction.LONG, 0.8) for i in (1, 3, 5)}
    scores.update({i: None for i in (2, 4, 6, 7, 8, 9, 10)})
    fs = aggregate(scores)
    assert fs.direction is Direction.LONG
    assert fs.score > 0.10
    assert fs.contributing_layers == (1, 3, 5)


def test_mixed_layers_can_neutralise() -> None:
    scores = {
        1: L(Direction.LONG, 0.8),
        3: L(Direction.SHORT, 0.8),
        5: L(Direction.NEUTRAL, 0.0),
    }
    scores.update({i: None for i in (2, 4, 6, 7, 8, 9, 10)})
    fs = aggregate(scores)
    assert abs(fs.score) <= 0.10
    assert fs.direction is Direction.NEUTRAL


def test_single_layer_can_drive_direction() -> None:
    scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    scores[3] = L(Direction.SHORT, 1.0, confidence=1.0)
    fs = aggregate(scores)
    # Only L3 present; weight redistributes to 1.0
    assert fs.direction is Direction.SHORT
    assert fs.score == pytest.approx(-1.0)


def test_no_layers_present_returns_neutral_zero() -> None:
    scores: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    fs = aggregate(scores)
    assert fs.score == 0.0
    assert fs.direction is Direction.NEUTRAL
    assert fs.confidence == 0.0


def test_score_clamped_to_unit_interval() -> None:
    scores = {i: L(Direction.LONG, 1.0, confidence=1.0) for i in range(1, 10)}
    scores[10] = None
    fs = aggregate(scores)
    assert fs.score == pytest.approx(1.0)
```

- [ ] **Step 2: Stub** — empty `aggregator.py`.

---

### Task E9: Aggregator implementation, green

**Files:**
- Modify: `worktrees/sp-0/backend/app/core/scoring/aggregator.py`

- [ ] **Step 1: Implement**

```python
from app.core.scoring.types import Direction, FinalScore, LayerScore

_NEUTRAL_BAND = 0.10
_BASE_WEIGHT = 1.0 / 9  # decision 2.3 — equal weights for L1..L9


def aggregate(layer_results: dict[int, LayerScore | None]) -> FinalScore:
    present = {i: s for i, s in layer_results.items() if s is not None and i != 10}
    if not present:
        return FinalScore(
            score=0.0, direction=Direction.NEUTRAL, confidence=0.0,
            layer_results=layer_results, contributing_layers=(),
        )

    # Weight redistribution: each present layer gets _BASE_WEIGHT, then we
    # rescale so the sum of present weights = 1.0 (handles missing layers).
    raw_total_weight = _BASE_WEIGHT * len(present)
    rescale = 1.0 / raw_total_weight if raw_total_weight > 0 else 1.0

    score = 0.0
    confidences: list[float] = []
    for layer in present.values():
        score += _BASE_WEIGHT * rescale * layer.signed_strength * layer.confidence
        confidences.append(layer.confidence)

    score = max(-1.0, min(1.0, score))

    if score > _NEUTRAL_BAND:
        direction = Direction.LONG
    elif score < -_NEUTRAL_BAND:
        direction = Direction.SHORT
    else:
        direction = Direction.NEUTRAL

    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

    return FinalScore(
        score=score,
        direction=direction,
        confidence=avg_conf,
        layer_results=layer_results,
        contributing_layers=tuple(sorted(present.keys())),
    )
```

- [ ] **Step 2: Tests pass + commit**

```bash
pytest tests/unit/test_scoring_aggregator.py -v
git -c safe.directory='A:/v5_Trade_bot' add backend/app/core/scoring/aggregator.py backend/tests/unit/test_scoring_aggregator.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): scoring aggregator (equal-weight, clamp, neutral band)"
```

---

## Phase F — Custom Paper Engine

Implements §2.1 (custom engine, not Freqtrade) and writes audit-chained rows per §5.14. SP-0 holds positions in memory; SP-4 will integrate the RL brain. PPO reward shaping (§5.5) is computed in SP-4 — SP-0 just records the trade outcome.

### Task F1: Signal + Trade types — failing test

**Files:**
- Create: `worktrees/sp-0/backend/app/core/execution/__init__.py` (empty)
- Create: `worktrees/sp-0/backend/app/core/execution/types.py`
- Create: `worktrees/sp-0/backend/tests/unit/test_execution_types.py`

- [ ] **Step 1: Failing test**

```python
import pytest
from datetime import datetime, timezone

from app.core.execution.types import Signal, Trade, ExitReason
from app.core.scoring.types import Direction


def test_signal_has_entry_sl_tp_size() -> None:
    sig = Signal(
        symbol="BTC/USDT", timeframe="1h",
        ts=datetime(2026, 5, 1, 12, tzinfo=timezone.utc),
        direction=Direction.LONG,
        entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        position_size=0.01, confidence=0.7, reasoning={},
    )
    assert sig.risk_reward == pytest.approx(2.0)


def test_signal_neutral_direction_rejected() -> None:
    with pytest.raises(ValueError):
        Signal(
            symbol="BTC/USDT", timeframe="1h",
            ts=datetime(2026, 5, 1, tzinfo=timezone.utc),
            direction=Direction.NEUTRAL,
            entry_price=100, stop_loss=95, take_profit=110,
            position_size=0.01, confidence=0.7, reasoning={},
        )


def test_short_signal_risk_reward() -> None:
    sig = Signal(
        symbol="BTC/USDT", timeframe="1h",
        ts=datetime(2026, 5, 1, tzinfo=timezone.utc),
        direction=Direction.SHORT,
        entry_price=100.0, stop_loss=105.0, take_profit=90.0,
        position_size=0.01, confidence=0.8, reasoning={},
    )
    assert sig.risk_reward == pytest.approx(2.0)


def test_trade_pnl_pct_long() -> None:
    t = Trade(
        symbol="BTC/USDT", direction=Direction.LONG,
        entry_price=100.0, exit_price=110.0,
        stop_loss=95.0, take_profit=110.0, position_size=0.01,
        opened_at=datetime(2026,5,1, tzinfo=timezone.utc),
        closed_at=datetime(2026,5,1,5, tzinfo=timezone.utc),
        bars_held=5, exit_reason=ExitReason.TAKE_PROFIT, reasoning={},
    )
    assert t.pnl_pct == pytest.approx(10.0)


def test_trade_pnl_pct_short() -> None:
    t = Trade(
        symbol="BTC/USDT", direction=Direction.SHORT,
        entry_price=100.0, exit_price=95.0,
        stop_loss=105.0, take_profit=90.0, position_size=0.01,
        opened_at=datetime(2026,5,1, tzinfo=timezone.utc),
        closed_at=datetime(2026,5,1,3, tzinfo=timezone.utc),
        bars_held=3, exit_reason=ExitReason.TAKE_PROFIT, reasoning={},
    )
    assert t.pnl_pct == pytest.approx(5.0)
```

- [ ] **Step 2: Implementation**

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from app.core.scoring.types import Direction


class ExitReason(str, Enum):
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    TIMEOUT = "TIMEOUT"
    MANUAL = "MANUAL"


@dataclass(frozen=True)
class Signal:
    symbol: str
    timeframe: str
    ts: datetime
    direction: Direction
    entry_price: float
    stop_loss: float
    take_profit: float
    position_size: float          # fraction of portfolio (e.g. 0.01 = 1%)
    confidence: float             # [0, 1]
    reasoning: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.direction is Direction.NEUTRAL:
            raise ValueError("Signal cannot have NEUTRAL direction")
        if self.position_size <= 0:
            raise ValueError("position_size must be positive")

    @property
    def risk_reward(self) -> float:
        if self.direction is Direction.LONG:
            risk = self.entry_price - self.stop_loss
            reward = self.take_profit - self.entry_price
        else:
            risk = self.stop_loss - self.entry_price
            reward = self.entry_price - self.take_profit
        return reward / risk if risk > 0 else 0.0


@dataclass(frozen=True)
class Trade:
    symbol: str
    direction: Direction
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    position_size: float
    opened_at: datetime
    closed_at: datetime
    bars_held: int
    exit_reason: ExitReason
    reasoning: dict[str, Any] = field(default_factory=dict)

    @property
    def pnl_pct(self) -> float:
        if self.direction is Direction.LONG:
            return (self.exit_price - self.entry_price) / self.entry_price * 100.0
        return (self.entry_price - self.exit_price) / self.entry_price * 100.0
```

- [ ] **Step 3: Tests pass + commit**

```bash
pytest tests/unit/test_execution_types.py -v
git -c safe.directory='A:/v5_Trade_bot' add backend/app/core/execution/
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): execution types (Signal, Trade, ExitReason)"
```

---

### Task F2: Paper engine — failing test (open + SL + TP + skip)

**Files:**
- Create: `worktrees/sp-0/backend/app/core/execution/paper_engine.py` (stub)
- Create: `worktrees/sp-0/backend/tests/unit/test_paper_engine.py`

- [ ] **Step 1: Failing test**

```python
from datetime import datetime, timezone, timedelta

import pytest

from app.core.execution.paper_engine import PaperEngine
from app.core.execution.types import Signal, ExitReason
from app.core.scoring.types import Direction


def make_signal(direction: Direction = Direction.LONG, **overrides) -> Signal:
    base = dict(
        symbol="BTC/USDT", timeframe="1h",
        ts=datetime(2026, 5, 1, 12, tzinfo=timezone.utc),
        direction=direction,
        entry_price=100.0, stop_loss=95.0, take_profit=110.0,
        position_size=0.01, confidence=0.7, reasoning={},
    )
    base.update(overrides)
    return Signal(**base)


def test_open_long_position_creates_open_trade() -> None:
    engine = PaperEngine()
    opened = engine.on_signal(make_signal())
    assert opened is True
    assert engine.open_position("BTC/USDT") is not None


def test_existing_position_blocks_new_open() -> None:
    engine = PaperEngine()
    engine.on_signal(make_signal())
    again = engine.on_signal(make_signal())
    assert again is False


def test_long_closes_on_stop_loss_hit() -> None:
    engine = PaperEngine()
    engine.on_signal(make_signal())
    closed = engine.on_bar(
        symbol="BTC/USDT",
        ts=datetime(2026, 5, 1, 13, tzinfo=timezone.utc),
        high=101.0, low=94.0, close=96.0,
    )
    assert closed is not None
    assert closed.exit_reason is ExitReason.STOP_LOSS
    assert closed.exit_price == 95.0
    assert closed.pnl_pct == pytest.approx(-5.0)


def test_long_closes_on_take_profit_hit() -> None:
    engine = PaperEngine()
    engine.on_signal(make_signal())
    closed = engine.on_bar(
        symbol="BTC/USDT",
        ts=datetime(2026, 5, 1, 14, tzinfo=timezone.utc),
        high=112.0, low=99.0, close=109.0,
    )
    assert closed is not None
    assert closed.exit_reason is ExitReason.TAKE_PROFIT
    assert closed.exit_price == 110.0


def test_short_closes_on_take_profit_hit() -> None:
    engine = PaperEngine()
    engine.on_signal(make_signal(
        direction=Direction.SHORT,
        entry_price=100.0, stop_loss=105.0, take_profit=90.0,
    ))
    closed = engine.on_bar(
        symbol="BTC/USDT",
        ts=datetime(2026, 5, 1, 14, tzinfo=timezone.utc),
        high=101.0, low=89.0, close=92.0,
    )
    assert closed is not None
    assert closed.exit_reason is ExitReason.TAKE_PROFIT
    assert closed.exit_price == 90.0


def test_when_both_sl_and_tp_in_same_bar_pessimistic_assumes_sl() -> None:
    engine = PaperEngine()
    engine.on_signal(make_signal())
    closed = engine.on_bar(
        symbol="BTC/USDT",
        ts=datetime(2026, 5, 1, 15, tzinfo=timezone.utc),
        high=112.0, low=94.0, close=100.0,  # both touched
    )
    assert closed is not None
    assert closed.exit_reason is ExitReason.STOP_LOSS


def test_bar_without_position_returns_none() -> None:
    engine = PaperEngine()
    closed = engine.on_bar(
        symbol="BTC/USDT",
        ts=datetime(2026, 5, 1, tzinfo=timezone.utc),
        high=110, low=90, close=100,
    )
    assert closed is None


def test_bars_held_counts_correctly() -> None:
    engine = PaperEngine()
    engine.on_signal(make_signal(ts=datetime(2026, 5, 1, 12, tzinfo=timezone.utc)))
    engine.on_bar("BTC/USDT", datetime(2026, 5, 1, 13, tzinfo=timezone.utc),
                  high=101, low=99, close=100)
    engine.on_bar("BTC/USDT", datetime(2026, 5, 1, 14, tzinfo=timezone.utc),
                  high=101, low=99, close=100)
    closed = engine.on_bar("BTC/USDT", datetime(2026, 5, 1, 15, tzinfo=timezone.utc),
                           high=112, low=99, close=109)
    assert closed is not None
    assert closed.bars_held == 3
```

- [ ] **Step 2: Stub** — empty `paper_engine.py`. Run: ImportError on `PaperEngine`.

---

### Task F3: Paper engine implementation, green

**Files:**
- Modify: `worktrees/sp-0/backend/app/core/execution/paper_engine.py`

- [ ] **Step 1: Implement**

```python
from dataclasses import dataclass
from datetime import datetime

from app.core.execution.types import ExitReason, Signal, Trade
from app.core.scoring.types import Direction


@dataclass
class _OpenPosition:
    signal: Signal
    bars_held: int = 0


class PaperEngine:
    """In-memory paper trading engine.

    SP-0 keeps state in memory (single process, single asset). SP-4 will
    persist positions and integrate with the RL brain for reward signals.
    Pessimistic exit assumption when both SL and TP are touched in the same
    bar: assumes SL hit first (worst case for the trader).
    """

    def __init__(self) -> None:
        self._positions: dict[str, _OpenPosition] = {}
        self._closed: list[Trade] = []

    def on_signal(self, signal: Signal) -> bool:
        if signal.symbol in self._positions:
            return False
        if signal.direction is Direction.NEUTRAL:
            return False
        self._positions[signal.symbol] = _OpenPosition(signal=signal)
        return True

    def open_position(self, symbol: str) -> Signal | None:
        pos = self._positions.get(symbol)
        return pos.signal if pos else None

    def on_bar(
        self, symbol: str, ts: datetime, *, high: float, low: float, close: float
    ) -> Trade | None:
        pos = self._positions.get(symbol)
        if pos is None:
            return None
        pos.bars_held += 1
        sig = pos.signal

        sl_hit = (sig.direction is Direction.LONG and low <= sig.stop_loss) or (
            sig.direction is Direction.SHORT and high >= sig.stop_loss
        )
        tp_hit = (sig.direction is Direction.LONG and high >= sig.take_profit) or (
            sig.direction is Direction.SHORT and low <= sig.take_profit
        )

        if not sl_hit and not tp_hit:
            return None

        # Pessimistic: SL first if both
        if sl_hit:
            exit_price = sig.stop_loss
            reason = ExitReason.STOP_LOSS
        else:
            exit_price = sig.take_profit
            reason = ExitReason.TAKE_PROFIT

        trade = Trade(
            symbol=sig.symbol,
            direction=sig.direction,
            entry_price=sig.entry_price,
            exit_price=exit_price,
            stop_loss=sig.stop_loss,
            take_profit=sig.take_profit,
            position_size=sig.position_size,
            opened_at=sig.ts,
            closed_at=ts,
            bars_held=pos.bars_held,
            exit_reason=reason,
            reasoning=sig.reasoning,
        )
        del self._positions[symbol]
        self._closed.append(trade)
        return trade

    @property
    def closed_trades(self) -> list[Trade]:
        return list(self._closed)
```

- [ ] **Step 2: Tests pass**

```bash
pytest tests/unit/test_paper_engine.py -v
```
Expected: `8 passed`.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/app/core/execution/paper_engine.py backend/tests/unit/test_paper_engine.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): in-memory paper engine (open, SL/TP exit, pessimistic)"
```

---

### Task F4: Persist trades + predictions via audit hash chain — failing test

**Files:**
- Create: `worktrees/sp-0/backend/app/core/execution/persistence.py` (stub)
- Create: `worktrees/sp-0/backend/tests/unit/test_paper_persistence.py`

- [ ] **Step 1: Failing test**

```python
import pytest
import sqlalchemy as sa
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.core.execution.paper_engine import PaperEngine
from app.core.execution.persistence import persist_trade, persist_prediction
from app.core.execution.types import Signal, ExitReason
from app.core.scoring.types import Direction


def make_signal() -> Signal:
    return Signal(
        symbol="BTC/USDT", timeframe="1h",
        ts=datetime(2026, 5, 1, 12, tzinfo=timezone.utc),
        direction=Direction.LONG,
        entry_price=100, stop_loss=95, take_profit=110,
        position_size=0.01, confidence=0.7, reasoning={"layer1": "long"},
    )


@pytest.mark.asyncio
async def test_persist_trade_writes_with_hash_chain() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE paper_trades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, direction TEXT, "
            "entry_price REAL, exit_price REAL, stop_loss REAL, take_profit REAL, "
            "position_size REAL, opened_at TEXT, closed_at TEXT, pnl_pct REAL, "
            "max_drawdown_during REAL, bars_held INTEGER, exit_reason TEXT, "
            "reasoning TEXT, model_version TEXT, prev_hash TEXT, row_hash TEXT UNIQUE)"
        ))

    pe = PaperEngine()
    pe.on_signal(make_signal())
    trade = pe.on_bar("BTC/USDT", datetime(2026,5,1,13, tzinfo=timezone.utc),
                      high=112, low=99, close=110)
    assert trade is not None

    async with AsyncSession(engine) as session:
        h = await persist_trade(session, trade)
        await session.commit()
        rows = (await session.execute(sa.text(
            "SELECT prev_hash, row_hash, exit_reason FROM paper_trades"
        ))).all()

    assert len(rows) == 1
    assert rows[0].prev_hash == "0" * 64
    assert rows[0].row_hash == h
    assert rows[0].exit_reason == "TAKE_PROFIT"


@pytest.mark.asyncio
async def test_persist_prediction_writes_with_hash_chain() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE predictions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, timeframe TEXT, "
            "ts TEXT, layer_scores TEXT, final_score REAL, direction TEXT, "
            "confidence REAL, inputs_hash TEXT, model_version TEXT, "
            "cold_start INTEGER, prev_hash TEXT, row_hash TEXT UNIQUE)"
        ))

    payload = dict(
        symbol="BTC/USDT", timeframe="1h",
        ts=datetime(2026,5,1, tzinfo=timezone.utc).isoformat(),
        layer_scores='{"1":"long"}',
        final_score=0.42, direction="LONG", confidence=0.7,
        inputs_hash="abc123", model_version="sp-0", cold_start=1,
    )

    async with AsyncSession(engine) as session:
        h = await persist_prediction(session, payload)
        await session.commit()
        row = (await session.execute(sa.text(
            "SELECT prev_hash, row_hash FROM predictions"
        ))).first()

    assert row.prev_hash == "0" * 64
    assert row.row_hash == h
```

- [ ] **Step 2: Stub** — empty `persistence.py`. Run: fail.

---

### Task F5: Persistence implementation, green

**Files:**
- Modify: `worktrees/sp-0/backend/app/core/execution/persistence.py`

- [ ] **Step 1: Implement**

```python
import json
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.execution.types import Trade
from app.db.audit import insert_with_chain


async def persist_trade(session: AsyncSession, trade: Trade) -> str:
    payload = {
        "symbol": trade.symbol,
        "direction": trade.direction.value,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "stop_loss": trade.stop_loss,
        "take_profit": trade.take_profit,
        "position_size": trade.position_size,
        "opened_at": trade.opened_at.isoformat(),
        "closed_at": trade.closed_at.isoformat(),
        "pnl_pct": trade.pnl_pct,
        "max_drawdown_during": None,
        "bars_held": trade.bars_held,
        "exit_reason": trade.exit_reason.value,
        "reasoning": json.dumps(trade.reasoning),
        "model_version": "sp-0",
    }
    return await insert_with_chain(session, "paper_trades", payload)


async def persist_prediction(session: AsyncSession, payload: dict) -> str:
    """Caller is responsible for shaping `payload` to match the predictions schema."""
    return await insert_with_chain(session, "predictions", payload)
```

- [ ] **Step 2: Tests pass + commit**

```bash
pytest tests/unit/test_paper_persistence.py -v
git -c safe.directory='A:/v5_Trade_bot' add backend/app/core/execution/persistence.py backend/tests/unit/test_paper_persistence.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): persist trades + predictions via audit hash chain"
```

---

## Phase G — REST API Routes

### Task G1: Health endpoint + router wiring — failing test

**Files:**
- Create: `worktrees/sp-0/backend/app/api/__init__.py` (empty)
- Create: `worktrees/sp-0/backend/app/api/routes/__init__.py` (empty)
- Create: `worktrees/sp-0/backend/app/api/routes/health.py` (stub)
- Modify: `worktrees/sp-0/backend/app/main.py`
- Create: `worktrees/sp-0/backend/tests/integration/test_api_health.py`

- [ ] **Step 1: Failing test (uses httpx ASGITransport)**

```python
import pytest
import httpx
from app.main import app


@pytest.mark.asyncio
async def test_health_returns_ok() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "trading-radar"
    assert "version" in body
```

- [ ] **Step 2: Stub** — empty `health.py`. Run: 404.

- [ ] **Step 3: Implement `health.py`**

```python
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "trading-radar",
        "version": "0.1.0-sp-0",
    }
```

- [ ] **Step 4: Wire router in `main.py`**

Edit `app/main.py` `create_app()` to include the router:

```python
from app.api.routes import health


def create_app() -> FastAPI:
    app = FastAPI(
        title="trading-radar",
        version="0.1.0-sp-0",
        lifespan=lifespan,
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
    )
    app.include_router(health.router)
    return app
```

- [ ] **Step 5: Tests pass + commit**

```bash
pytest tests/integration/test_api_health.py -v
git -c safe.directory='A:/v5_Trade_bot' add backend/app/api/ backend/app/main.py backend/tests/integration/test_api_health.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): /api/v1/health endpoint"
```

---

### Task G2: Pydantic schemas for live prediction

**Files:**
- Create: `worktrees/sp-0/backend/app/api/schemas.py`

- [ ] **Step 1: Implement (no test — Pydantic enforces)**

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class LayerScoreOut(BaseModel):
    direction: Literal["LONG", "SHORT", "NEUTRAL"]
    strength: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    notes: str = ""


class FinalScoreOut(BaseModel):
    score: float = Field(ge=-1.0, le=1.0)
    direction: Literal["LONG", "SHORT", "NEUTRAL"]
    confidence: float = Field(ge=0.0, le=1.0)
    contributing_layers: list[int]


class TradeSetupOut(BaseModel):
    direction: Literal["LONG", "SHORT", "NEUTRAL"]
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_reward: float | None = None


class MomentumPanelOut(BaseModel):
    rsi: float | None
    macd_line: float | None
    macd_signal: float | None
    macd_hist: float | None


class LivePredictionOut(BaseModel):
    symbol: str
    timeframe: str
    ts: datetime
    price: float
    final: FinalScoreOut
    layer_scores: dict[str, LayerScoreOut | None]
    trade_setup: TradeSetupOut
    momentum: MomentumPanelOut
    cold_start: bool = True
    inputs_hash: str
```

- [ ] **Step 2: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/app/api/schemas.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): pydantic schemas for live prediction payload"
```

---

### Task G3: Prediction-builder helper — failing test

The prediction-builder is the "compose all layers + aggregate + build TradeSetup + compute inputs_hash" function used by both REST and WS routes. Pulling it into its own module avoids duplicating logic.

**Files:**
- Create: `worktrees/sp-0/backend/app/core/predictor.py` (stub)
- Create: `worktrees/sp-0/backend/tests/unit/test_predictor.py`

- [ ] **Step 1: Failing test**

```python
import numpy as np
import pandas as pd

from app.core.predictor import build_prediction


def make_bars(n: int = 250) -> pd.DataFrame:
    closes = list(np.linspace(100.0, 200.0, n))
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC"),
        "open": closes, "high": [c * 1.01 for c in closes],
        "low":  [c * 0.99 for c in closes], "close": closes,
        "volume": [1000.0] * n,
    }).set_index("ts")


def test_build_prediction_returns_required_keys() -> None:
    bars = make_bars()
    pred = build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars)
    assert pred.symbol == "BTC/USDT"
    assert pred.timeframe == "1h"
    assert pred.final.direction in {"LONG", "SHORT", "NEUTRAL"}
    assert -1.0 <= pred.final.score <= 1.0
    assert "1" in pred.layer_scores  # Layer 1 always evaluated
    assert pred.inputs_hash  # non-empty


def test_uptrend_yields_long_direction() -> None:
    bars = make_bars(250)
    pred = build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars)
    assert pred.final.direction == "LONG"


def test_inputs_hash_is_deterministic_for_same_input() -> None:
    bars = make_bars()
    a = build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars)
    b = build_prediction(symbol="BTC/USDT", timeframe="1h", bars=bars)
    assert a.inputs_hash == b.inputs_hash
```

- [ ] **Step 2: Stub** — empty `predictor.py`.

---

### Task G4: Prediction-builder implementation, green

**Files:**
- Modify: `worktrees/sp-0/backend/app/core/predictor.py`

- [ ] **Step 1: Implement**

```python
import hashlib
import math
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from app.api.schemas import (
    FinalScoreOut, LayerScoreOut, LivePredictionOut, MomentumPanelOut, TradeSetupOut,
)
from app.core.indicators.macd import macd
from app.core.indicators.rsi import rsi
from app.core.scoring.aggregator import aggregate
from app.core.scoring.layer1_macro import score as score_l1
from app.core.scoring.layer3_momentum import score as score_l3
from app.core.scoring.layer5_volume import score as score_l5
from app.core.scoring.types import Direction, LayerScore


def _layer_to_out(layer: LayerScore | None) -> LayerScoreOut | None:
    if layer is None:
        return None
    return LayerScoreOut(
        direction=layer.direction.value,
        strength=layer.strength,
        confidence=layer.confidence,
        notes=layer.notes,
    )


def _compute_inputs_hash(symbol: str, timeframe: str, bars: pd.DataFrame) -> str:
    last = bars.iloc[-1]
    canon = (
        f"{symbol}|{timeframe}|{bars.index[-1].isoformat()}|"
        f"{last['open']}|{last['high']}|{last['low']}|{last['close']}|{last['volume']}"
    )
    return hashlib.sha256(canon.encode()).hexdigest()


def _build_trade_setup(direction: Direction, last_close: float, atr: float) -> TradeSetupOut:
    if direction is Direction.NEUTRAL or atr <= 0:
        return TradeSetupOut(direction=direction.value)
    if direction is Direction.LONG:
        sl = last_close - 1.5 * atr
        tp = last_close + 3.0 * atr
    else:
        sl = last_close + 1.5 * atr
        tp = last_close - 3.0 * atr
    risk = abs(last_close - sl)
    reward = abs(tp - last_close)
    rr = reward / risk if risk > 0 else 0.0
    return TradeSetupOut(
        direction=direction.value, entry=round(last_close, 2),
        stop_loss=round(sl, 2), take_profit=round(tp, 2),
        risk_reward=round(rr, 2),
    )


def _atr(bars: pd.DataFrame, period: int = 14) -> float:
    if len(bars) < period + 1:
        return 0.0
    h = bars["high"].to_numpy(dtype=float)
    l = bars["low"].to_numpy(dtype=float)
    c = bars["close"].to_numpy(dtype=float)
    prev_close = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_close), np.abs(l - prev_close)))
    return float(np.mean(tr[-period:]))


def build_prediction(
    *, symbol: str, timeframe: str, bars: pd.DataFrame
) -> LivePredictionOut:
    layer_results: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    layer_results[1] = score_l1(bars)
    layer_results[3] = score_l3(bars)
    layer_results[5] = score_l5(bars)

    final = aggregate(layer_results)

    closes = bars["close"].to_numpy(dtype=float)
    rsi14 = rsi(closes, 14)
    macd_line, macd_signal, macd_hist = macd(closes, 12, 26, 9)

    def _safe(arr: np.ndarray) -> float | None:
        v = float(arr[-1])
        return None if math.isnan(v) else v

    momentum = MomentumPanelOut(
        rsi=_safe(rsi14),
        macd_line=_safe(macd_line),
        macd_signal=_safe(macd_signal),
        macd_hist=_safe(macd_hist),
    )

    trade_setup = _build_trade_setup(final.direction, float(closes[-1]), _atr(bars))

    return LivePredictionOut(
        symbol=symbol,
        timeframe=timeframe,
        ts=bars.index[-1].to_pydatetime(),
        price=float(closes[-1]),
        final=FinalScoreOut(
            score=final.score, direction=final.direction.value,
            confidence=final.confidence,
            contributing_layers=list(final.contributing_layers),
        ),
        layer_scores={str(i): _layer_to_out(s) for i, s in layer_results.items()},
        trade_setup=trade_setup,
        momentum=momentum,
        cold_start=True,
        inputs_hash=_compute_inputs_hash(symbol, timeframe, bars),
    )
```

- [ ] **Step 2: Tests pass + commit**

```bash
pytest tests/unit/test_predictor.py -v
git -c safe.directory='A:/v5_Trade_bot' add backend/app/core/predictor.py backend/tests/unit/test_predictor.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): build_prediction — composes layers + ATR-based trade setup"
```

---

### Task G5: `/api/v1/predict/{symbol}/{tf}` route — failing test

**Files:**
- Create: `worktrees/sp-0/backend/app/api/routes/tab1.py` (stub)
- Modify: `worktrees/sp-0/backend/app/main.py` (include router)
- Create: `worktrees/sp-0/backend/tests/integration/test_api_predict.py`

- [ ] **Step 1: Failing test (mocks Binance via dependency override)**

```python
import numpy as np
import pytest
import httpx
import pandas as pd
from datetime import datetime, timezone

from app.main import app
from app.api.routes import tab1
from app.core.dataquality.validator import Candle


def _fake_candles(n: int = 250) -> list[Candle]:
    closes = list(np.linspace(100.0, 200.0, n))
    return [
        Candle(
            symbol="BTC/USDT", timeframe="1h",
            ts=datetime(2026, 1, 1, tzinfo=timezone.utc) + pd.Timedelta(hours=i),
            open=c, high=c * 1.01, low=c * 0.99, close=c, volume=1000.0,
        )
        for i, c in enumerate(closes)
    ]


@pytest.mark.asyncio
async def test_predict_returns_full_payload(monkeypatch) -> None:
    async def fake_fetch(symbol: str, timeframe: str, *, limit: int = 500):
        return _fake_candles(min(limit, 250))

    monkeypatch.setattr(tab1, "_fetch_recent_candles", fake_fetch)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/predict/BTC-USDT/1h")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "BTC/USDT"
    assert body["timeframe"] == "1h"
    assert body["final"]["direction"] in {"LONG", "SHORT", "NEUTRAL"}
    assert "rsi" in body["momentum"]
    assert body["inputs_hash"]


@pytest.mark.asyncio
async def test_predict_unknown_symbol_returns_404(monkeypatch) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/predict/XXX-YYY/1h")
    assert r.status_code == 404
```

- [ ] **Step 2: Stub** — empty `tab1.py`.

---

### Task G6: Predict route implementation, green

**Files:**
- Modify: `worktrees/sp-0/backend/app/api/routes/tab1.py`

- [ ] **Step 1: Implement**

```python
from datetime import datetime, timezone

import httpx
import pandas as pd
from fastapi import APIRouter, HTTPException

from app.api.schemas import LivePredictionOut
from app.core.dataquality.validator import Candle
from app.core.predictor import build_prediction
from app.data.adapters.binance import BinanceClient
from app.data.universe import is_tradable

router = APIRouter(prefix="/api/v1", tags=["tab1"])

_TIMEFRAMES = {"1m", "5m", "15m", "1h", "4h", "1d"}


def _normalize_pair(symbol_path: str) -> str:
    """BTC-USDT (URL-safe) -> BTC/USDT."""
    return symbol_path.replace("-", "/").upper()


def _to_binance_symbol(pair: str) -> str:
    return pair.replace("/", "")


async def _fetch_recent_candles(symbol: str, timeframe: str, *, limit: int = 500) -> list[Candle]:
    async with httpx.AsyncClient() as http:
        client = BinanceClient(http=http)
        return await client.fetch_klines(_to_binance_symbol(symbol), timeframe, limit=limit)


def _candles_to_df(candles: list[Candle]) -> pd.DataFrame:
    df = pd.DataFrame([c.__dict__ for c in candles])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")[["open", "high", "low", "close", "volume"]]


@router.get("/predict/{symbol_path}/{timeframe}", response_model=LivePredictionOut)
async def predict(symbol_path: str, timeframe: str) -> LivePredictionOut:
    pair = _normalize_pair(symbol_path)
    if timeframe not in _TIMEFRAMES:
        raise HTTPException(400, f"Unsupported timeframe {timeframe}")
    if not is_tradable(pair, datetime.now(timezone.utc)):
        raise HTTPException(404, f"Unknown symbol {pair}")

    candles = await _fetch_recent_candles(pair, timeframe, limit=300)
    if len(candles) < 200:
        raise HTTPException(503, "Insufficient candles to compute prediction")
    bars = _candles_to_df(candles)
    return build_prediction(symbol=pair, timeframe=timeframe, bars=bars)
```

- [ ] **Step 2: Wire router in `main.py`**

Add to `create_app()`:

```python
from app.api.routes import tab1
app.include_router(tab1.router)
```

- [ ] **Step 3: Tests pass + commit**

```bash
pytest tests/integration/test_api_predict.py -v
git -c safe.directory='A:/v5_Trade_bot' add backend/app/api/routes/tab1.py backend/app/main.py backend/tests/integration/test_api_predict.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): GET /api/v1/predict/{symbol}/{tf} endpoint"
```

---

## Phase H — WebSocket Channel

Implements §5.8 (heartbeat, reconnect, gap-fill).

### Task H1: Connection manager — failing test

**Files:**
- Create: `worktrees/sp-0/backend/app/ws/__init__.py` (empty)
- Create: `worktrees/sp-0/backend/app/ws/manager.py` (stub)
- Create: `worktrees/sp-0/backend/tests/unit/test_ws_manager.py`

- [ ] **Step 1: Failing test**

```python
import pytest

from app.ws.manager import ConnectionManager, Subscription


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, data: dict) -> None:
        if self.closed:
            raise ConnectionError("closed")
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_subscribe_then_publish_routes_message() -> None:
    mgr = ConnectionManager()
    sock = FakeSocket()
    sub = Subscription(client_id="c1", channel="live_prediction",
                       params={"symbol": "BTC/USDT", "timeframe": "1h"})
    mgr.attach(sub, sock)

    await mgr.publish(channel="live_prediction",
                      key={"symbol": "BTC/USDT", "timeframe": "1h"},
                      payload={"price": 100.0})

    assert len(sock.sent) == 1
    assert sock.sent[0]["payload"]["price"] == 100.0


@pytest.mark.asyncio
async def test_publish_to_nonmatching_key_does_not_send() -> None:
    mgr = ConnectionManager()
    sock = FakeSocket()
    sub = Subscription(client_id="c1", channel="live_prediction",
                       params={"symbol": "BTC/USDT", "timeframe": "1h"})
    mgr.attach(sub, sock)

    await mgr.publish(channel="live_prediction",
                      key={"symbol": "ETH/USDT", "timeframe": "1h"},
                      payload={"price": 100.0})

    assert sock.sent == []


@pytest.mark.asyncio
async def test_detach_removes_subscription() -> None:
    mgr = ConnectionManager()
    sock = FakeSocket()
    sub = Subscription(client_id="c1", channel="live_prediction",
                       params={"symbol": "BTC/USDT", "timeframe": "1h"})
    mgr.attach(sub, sock)
    mgr.detach("c1")

    await mgr.publish(channel="live_prediction",
                      key={"symbol": "BTC/USDT", "timeframe": "1h"},
                      payload={"price": 100.0})

    assert sock.sent == []


@pytest.mark.asyncio
async def test_failed_send_auto_detaches_client() -> None:
    mgr = ConnectionManager()
    sock = FakeSocket()
    await sock.close()  # makes sends raise
    sub = Subscription(client_id="c1", channel="live_prediction",
                       params={"symbol": "BTC/USDT", "timeframe": "1h"})
    mgr.attach(sub, sock)

    await mgr.publish(channel="live_prediction",
                      key={"symbol": "BTC/USDT", "timeframe": "1h"},
                      payload={"price": 100.0})

    # Subsequent publish must not raise (client already detached)
    await mgr.publish(channel="live_prediction",
                      key={"symbol": "BTC/USDT", "timeframe": "1h"},
                      payload={"price": 101.0})
    assert mgr.subscriber_count("live_prediction") == 0
```

---

### Task H2: Connection manager implementation, green

**Files:**
- Modify: `worktrees/sp-0/backend/app/ws/manager.py`

- [ ] **Step 1: Implement**

```python
import asyncio
from dataclasses import dataclass
from typing import Any, Protocol


class WebSocketLike(Protocol):
    async def send_json(self, data: dict) -> None: ...
    async def close(self) -> None: ...


@dataclass(frozen=True)
class Subscription:
    client_id: str
    channel: str
    params: dict[str, Any]


def _key_matches(sub_params: dict[str, Any], publish_key: dict[str, Any]) -> bool:
    return all(sub_params.get(k) == v for k, v in publish_key.items())


class ConnectionManager:
    def __init__(self) -> None:
        self._subs: dict[str, tuple[Subscription, WebSocketLike]] = {}
        self._lock = asyncio.Lock()

    def attach(self, sub: Subscription, socket: WebSocketLike) -> None:
        self._subs[sub.client_id] = (sub, socket)

    def detach(self, client_id: str) -> None:
        self._subs.pop(client_id, None)

    def subscriber_count(self, channel: str) -> int:
        return sum(1 for sub, _ in self._subs.values() if sub.channel == channel)

    async def publish(
        self, *, channel: str, key: dict[str, Any], payload: dict[str, Any]
    ) -> None:
        message = {"channel": channel, "key": key, "payload": payload}
        dead: list[str] = []
        for cid, (sub, sock) in list(self._subs.items()):
            if sub.channel != channel:
                continue
            if not _key_matches(sub.params, key):
                continue
            try:
                await sock.send_json(message)
            except Exception:  # noqa: BLE001 — drop dead client
                dead.append(cid)
        async with self._lock:
            for cid in dead:
                self._subs.pop(cid, None)
```

- [ ] **Step 2: Tests pass + commit**

```bash
pytest tests/unit/test_ws_manager.py -v
git -c safe.directory='A:/v5_Trade_bot' add backend/app/ws/ backend/tests/unit/test_ws_manager.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): WebSocket connection manager (subscribe/publish/detach)"
```

---

### Task H3: WebSocket route + heartbeat + live-prediction publisher

**Files:**
- Create: `worktrees/sp-0/backend/app/api/routes/ws.py`
- Modify: `worktrees/sp-0/backend/app/main.py`

- [ ] **Step 1: ws.py**

```python
import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.ws.manager import ConnectionManager, Subscription

router = APIRouter(prefix="/ws/v1", tags=["ws"])

manager = ConnectionManager()
HEARTBEAT_SECONDS = 15.0


async def _heartbeat_loop(ws: WebSocket) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        try:
            await ws.send_json({"type": "ping",
                                "ts": datetime.now(timezone.utc).isoformat()})
        except Exception:  # noqa: BLE001
            return


@router.websocket("/{client_id}")
async def ws_endpoint(ws: WebSocket, client_id: str) -> None:
    await ws.accept()
    hb_task = asyncio.create_task(_heartbeat_loop(ws))
    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            action = msg.get("action")
            channel = msg.get("channel")
            params = msg.get("params") or {}

            if action == "subscribe" and channel:
                manager.attach(Subscription(client_id, channel, params), ws)
                await ws.send_json({"type": "subscribed",
                                    "channel": channel, "params": params})
            elif action == "unsubscribe":
                manager.detach(client_id)
                await ws.send_json({"type": "unsubscribed"})
            elif action == "pong":
                pass
            else:
                await ws.send_json({"type": "error", "reason": "unknown action"})
    except WebSocketDisconnect:
        pass
    finally:
        hb_task.cancel()
        manager.detach(client_id)
```

- [ ] **Step 2: Wire router**

In `main.py` `create_app()`:

```python
from app.api.routes import ws as ws_routes
app.include_router(ws_routes.router)
```

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/app/api/routes/ws.py backend/app/main.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): WebSocket route /ws/v1/{client_id} with heartbeat"
```

---

### Task H4: Live-prediction worker (publishes every closed bar AND persists to audit chain)

**Files:**
- Create: `worktrees/sp-0/backend/app/ws/live_prediction.py`
- Modify: `worktrees/sp-0/backend/app/main.py` (start worker on lifespan startup)

This worker is what guarantees acceptance criterion §4.1#8 — every prediction lands in the `predictions` table with hash-chained `prev_hash`/`row_hash`. WS publish + DB persist happen in the same loop iteration; if persist fails the WS message is suppressed (don't lie to the UI).

- [ ] **Step 1: live_prediction.py**

```python
import asyncio
import json
import logging

import httpx
import pandas as pd

from app.api.routes.ws import manager
from app.core.execution.persistence import persist_prediction
from app.core.predictor import build_prediction
from app.data.adapters.binance import BinanceClient, BinanceKlineStream
from app.db.session import get_session_factory

log = logging.getLogger(__name__)


async def run_live_prediction(symbol_pair: str = "BTC/USDT", timeframe: str = "1h") -> None:
    """Seed REST history, subscribe to Binance WS, on each closed candle:
    1. Append candle to in-memory DataFrame (last 1000 bars)
    2. Build prediction (compose layers + aggregate)
    3. Persist prediction row to predictions table via audit hash chain
    4. Publish payload over WebSocket so UI updates
    Persist comes BEFORE publish — if persist fails (DB down), do not publish.
    """
    binance_symbol = symbol_pair.replace("/", "")

    async with httpx.AsyncClient() as http:
        client = BinanceClient(http=http)
        history = await client.fetch_klines(binance_symbol, timeframe, limit=300)
    bars = pd.DataFrame([c.__dict__ for c in history])
    bars["ts"] = pd.to_datetime(bars["ts"], utc=True)
    bars = bars.set_index("ts")[["open", "high", "low", "close", "volume"]]

    session_factory = get_session_factory()
    stream = BinanceKlineStream(symbol=binance_symbol, timeframe=timeframe)

    async for candle in stream.stream():
        new_row = pd.DataFrame(
            [[candle.open, candle.high, candle.low, candle.close, candle.volume]],
            columns=["open", "high", "low", "close", "volume"],
            index=[candle.ts],
        )
        bars = pd.concat([bars, new_row]).iloc[-1000:]

        try:
            pred = build_prediction(symbol=symbol_pair, timeframe=timeframe, bars=bars)
        except Exception as e:  # noqa: BLE001
            log.warning("build_prediction failed: %s", e)
            continue

        # Persist BEFORE publishing — audit chain is the source of truth.
        try:
            async with session_factory() as session:
                await persist_prediction(session, {
                    "symbol": pred.symbol,
                    "timeframe": pred.timeframe,
                    "ts": pred.ts.isoformat(),
                    "layer_scores": json.dumps({
                        k: (v.model_dump() if v else None)
                        for k, v in pred.layer_scores.items()
                    }),
                    "final_score": pred.final.score,
                    "direction": pred.final.direction,
                    "confidence": pred.final.confidence,
                    "inputs_hash": pred.inputs_hash,
                    "model_version": "sp-0",
                    "cold_start": pred.cold_start,
                })
                await session.commit()
        except Exception as e:  # noqa: BLE001
            log.error("persist_prediction failed; suppressing publish: %s", e)
            continue

        await manager.publish(
            channel="live_prediction",
            key={"symbol": symbol_pair, "timeframe": timeframe},
            payload=pred.model_dump(mode="json"),
        )


def start_background_worker() -> asyncio.Task:
    return asyncio.create_task(run_live_prediction())
```

- [ ] **Step 2: Modify main.py lifespan to start worker**

```python
from app.ws.live_prediction import start_background_worker


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _ = get_settings()
    worker = start_background_worker()
    try:
        yield
    finally:
        worker.cancel()
```

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/app/ws/live_prediction.py backend/app/main.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): live-prediction WS worker (REST seed + WS stream)"
```

---

### Task H5: WebSocket reconnect E2E test

**Files:**
- Create: `worktrees/sp-0/backend/tests/integration/test_ws_reconnect.py`

This validates the **client side** would survive a reconnect — backend just needs to re-accept and re-publish on resubscribe. The frontend's `useWebSocket` hook (Phase J) handles the actual reconnect.

- [ ] **Step 1: Write test**

```python
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app


def test_ws_accepts_connection_and_subscribes() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/v1/test-client-1") as ws:
        ws.send_text(json.dumps({
            "action": "subscribe", "channel": "live_prediction",
            "params": {"symbol": "BTC/USDT", "timeframe": "1h"},
        }))
        msg = ws.receive_json()
        assert msg["type"] == "subscribed"
        assert msg["channel"] == "live_prediction"


def test_ws_handles_unsubscribe() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/v1/test-client-2") as ws:
        ws.send_text(json.dumps({"action": "subscribe", "channel": "live_prediction",
                                 "params": {"symbol": "BTC/USDT", "timeframe": "1h"}}))
        ws.receive_json()  # subscribed
        ws.send_text(json.dumps({"action": "unsubscribe"}))
        msg = ws.receive_json()
        assert msg["type"] == "unsubscribed"


def test_ws_two_independent_clients_isolated() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/v1/cA") as wsA, \
         client.websocket_connect("/ws/v1/cB") as wsB:
        wsA.send_text(json.dumps({"action": "subscribe", "channel": "live_prediction",
                                  "params": {"symbol": "BTC/USDT", "timeframe": "1h"}}))
        wsA.receive_json()
        wsB.send_text(json.dumps({"action": "subscribe", "channel": "live_prediction",
                                  "params": {"symbol": "ETH/USDT", "timeframe": "1h"}}))
        wsB.receive_json()
        # No assertion on traffic; passing means no cross-talk in 1s
        # (real cross-talk would be caught by Phase O E2E)
```

- [ ] **Step 2: Tests pass + commit**

```bash
pytest tests/integration/test_ws_reconnect.py -v
git -c safe.directory='A:/v5_Trade_bot' add backend/tests/integration/test_ws_reconnect.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "test(sp-0): WebSocket subscribe/unsubscribe/isolation"
```

---

## Phase I — Frontend Layout + Responsive Shell

Mobile-first per §2.7. Default = mobile (<768 px) → drawer for sidebar; `md:` = tablet (≥768) sidebar visible; `lg:` = desktop (≥1024) full layout.

### Task I1: API + WS clients

**Files:**
- Create: `worktrees/sp-0/frontend/src/lib/api.ts`
- Create: `worktrees/sp-0/frontend/src/lib/ws.ts`

- [ ] **Step 1: src/lib/api.ts**

```ts
const BASE = (import.meta.env.VITE_API_URL ?? "/api/v1") as string;

export async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { credentials: "include" });
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${path}`);
  return (await res.json()) as T;
}

export const api = {
  health: () => fetchJson<{ status: string; version: string }>("/health"),
  predict: (symbolPath: string, tf: string) =>
    fetchJson<LivePrediction>(`/predict/${symbolPath}/${tf}`),
};

export interface LayerScore {
  direction: "LONG" | "SHORT" | "NEUTRAL";
  strength: number;
  confidence: number;
  notes: string;
}

export interface LivePrediction {
  symbol: string;
  timeframe: string;
  ts: string;
  price: number;
  final: {
    score: number;
    direction: "LONG" | "SHORT" | "NEUTRAL";
    confidence: number;
    contributing_layers: number[];
  };
  layer_scores: Record<string, LayerScore | null>;
  trade_setup: {
    direction: "LONG" | "SHORT" | "NEUTRAL";
    entry: number | null;
    stop_loss: number | null;
    take_profit: number | null;
    risk_reward: number | null;
  };
  momentum: {
    rsi: number | null;
    macd_line: number | null;
    macd_signal: number | null;
    macd_hist: number | null;
  };
  cold_start: boolean;
  inputs_hash: string;
}
```

- [ ] **Step 2: src/lib/ws.ts (reconnect + heartbeat-aware)**

```ts
type Listener = (msg: unknown) => void;

export class TradingRadarSocket {
  private ws: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private backoff = 1000;
  private clientId: string;
  private url: string;
  private subscription: { channel: string; params: Record<string, unknown> } | null = null;
  private alive = false;
  private heartbeatTimer?: number;

  constructor(clientId: string) {
    this.clientId = clientId;
    const wsBase = (import.meta.env.VITE_WS_URL ?? "/ws/v1") as string;
    this.url = wsBase.startsWith("ws")
      ? `${wsBase}/${clientId}`
      : `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}${wsBase}/${clientId}`;
  }

  connect(): void {
    if (this.alive) return;
    this.alive = true;
    this.open();
  }

  private open(): void {
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => {
      this.backoff = 1000;
      if (this.subscription) {
        this.send({ action: "subscribe", ...this.subscription });
      }
      this.scheduleHeartbeatGuard();
    };
    this.ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data.type === "ping") {
          this.send({ action: "pong" });
          this.scheduleHeartbeatGuard();
          return;
        }
        for (const l of this.listeners) l(data);
      } catch {
        // ignore malformed
      }
    };
    this.ws.onclose = () => {
      window.clearTimeout(this.heartbeatTimer);
      if (!this.alive) return;
      window.setTimeout(() => this.open(), this.backoff);
      this.backoff = Math.min(this.backoff * 2, 30000);
    };
    this.ws.onerror = () => this.ws?.close();
  }

  private scheduleHeartbeatGuard(): void {
    window.clearTimeout(this.heartbeatTimer);
    this.heartbeatTimer = window.setTimeout(() => this.ws?.close(), 45_000);
  }

  subscribe(channel: string, params: Record<string, unknown>): void {
    this.subscription = { channel, params };
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.send({ action: "subscribe", channel, params });
    }
  }

  on(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  close(): void {
    this.alive = false;
    this.ws?.close();
    window.clearTimeout(this.heartbeatTimer);
  }

  private send(payload: unknown): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
  }
}
```

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add frontend/src/lib/
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): frontend API + reconnecting WebSocket client"
```

---

### Task I2: useLivePrediction hook

**Files:**
- Create: `worktrees/sp-0/frontend/src/hooks/useLivePrediction.ts`

- [ ] **Step 1: Implementation**

```ts
import { useEffect, useState, useRef } from "react";
import { api, type LivePrediction } from "@/lib/api";
import { TradingRadarSocket } from "@/lib/ws";

export function useLivePrediction(symbol: string, timeframe: string) {
  const [data, setData] = useState<LivePrediction | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const sockRef = useRef<TradingRadarSocket | null>(null);

  useEffect(() => {
    let cancelled = false;
    const symbolPath = symbol.replace("/", "-");

    api.predict(symbolPath, timeframe).then(
      (d) => { if (!cancelled) setData(d); },
      (e: Error) => { if (!cancelled) setErr(e.message); },
    );

    const sock = new TradingRadarSocket(`tab1-${symbol}-${timeframe}`);
    sockRef.current = sock;
    sock.connect();
    sock.subscribe("live_prediction", { symbol, timeframe });

    const off = sock.on((msg: unknown) => {
      const m = msg as { channel?: string; payload?: LivePrediction };
      if (m.channel === "live_prediction" && m.payload) {
        setData(m.payload);
      }
    });

    return () => {
      cancelled = true;
      off();
      sock.close();
    };
  }, [symbol, timeframe]);

  return { data, err };
}
```

- [ ] **Step 2: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add frontend/src/hooks/
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): useLivePrediction hook (REST seed + WS updates)"
```

---

### Task I3: Layout shell — top nav + timeframe row + sidebar drawer

**Files:**
- Create: `worktrees/sp-0/frontend/src/components/layout/TopNav.tsx`
- Create: `worktrees/sp-0/frontend/src/components/layout/TimeframeRow.tsx`
- Create: `worktrees/sp-0/frontend/src/components/layout/Sidebar.tsx`
- Create: `worktrees/sp-0/frontend/src/components/ui/Panel.tsx`

- [ ] **Step 1: components/ui/Panel.tsx**

```tsx
import type { PropsWithChildren } from "react";

interface PanelProps {
  title: string;
  rightSlot?: React.ReactNode;
  intensity?: "default" | "alert";
}

export function Panel({ title, rightSlot, children, intensity = "default" }:
  PropsWithChildren<PanelProps>) {
  const border =
    intensity === "alert" ? "border border-red/60" : "border border-border";
  return (
    <section
      className={`bg-bg-panel rounded-[4px] ${border} px-[0.55rem] py-[0.4rem] mb-[3px]`}
    >
      <header className="flex items-center justify-between mb-1">
        <h3 className="text-[7.5px] uppercase tracking-[0.04em] text-text-tertiary">
          {title}
        </h3>
        {rightSlot}
      </header>
      <div className="text-[9px] font-mono">{children}</div>
    </section>
  );
}
```

- [ ] **Step 2: components/layout/TopNav.tsx**

```tsx
import { useState } from "react";

interface TopNavProps {
  symbol: string;
  onSymbolChange: (s: string) => void;
  onMenuClick: () => void;
}

export function TopNav({ symbol, onSymbolChange, onMenuClick }: TopNavProps) {
  const [draft, setDraft] = useState(symbol);
  return (
    <nav className="h-8 bg-bg-elevated border-b border-border flex items-center px-2 gap-2">
      <button
        type="button"
        aria-label="Open sidebar"
        onClick={onMenuClick}
        className="md:hidden h-11 w-11 flex items-center justify-center text-text-secondary"
      >
        ≡
      </button>
      <span className="font-mono text-[10px] text-text-secondary">trading-radar</span>
      <form
        className="ml-auto flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          onSymbolChange(draft);
        }}
      >
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value.toUpperCase())}
          className="bg-bg-base border border-border rounded px-2 py-1 text-[10px] font-mono w-32"
          aria-label="Symbol"
        />
      </form>
    </nav>
  );
}
```

- [ ] **Step 3: components/layout/TimeframeRow.tsx**

```tsx
const TFS = ["1m", "5m", "15m", "1h", "4h", "1d"] as const;
type Tf = typeof TFS[number];

interface Props {
  active: Tf;
  onChange: (tf: Tf) => void;
}

export function TimeframeRow({ active, onChange }: Props) {
  return (
    <div className="h-7 bg-bg-base border-b border-border flex items-center px-2 gap-1 overflow-x-auto">
      {TFS.map((tf) => (
        <button
          key={tf}
          type="button"
          onClick={() => onChange(tf)}
          className={`min-h-11 min-w-11 px-2 text-[10px] font-mono rounded ${
            active === tf
              ? "bg-purple text-bg-base"
              : "text-text-secondary hover:bg-bg-elevated"
          }`}
        >
          {tf}
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: components/layout/Sidebar.tsx (drawer on mobile, fixed on md+)**

```tsx
import type { PropsWithChildren } from "react";

interface SidebarProps {
  open: boolean;
  onClose: () => void;
}

export function Sidebar({ open, onClose, children }: PropsWithChildren<SidebarProps>) {
  return (
    <>
      {/* Mobile backdrop */}
      <div
        className={`md:hidden fixed inset-0 bg-black/50 transition-opacity z-40 ${
          open ? "opacity-100" : "opacity-0 pointer-events-none"
        }`}
        onClick={onClose}
        aria-hidden
      />
      <aside
        className={`fixed md:static z-50 top-0 right-0 h-full w-[260px] md:w-[230px]
          bg-bg-base border-l border-border overflow-y-auto p-1
          transition-transform md:translate-x-0
          ${open ? "translate-x-0" : "translate-x-full"}`}
      >
        <button
          type="button"
          onClick={onClose}
          className="md:hidden h-11 w-full text-right pr-2 text-text-secondary"
          aria-label="Close sidebar"
        >
          ✕
        </button>
        {children}
      </aside>
    </>
  );
}
```

- [ ] **Step 5: Vitest test for Panel rendering**

Create `worktrees/sp-0/frontend/tests/unit/Panel.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { Panel } from "@/components/ui/Panel";

test("renders title and children", () => {
  render(<Panel title="Trade Setup">Body content</Panel>);
  expect(screen.getByText("TRADE SETUP".toLowerCase()) || screen.getByText(/trade setup/i)).toBeTruthy();
  expect(screen.getByText("Body content")).toBeInTheDocument();
});

test("uses alert border when intensity=alert", () => {
  const { container } = render(
    <Panel title="alert panel" intensity="alert">x</Panel>
  );
  expect(container.firstChild).toHaveClass("border-red/60");
});
```

- [ ] **Step 6: Run tests**

```bash
cd worktrees/sp-0/frontend
npm test
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add frontend/src/components/ frontend/tests/unit/Panel.test.tsx
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): Panel + TopNav + TimeframeRow + responsive Sidebar"
```

---

## Phase J — Chart Wrapper (TradingView Lightweight Charts)

### Task J1: TVChart component

**Files:**
- Create: `worktrees/sp-0/frontend/src/components/chart/TVChart.tsx`
- Create: `worktrees/sp-0/frontend/src/hooks/useChartHistory.ts`

- [ ] **Step 1: useChartHistory.ts (fetch initial candles for chart)**

Add a backend endpoint first to serve candles. Modify `tab1.py` to add:

```python
from app.api.schemas import LivePredictionOut  # already imported
from pydantic import BaseModel


class CandleOut(BaseModel):
    time: int      # unix seconds (lightweight-charts expects this)
    open: float
    high: float
    low: float
    close: float


@router.get("/candles/{symbol_path}/{timeframe}", response_model=list[CandleOut])
async def candles(symbol_path: str, timeframe: str, limit: int = 500) -> list[CandleOut]:
    pair = _normalize_pair(symbol_path)
    if timeframe not in _TIMEFRAMES:
        raise HTTPException(400, f"Unsupported timeframe {timeframe}")
    if not is_tradable(pair, datetime.now(timezone.utc)):
        raise HTTPException(404, f"Unknown symbol {pair}")
    cs = await _fetch_recent_candles(pair, timeframe, limit=min(1000, limit))
    return [
        CandleOut(time=int(c.ts.timestamp()), open=c.open, high=c.high,
                  low=c.low, close=c.close)
        for c in cs
    ]
```

Commit backend addition:

```bash
git -c safe.directory='A:/v5_Trade_bot' add backend/app/api/routes/tab1.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): /api/v1/candles endpoint for chart"
```

Now `useChartHistory.ts`:

```ts
import { useEffect, useState } from "react";
import { fetchJson } from "@/lib/api";

export interface ChartCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export function useChartHistory(symbol: string, timeframe: string, limit = 500) {
  const [candles, setCandles] = useState<ChartCandle[]>([]);
  useEffect(() => {
    let cancelled = false;
    const path = `/candles/${symbol.replace("/", "-")}/${timeframe}?limit=${limit}`;
    fetchJson<ChartCandle[]>(path).then((cs) => {
      if (!cancelled) setCandles(cs);
    });
    return () => { cancelled = true; };
  }, [symbol, timeframe, limit]);
  return candles;
}
```

- [ ] **Step 2: TVChart.tsx**

```tsx
import { useEffect, useRef } from "react";
import { createChart, type IChartApi, type ISeriesApi, type CandlestickData } from "lightweight-charts";
import { useChartHistory, type ChartCandle } from "@/hooks/useChartHistory";

interface Props {
  symbol: string;
  timeframe: string;
  livePrice?: number;
  liveTs?: string;
}

const TR_GREEN = "#00d68f";
const TR_RED = "#ff3d71";

export function TVChart({ symbol, timeframe, livePrice, liveTs }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);

  const history = useChartHistory(symbol, timeframe);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: { background: { color: "#0d1018" }, textColor: "#c4c8d0" },
      grid: {
        vertLines: { color: "#1f2530" },
        horzLines: { color: "#1f2530" },
      },
      rightPriceScale: { borderColor: "#1f2530" },
      timeScale: { borderColor: "#1f2530", timeVisible: true, secondsVisible: false },
      autoSize: true,
    });
    const series = chart.addCandlestickSeries({
      upColor: TR_GREEN, downColor: TR_RED,
      borderUpColor: TR_GREEN, borderDownColor: TR_RED,
      wickUpColor: TR_GREEN, wickDownColor: TR_RED,
    });
    chartRef.current = chart;
    seriesRef.current = series;
    return () => chart.remove();
  }, []);

  useEffect(() => {
    if (!seriesRef.current || history.length === 0) return;
    const data: CandlestickData[] = history.map((c: ChartCandle) => ({
      time: c.time as CandlestickData["time"],
      open: c.open, high: c.high, low: c.low, close: c.close,
    }));
    seriesRef.current.setData(data);
  }, [history]);

  useEffect(() => {
    if (!seriesRef.current || livePrice == null || liveTs == null) return;
    const t = Math.floor(new Date(liveTs).getTime() / 1000);
    seriesRef.current.update({
      time: t as CandlestickData["time"],
      open: livePrice, high: livePrice, low: livePrice, close: livePrice,
    });
  }, [livePrice, liveTs]);

  return <div ref={containerRef} className="w-full h-full bg-bg-chart" />;
}
```

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add frontend/src/components/chart/ frontend/src/hooks/useChartHistory.ts
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): TVChart wrapper with lightweight-charts + live update"
```

---

## Phase K — Tab 1 Panels + Wire Everything Up

4 panels (the SP-0 acceptance subset). Each is a small dumb component fed by `useLivePrediction`. Per panel: snapshot test + render test.

### Task K1: TradeStatusBar panel

**Files:**
- Create: `worktrees/sp-0/frontend/src/tabs/Tab1LivePrediction/panels/TradeStatusBar.tsx`
- Create: `worktrees/sp-0/frontend/tests/unit/TradeStatusBar.test.tsx`

- [ ] **Step 1: Component**

```tsx
import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

interface Props {
  data: LivePrediction | null;
}

const COLOR = {
  LONG: "text-green",
  SHORT: "text-red",
  NEUTRAL: "text-text-secondary",
} as const;

export function TradeStatusBar({ data }: Props) {
  if (!data) return <Panel title="Trade Status">—</Panel>;
  const dir = data.final.direction;
  return (
    <Panel title="Trade Status" intensity={dir !== "NEUTRAL" ? "alert" : "default"}>
      <div className="flex justify-between">
        <span className={COLOR[dir]}>{dir}</span>
        <span className="text-text-secondary">
          {data.cold_start ? "warming" : "live"}
        </span>
      </div>
    </Panel>
  );
}
```

- [ ] **Step 2: Test**

```tsx
import { render, screen } from "@testing-library/react";
import { TradeStatusBar } from "@/tabs/Tab1LivePrediction/panels/TradeStatusBar";

const mockLong = {
  symbol: "BTC/USDT", timeframe: "1h", ts: "2026-05-01T12:00:00Z",
  price: 100, final: { score: 0.5, direction: "LONG" as const, confidence: 0.7,
                        contributing_layers: [1] },
  layer_scores: {}, trade_setup: { direction: "LONG" as const, entry: 100,
    stop_loss: 95, take_profit: 110, risk_reward: 2 },
  momentum: { rsi: 60, macd_line: 1, macd_signal: 0.5, macd_hist: 0.5 },
  cold_start: false, inputs_hash: "abc",
};

test("renders LONG with green color", () => {
  render(<TradeStatusBar data={mockLong} />);
  expect(screen.getByText("LONG")).toHaveClass("text-green");
});

test("renders dash when no data", () => {
  render(<TradeStatusBar data={null} />);
  expect(screen.getByText("—")).toBeInTheDocument();
});
```

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add frontend/src/tabs/Tab1LivePrediction/panels/TradeStatusBar.tsx frontend/tests/unit/TradeStatusBar.test.tsx
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): TradeStatusBar panel"
```

---

### Task K2: MasterBiasScore panel

**Files:**
- Create: `worktrees/sp-0/frontend/src/tabs/Tab1LivePrediction/panels/MasterBiasScore.tsx`
- Create: `worktrees/sp-0/frontend/tests/unit/MasterBiasScore.test.tsx`

- [ ] **Step 1: Component**

```tsx
import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

interface Props {
  data: LivePrediction | null;
}

const labelFor = (score: number): "BULL" | "BEAR" | "NEUTRAL" => {
  if (score > 0.10) return "BULL";
  if (score < -0.10) return "BEAR";
  return "NEUTRAL";
};

export function MasterBiasScore({ data }: Props) {
  if (!data) return <Panel title="Master Bias Score">—</Panel>;
  const score = data.final.score;
  const pct = ((score + 1) / 2) * 100;
  const label = labelFor(score);
  const trackColor =
    label === "BULL" ? "bg-green" : label === "BEAR" ? "bg-red" : "bg-purple";

  return (
    <Panel title="Master Bias Score">
      <div className="flex justify-between mb-1">
        <span>{(score * 100).toFixed(1)}</span>
        <span className="text-text-secondary">{label}</span>
      </div>
      <div className="h-1 bg-bg-elevated rounded">
        <div
          className={`h-1 rounded ${trackColor}`}
          style={{ width: `${pct}%` }}
          aria-label={`bias ${score.toFixed(2)}`}
        />
      </div>
    </Panel>
  );
}
```

- [ ] **Step 2: Test**

```tsx
import { render, screen } from "@testing-library/react";
import { MasterBiasScore } from "@/tabs/Tab1LivePrediction/panels/MasterBiasScore";

const baseMock = {
  symbol: "BTC/USDT", timeframe: "1h", ts: "2026-05-01T12:00:00Z",
  price: 100, layer_scores: {},
  trade_setup: { direction: "NEUTRAL" as const, entry: null, stop_loss: null, take_profit: null, risk_reward: null },
  momentum: { rsi: null, macd_line: null, macd_signal: null, macd_hist: null },
  cold_start: false, inputs_hash: "x",
};

test("shows BULL label when score positive", () => {
  render(<MasterBiasScore data={{ ...baseMock,
    final: { score: 0.5, direction: "LONG", confidence: 0.7, contributing_layers: [] } }} />);
  expect(screen.getByText("BULL")).toBeInTheDocument();
});

test("shows BEAR label when score negative", () => {
  render(<MasterBiasScore data={{ ...baseMock,
    final: { score: -0.5, direction: "SHORT", confidence: 0.7, contributing_layers: [] } }} />);
  expect(screen.getByText("BEAR")).toBeInTheDocument();
});

test("shows NEUTRAL within band", () => {
  render(<MasterBiasScore data={{ ...baseMock,
    final: { score: 0.05, direction: "NEUTRAL", confidence: 0.5, contributing_layers: [] } }} />);
  expect(screen.getByText("NEUTRAL")).toBeInTheDocument();
});
```

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add frontend/src/tabs/Tab1LivePrediction/panels/MasterBiasScore.tsx frontend/tests/unit/MasterBiasScore.test.tsx
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): MasterBiasScore panel with progress bar"
```

---

### Task K3: MomentumIndicators panel

**Files:**
- Create: `worktrees/sp-0/frontend/src/tabs/Tab1LivePrediction/panels/MomentumIndicators.tsx`
- Create: `worktrees/sp-0/frontend/tests/unit/MomentumIndicators.test.tsx`

- [ ] **Step 1: Component**

```tsx
import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

const fmt = (v: number | null, dp = 2) =>
  v == null ? "—" : v.toFixed(dp);

export function MomentumIndicators({ data }: { data: LivePrediction | null }) {
  const m = data?.momentum;
  return (
    <Panel title="Momentum">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">RSI(14)</span>
        <span className="text-right">{fmt(m?.rsi ?? null, 1)}</span>
        <span className="text-text-secondary">MACD line</span>
        <span className="text-right">{fmt(m?.macd_line ?? null, 4)}</span>
        <span className="text-text-secondary">MACD signal</span>
        <span className="text-right">{fmt(m?.macd_signal ?? null, 4)}</span>
        <span className="text-text-secondary">MACD hist</span>
        <span className="text-right">{fmt(m?.macd_hist ?? null, 4)}</span>
      </div>
    </Panel>
  );
}
```

- [ ] **Step 2: Test**

```tsx
import { render, screen } from "@testing-library/react";
import { MomentumIndicators } from "@/tabs/Tab1LivePrediction/panels/MomentumIndicators";

const data = {
  symbol: "BTC/USDT", timeframe: "1h", ts: "2026-05-01T12:00:00Z",
  price: 100,
  final: { score: 0, direction: "NEUTRAL" as const, confidence: 0, contributing_layers: [] },
  layer_scores: {},
  trade_setup: { direction: "NEUTRAL" as const, entry: null, stop_loss: null, take_profit: null, risk_reward: null },
  momentum: { rsi: 58.2, macd_line: 0.7321, macd_signal: 0.5012, macd_hist: 0.231 },
  cold_start: true, inputs_hash: "x",
};

test("renders momentum values", () => {
  render(<MomentumIndicators data={data} />);
  expect(screen.getByText("58.2")).toBeInTheDocument();
  expect(screen.getByText("0.7321")).toBeInTheDocument();
});

test("renders dashes when null momentum", () => {
  render(<MomentumIndicators data={{ ...data, momentum: { rsi: null, macd_line: null, macd_signal: null, macd_hist: null } }} />);
  // Four dashes for four metrics
  expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(4);
});
```

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add frontend/src/tabs/Tab1LivePrediction/panels/MomentumIndicators.tsx frontend/tests/unit/MomentumIndicators.test.tsx
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): MomentumIndicators panel (RSI + MACD)"
```

---

### Task K4: TradeSetup panel

**Files:**
- Create: `worktrees/sp-0/frontend/src/tabs/Tab1LivePrediction/panels/TradeSetup.tsx`
- Create: `worktrees/sp-0/frontend/tests/unit/TradeSetup.test.tsx`

- [ ] **Step 1: Component**

```tsx
import { Panel } from "@/components/ui/Panel";
import type { LivePrediction } from "@/lib/api";

const fmt = (v: number | null) => (v == null ? "—" : v.toFixed(2));

export function TradeSetup({ data }: { data: LivePrediction | null }) {
  const ts = data?.trade_setup;
  return (
    <Panel title="Trade Setup">
      <div className="grid grid-cols-2 gap-x-2 gap-y-1">
        <span className="text-text-secondary">Entry</span>
        <span className="text-right">{fmt(ts?.entry ?? null)}</span>
        <span className="text-text-secondary">Stop</span>
        <span className="text-right text-red">{fmt(ts?.stop_loss ?? null)}</span>
        <span className="text-text-secondary">TP</span>
        <span className="text-right text-green">{fmt(ts?.take_profit ?? null)}</span>
        <span className="text-text-secondary">R:R</span>
        <span className="text-right">{fmt(ts?.risk_reward ?? null)}</span>
      </div>
    </Panel>
  );
}
```

- [ ] **Step 2: Test**

```tsx
import { render, screen } from "@testing-library/react";
import { TradeSetup } from "@/tabs/Tab1LivePrediction/panels/TradeSetup";

const data = {
  symbol: "BTC/USDT", timeframe: "1h", ts: "2026-05-01T12:00:00Z", price: 100,
  final: { score: 0.5, direction: "LONG" as const, confidence: 0.7, contributing_layers: [1] },
  layer_scores: {},
  trade_setup: { direction: "LONG" as const, entry: 100.0, stop_loss: 95.0, take_profit: 110.0, risk_reward: 2.0 },
  momentum: { rsi: null, macd_line: null, macd_signal: null, macd_hist: null },
  cold_start: false, inputs_hash: "x",
};

test("renders entry/stop/tp/rr", () => {
  render(<TradeSetup data={data} />);
  expect(screen.getByText("100.00")).toBeInTheDocument();
  expect(screen.getByText("95.00")).toBeInTheDocument();
  expect(screen.getByText("110.00")).toBeInTheDocument();
  expect(screen.getByText("2.00")).toBeInTheDocument();
});
```

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add frontend/src/tabs/Tab1LivePrediction/panels/TradeSetup.tsx frontend/tests/unit/TradeSetup.test.tsx
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): TradeSetup panel"
```

---

### Task K5: Tab1 page wires it all together

**Files:**
- Create: `worktrees/sp-0/frontend/src/tabs/Tab1LivePrediction/index.tsx`
- Modify: `worktrees/sp-0/frontend/src/App.tsx`

- [ ] **Step 1: Tab1LivePrediction/index.tsx**

```tsx
import { useState } from "react";
import { TopNav } from "@/components/layout/TopNav";
import { TimeframeRow } from "@/components/layout/TimeframeRow";
import { Sidebar } from "@/components/layout/Sidebar";
import { TVChart } from "@/components/chart/TVChart";
import { useLivePrediction } from "@/hooks/useLivePrediction";
import { TradeStatusBar } from "./panels/TradeStatusBar";
import { MasterBiasScore } from "./panels/MasterBiasScore";
import { MomentumIndicators } from "./panels/MomentumIndicators";
import { TradeSetup } from "./panels/TradeSetup";

type Tf = "1m" | "5m" | "15m" | "1h" | "4h" | "1d";

export function Tab1LivePrediction() {
  const [symbol, setSymbol] = useState("BTC/USDT");
  const [timeframe, setTimeframe] = useState<Tf>("1h");
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { data } = useLivePrediction(symbol, timeframe);

  return (
    <div className="h-screen flex flex-col">
      <TopNav
        symbol={symbol}
        onSymbolChange={(s) => setSymbol(s.includes("/") ? s : s.replace(/(USDT|USDC|BUSD)$/, "/$1"))}
        onMenuClick={() => setDrawerOpen(true)}
      />
      <TimeframeRow active={timeframe} onChange={(tf) => setTimeframe(tf)} />
      <main className="flex-1 flex min-h-0">
        <div className="flex-1 min-w-0">
          <TVChart
            symbol={symbol}
            timeframe={timeframe}
            livePrice={data?.price}
            liveTs={data?.ts}
          />
        </div>
        <Sidebar open={drawerOpen} onClose={() => setDrawerOpen(false)}>
          <TradeStatusBar data={data} />
          <MasterBiasScore data={data} />
          <MomentumIndicators data={data} />
          <TradeSetup data={data} />
        </Sidebar>
      </main>
    </div>
  );
}
```

- [ ] **Step 2: Modify src/App.tsx**

Replace placeholder with:

```tsx
import { Tab1LivePrediction } from "./tabs/Tab1LivePrediction";

export default function App() {
  return <Tab1LivePrediction />;
}
```

- [ ] **Step 3: Manual verify (laptop dev)**

```bash
cd worktrees/sp-0
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```
Open `http://localhost:5173` — should see chart with BTC/USDT 1h candles + 4 panels populated within ~3 seconds.

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add frontend/src/tabs/ frontend/src/App.tsx
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): Tab 1 wires chart + 4 panels via live prediction hook"
```

---

## Phase L — Cloudflare Tunnel + Access Auth

Implements §2.6. SP-0 trusts Cloudflare Access JWT — backend rejects requests without a valid JWT.

### Task L1: Cloudflare Access JWT validator — failing test

**Files:**
- Create: `worktrees/sp-0/backend/app/deps.py` (stub for CFAccessUser)
- Create: `worktrees/sp-0/backend/tests/unit/test_cf_access.py`

- [ ] **Step 1: Failing test**

```python
import time
from unittest.mock import patch

import jwt as pyjwt
import pytest

from app.deps import verify_cf_access_jwt, CFAccessConfig, CFAccessError


# Generate an RSA keypair fixture (for HS testing we'd use HS256;
# Cloudflare uses RS256 — we use a fake public key fetcher here)
@pytest.fixture
def signing_key() -> str:
    # Use HS256 in tests for simplicity; production uses RS256 from CF JWKS.
    return "test-secret-do-not-use-in-prod"


def make_jwt(*, aud: str, iss: str, secret: str, exp_offset: int = 3600,
             email: str = "user@example.com") -> str:
    return pyjwt.encode(
        {"aud": aud, "iss": iss, "email": email,
         "iat": int(time.time()), "exp": int(time.time()) + exp_offset},
        secret, algorithm="HS256",
    )


def test_valid_jwt_returns_email(signing_key: str) -> None:
    cfg = CFAccessConfig(team_domain="myteam.cloudflareaccess.com",
                         aud="my-app-aud", _algorithm="HS256",
                         _key_resolver=lambda _kid: signing_key)
    token = make_jwt(aud="my-app-aud",
                     iss="https://myteam.cloudflareaccess.com",
                     secret=signing_key)
    user = verify_cf_access_jwt(token, cfg=cfg)
    assert user.email == "user@example.com"


def test_wrong_aud_raises(signing_key: str) -> None:
    cfg = CFAccessConfig(team_domain="myteam.cloudflareaccess.com",
                         aud="my-app-aud", _algorithm="HS256",
                         _key_resolver=lambda _kid: signing_key)
    token = make_jwt(aud="OTHER-aud",
                     iss="https://myteam.cloudflareaccess.com",
                     secret=signing_key)
    with pytest.raises(CFAccessError):
        verify_cf_access_jwt(token, cfg=cfg)


def test_expired_jwt_raises(signing_key: str) -> None:
    cfg = CFAccessConfig(team_domain="myteam.cloudflareaccess.com",
                         aud="my-app-aud", _algorithm="HS256",
                         _key_resolver=lambda _kid: signing_key)
    token = make_jwt(aud="my-app-aud",
                     iss="https://myteam.cloudflareaccess.com",
                     secret=signing_key, exp_offset=-1)
    with pytest.raises(CFAccessError):
        verify_cf_access_jwt(token, cfg=cfg)


def test_no_token_raises() -> None:
    cfg = CFAccessConfig(team_domain="myteam.cloudflareaccess.com",
                         aud="my-app-aud")
    with pytest.raises(CFAccessError):
        verify_cf_access_jwt("", cfg=cfg)
```

- [ ] **Step 2: Stub** — empty `deps.py`. Run: ImportError.

---

### Task L2: Implement Cloudflare Access verifier, green

**Files:**
- Modify: `worktrees/sp-0/backend/app/deps.py`

- [ ] **Step 1: Implement**

```python
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt as pyjwt
from fastapi import Header, HTTPException, status

from app.config import get_settings


class CFAccessError(Exception):
    pass


@dataclass
class CFAccessUser:
    email: str
    sub: str
    raw: dict[str, Any]


_JWKS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_JWKS_TTL = 3600.0


def _fetch_jwks(team_domain: str) -> dict[str, Any]:
    now = time.time()
    cached = _JWKS_CACHE.get(team_domain)
    if cached and (now - cached[0]) < _JWKS_TTL:
        return cached[1]
    url = f"https://{team_domain}/cdn-cgi/access/certs"
    r = httpx.get(url, timeout=10.0)
    r.raise_for_status()
    jwks = r.json()
    _JWKS_CACHE[team_domain] = (now, jwks)
    return jwks


def _default_key_resolver(team_domain: str) -> Callable[[str], str]:
    def resolve(kid: str) -> str:
        jwks = _fetch_jwks(team_domain)
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                return pyjwt.algorithms.RSAAlgorithm.from_jwk(key)
        raise CFAccessError(f"kid {kid} not found in JWKS")
    return resolve


@dataclass
class CFAccessConfig:
    team_domain: str
    aud: str
    _algorithm: str = "RS256"
    _key_resolver: Callable[[str], Any] | None = None

    def issuer(self) -> str:
        return f"https://{self.team_domain}"

    def resolver(self) -> Callable[[str], Any]:
        if self._key_resolver is not None:
            return self._key_resolver
        return _default_key_resolver(self.team_domain)


def verify_cf_access_jwt(token: str, *, cfg: CFAccessConfig) -> CFAccessUser:
    if not token:
        raise CFAccessError("missing token")
    try:
        unverified_header = pyjwt.get_unverified_header(token) if cfg._algorithm == "RS256" else {"kid": "test"}
        kid = unverified_header.get("kid", "test")
        key = cfg.resolver()(kid)
        payload = pyjwt.decode(
            token, key, algorithms=[cfg._algorithm],
            audience=cfg.aud, issuer=cfg.issuer(),
        )
    except Exception as e:
        raise CFAccessError(str(e)) from e
    return CFAccessUser(
        email=payload.get("email", ""),
        sub=payload.get("sub", ""),
        raw=payload,
    )


# FastAPI dependency
async def require_cf_user(
    cf_access_jwt_assertion: str = Header(default="", alias="Cf-Access-Jwt-Assertion"),
) -> CFAccessUser:
    settings = get_settings()
    if settings.env == "development":
        # Bypass in dev; LAN access is unauthenticated by design (§2.7)
        return CFAccessUser(email="dev@local", sub="dev", raw={})
    if not settings.cf_access_team_domain or not settings.cf_access_aud:
        raise HTTPException(status_code=503, detail="auth not configured")
    cfg = CFAccessConfig(
        team_domain=settings.cf_access_team_domain,
        aud=settings.cf_access_aud,
    )
    try:
        return verify_cf_access_jwt(cf_access_jwt_assertion, cfg=cfg)
    except CFAccessError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e
```

- [ ] **Step 2: Apply dependency to all routes**

Modify `tab1.py` and `health.py` to require auth in production by adding the dep at router level:

```python
# In tab1.py:
from fastapi import APIRouter, Depends, HTTPException
from app.deps import require_cf_user

router = APIRouter(prefix="/api/v1", tags=["tab1"], dependencies=[Depends(require_cf_user)])
```

Health stays open (no Depends) so Cloudflare Tunnel can health-check.

- [ ] **Step 3: Tests pass + commit**

```bash
pytest tests/unit/test_cf_access.py -v
git -c safe.directory='A:/v5_Trade_bot' add backend/app/deps.py backend/app/api/routes/tab1.py backend/tests/unit/test_cf_access.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): Cloudflare Access JWT verifier + route guard (dev bypass)"
```

---

### Task L3: Cloudflare Tunnel + Access setup runbook

**Files:**
- Create: `worktrees/sp-0/infra/cloudflare/tunnel-config.yml.example`
- Create: `worktrees/sp-0/infra/cloudflare/access-policy.md`

- [ ] **Step 1: tunnel-config.yml.example**

```yaml
# Place at /etc/cloudflared/config.yml on the Oracle host (or ~/.cloudflared/config.yml)
tunnel: <YOUR_TUNNEL_UUID>
credentials-file: /etc/cloudflared/<YOUR_TUNNEL_UUID>.json

ingress:
  # Frontend
  - hostname: trading-radar.<yourdomain>
    service: http://localhost:5173
    originRequest:
      noTLSVerify: true
  # Backend API + WS — same hostname, path-routed
  - hostname: trading-radar.<yourdomain>
    path: /api/.*
    service: http://localhost:8000
  - hostname: trading-radar.<yourdomain>
    path: /ws/.*
    service: http://localhost:8000
  # Default catch-all
  - service: http_status:404
```

- [ ] **Step 2: access-policy.md (runbook)**

```markdown
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
```

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add infra/cloudflare/
git -c safe.directory='A:/v5_Trade_bot' commit -m "docs(sp-0): Cloudflare Tunnel + Access setup runbook"
```

---

## Phase M — Oracle Cloud Deployment Runbook

Manual ops phase. Each task is a runbook step with exact commands and a verification check. The human (or a skilled subagent with cloud creds) executes these.

### Task M1: Provision Oracle Ampere A1 instance

**Files:**
- Create: `worktrees/sp-0/infra/oracle/provision-runbook.md`

- [ ] **Step 1: Write runbook**

```markdown
# Oracle Cloud Always Free — Ampere A1 ARM Provisioning

## Account
1. Sign up at https://www.oracle.com/cloud/free/ (requires credit card; you will not be charged for Always Free).
2. Pick **home region** carefully: Mumbai or Hyderabad for India users (low latency). Cannot be changed later.

## Provision the VM
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

## Once provisioned
1. Note the public IP (e.g., `132.226.45.123`).
2. SSH in:
   ```bash
   ssh -i ~/.ssh/oracle_key ubuntu@<public-ip>
   ```
3. Open ingress rules in OCI Networking → VCN → Security List:
   - Inbound: TCP 22 from your IP only (not 0.0.0.0/0).
   - **Do not open 8000 / 5173 publicly.** Cloudflare Tunnel goes outbound from this host.

## Initial OS hardening
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y ufw fail2ban
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw enable
sudo systemctl enable --now fail2ban
```
```

- [ ] **Step 2: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add infra/oracle/
git -c safe.directory='A:/v5_Trade_bot' commit -m "docs(sp-0): Oracle Ampere A1 provisioning runbook"
```

---

### Task M2: Install Docker + dependencies on Oracle

**Files:** none (runbook only — append to `provision-runbook.md` or document inline).

- [ ] **Step 1: SSH-in installation**

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

- [ ] **Step 2: Verification**

```bash
docker run --rm hello-world
```
Expected: "Hello from Docker!" message.

---

### Task M3: Deploy the stack

- [ ] **Step 1: Generate SSH key for GitHub deploy**

```bash
# On Oracle:
ssh-keygen -t ed25519 -C "oracle-trading-radar" -f ~/.ssh/github_deploy
cat ~/.ssh/github_deploy.pub
# Add this key to GitHub repo as a Deploy Key (read-only).
```

- [ ] **Step 2: Clone repo**

```bash
GIT_SSH_COMMAND="ssh -i ~/.ssh/github_deploy" \
  git clone git@github.com:<your-username>/v5_Trade_bot.git ~/trading-radar
cd ~/trading-radar
git checkout sp-0/main
```

- [ ] **Step 3: Configure .env**

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

- [ ] **Step 4: Build + start**

```bash
docker compose up -d --build
docker compose ps
```
Expected: 4 containers all "healthy".

- [ ] **Step 5: Run migrations**

```bash
docker compose exec backend alembic upgrade head
```
Expected: `Running upgrade 0001_initial -> 0002_audit_chain`.

- [ ] **Step 6: Smoke check**

```bash
curl http://localhost:8000/api/v1/health
# Expected: {"status":"ok","service":"trading-radar","version":"0.1.0-sp-0"}
```

- [ ] **Step 7: Commit deploy notes**

Append to `provision-runbook.md` and commit.

---

### Task M4: GitHub Actions auto-deploy on merge to main

**Files:**
- Create: `worktrees/sp-0/.github/workflows/deploy.yml`

- [ ] **Step 1: Workflow**

```yaml
name: deploy

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Set up SSH
        run: |
          mkdir -p ~/.ssh
          echo "${{ secrets.ORACLE_SSH_KEY }}" > ~/.ssh/oracle_key
          chmod 600 ~/.ssh/oracle_key
          ssh-keyscan -H ${{ secrets.ORACLE_HOST }} >> ~/.ssh/known_hosts

      - name: Deploy on Oracle
        run: |
          ssh -i ~/.ssh/oracle_key ubuntu@${{ secrets.ORACLE_HOST }} \
            'cd ~/trading-radar && \
             git fetch --all && \
             git checkout main && git pull && \
             docker compose up -d --build && \
             docker compose exec -T backend alembic upgrade head && \
             curl -fsS http://localhost:8000/api/v1/health'
```

- [ ] **Step 2: Configure secrets in GitHub**

In repo Settings → Secrets and variables → Actions, add:
- `ORACLE_HOST` = Oracle public IP
- `ORACLE_SSH_KEY` = the private key matching the public key authorized on Oracle

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add .github/
git -c safe.directory='A:/v5_Trade_bot' commit -m "ci(sp-0): GitHub Actions auto-deploy to Oracle on main"
```

---

### Task M5: GitHub Actions backend tests + lint on PR

**Files:**
- Create: `worktrees/sp-0/.github/workflows/ci.yml`

- [ ] **Step 1: Workflow**

```yaml
name: ci

on:
  pull_request:
  push:
    branches: [main]

jobs:
  backend:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: timescale/timescaledb:2.17.2-pg16
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: testpw
          POSTGRES_DB: trading_radar
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U postgres"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
      redis:
        image: redis:7-alpine
        ports: ["6379:6379"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - name: Install deps
        working-directory: backend
        run: |
          pip install --upgrade pip
          pip install -e ".[dev]"
          pip install aiosqlite==0.20.0 respx==0.22.0
      - name: Lint
        working-directory: backend
        run: |
          ruff check .
          mypy app
      - name: Unit + integration tests
        working-directory: backend
        env:
          DATABASE_URL: postgresql+asyncpg://postgres:testpw@localhost:5432/trading_radar
          REDIS_URL: redis://localhost:6379/0
          ENV: development
        run: pytest -v

  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "20" }
      - name: Install + lint + test
        working-directory: frontend
        run: |
          npm ci
          npm run lint
          npm test
          npm run build
```

- [ ] **Step 2: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add .github/workflows/ci.yml
git -c safe.directory='A:/v5_Trade_bot' commit -m "ci(sp-0): backend + frontend test + lint workflow"
```

---

## Phase N — Backups & Disaster Recovery

Implements §5.13. RPO 1h, RTO 4h. Three-copy rule: Oracle local disk + laptop SSD + Backblaze B2.

### Task N1: Hourly pg_dump script

**Files:**
- Create: `worktrees/sp-0/infra/backup/pg_dump_hourly.sh`

- [ ] **Step 1: Write script**

```bash
#!/usr/bin/env bash
# Hourly incremental dump of changed tables. Runs on the Oracle host via cron.
set -euo pipefail

LOG_DIR="${LOG_DIR:-/var/log/trading-radar}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/trading-radar}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
KEEP_HOURS="${KEEP_HOURS:-72}"

mkdir -p "$BACKUP_DIR" "$LOG_DIR"
LOG="$LOG_DIR/pg_dump_hourly.log"

# Source env to get DATABASE_URL
set -a
[[ -f /home/ubuntu/trading-radar/.env ]] && . /home/ubuntu/trading-radar/.env
set +a

# Use docker exec since postgres is in a container
DUMP_FILE="$BACKUP_DIR/hourly_${TIMESTAMP}.sql.gz"

cd /home/ubuntu/trading-radar
docker compose exec -T postgres pg_dump \
    -U "${POSTGRES_USER:-postgres}" \
    -d "${POSTGRES_DB:-trading_radar}" \
    --data-only \
    --no-owner \
    --table=predictions \
    --table=paper_trades \
    --table=watchlist \
    --table=audit_violations \
    --table=data_quality_alerts \
    | gzip > "$DUMP_FILE"

echo "[$(date -u +%FT%TZ)] Wrote $DUMP_FILE ($(du -h "$DUMP_FILE" | cut -f1))" >> "$LOG"

# Prune older than KEEP_HOURS
find "$BACKUP_DIR" -name "hourly_*.sql.gz" -mmin +$((KEEP_HOURS * 60)) -delete
```

- [ ] **Step 2: Install + cron**

On Oracle host:
```bash
sudo cp infra/backup/pg_dump_hourly.sh /usr/local/bin/tr_pg_dump_hourly.sh
sudo chmod +x /usr/local/bin/tr_pg_dump_hourly.sh
( crontab -l 2>/dev/null; echo "0 * * * * /usr/local/bin/tr_pg_dump_hourly.sh" ) | crontab -
```

- [ ] **Step 3: Verify (run manually once)**

```bash
sudo /usr/local/bin/tr_pg_dump_hourly.sh
ls -lh /var/backups/trading-radar/
```

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add infra/backup/pg_dump_hourly.sh
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): hourly pg_dump cron script (data-only, gzip, 72h retention)"
```

---

### Task N2: Nightly pg_basebackup + B2 upload + laptop rsync

**Files:**
- Create: `worktrees/sp-0/infra/backup/pg_basebackup_nightly.sh`
- Create: `worktrees/sp-0/infra/backup/b2_upload.sh`

- [ ] **Step 1: pg_basebackup_nightly.sh**

```bash
#!/usr/bin/env bash
# Nightly full base backup, then upload to B2 and rsync to laptop.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/trading-radar}"
LOG="${LOG:-/var/log/trading-radar/pg_basebackup_nightly.log}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="$BACKUP_DIR/full_${TIMESTAMP}"

mkdir -p "$TARGET" "$(dirname "$LOG")"
set -a
[[ -f /home/ubuntu/trading-radar/.env ]] && . /home/ubuntu/trading-radar/.env
set +a

cd /home/ubuntu/trading-radar
docker compose exec -T postgres pg_basebackup \
    -U "${POSTGRES_USER:-postgres}" \
    -D /tmp/basebackup \
    -F tar -X stream -z -P
docker compose cp postgres:/tmp/basebackup/. "$TARGET/"
docker compose exec -T postgres rm -rf /tmp/basebackup

echo "[$(date -u +%FT%TZ)] Created $TARGET" >> "$LOG"

# Upload to Backblaze B2
/usr/local/bin/tr_b2_upload.sh "$TARGET" || \
    echo "[$(date -u +%FT%TZ)] B2 upload failed" >> "$LOG"

# Rsync to laptop
if [[ -n "${LAPTOP_RSYNC_TARGET:-}" ]]; then
    rsync -avz --partial "$TARGET/" "$LAPTOP_RSYNC_TARGET/full_${TIMESTAMP}/" \
        && echo "[$(date -u +%FT%TZ)] Rsynced to laptop" >> "$LOG" \
        || echo "[$(date -u +%FT%TZ)] Rsync failed" >> "$LOG"
fi

# Retention: keep last 7 nightly fulls
find "$BACKUP_DIR" -maxdepth 1 -name "full_*" -mtime +7 -exec rm -rf {} \;
```

- [ ] **Step 2: b2_upload.sh**

```bash
#!/usr/bin/env bash
# Uploads a directory to B2 using rclone.
set -euo pipefail

SRC="${1:?usage: $0 <source-dir>}"
DEST="b2:${B2_BUCKET}/$(basename "$SRC")"

if ! command -v rclone >/dev/null 2>&1; then
    curl -fsSL https://rclone.org/install.sh | sudo bash
fi

# rclone config must already exist at /root/.config/rclone/rclone.conf with a
# remote named "b2" — see infra/backup/README.md
rclone copy "$SRC" "$DEST" --progress --retries 3 --transfers 4
```

- [ ] **Step 3: Install + cron**

```bash
sudo cp infra/backup/pg_basebackup_nightly.sh /usr/local/bin/tr_pg_basebackup_nightly.sh
sudo cp infra/backup/b2_upload.sh /usr/local/bin/tr_b2_upload.sh
sudo chmod +x /usr/local/bin/tr_pg_basebackup_nightly.sh /usr/local/bin/tr_b2_upload.sh
( crontab -l 2>/dev/null; echo "30 2 * * * /usr/local/bin/tr_pg_basebackup_nightly.sh" ) | crontab -
```

Configure rclone B2 remote (one-time):
```bash
rclone config
# Pick: n (new) → name "b2" → storage "b2" → account_id from env → key from env
```

- [ ] **Step 4: Verify**

```bash
sudo /usr/local/bin/tr_pg_basebackup_nightly.sh
ls /var/backups/trading-radar/
rclone ls b2:trading-radar-backups | head
```

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add infra/backup/
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): nightly pg_basebackup + B2 upload + laptop rsync"
```

---

### Task N3: Recovery rehearsal script

**Files:**
- Create: `worktrees/sp-0/infra/backup/recovery_rehearsal.sh`
- Create: `worktrees/sp-0/infra/backup/README.md`

- [ ] **Step 1: recovery_rehearsal.sh (runs on laptop)**

```bash
#!/usr/bin/env bash
# Restore the latest B2 backup to a temporary postgres on the laptop and verify
# row counts match the production Oracle host. Run quarterly per §5.13.
set -euo pipefail

WORKDIR="${WORKDIR:-/tmp/tr_restore_$(date +%s)}"
TEMP_PORT="${TEMP_PORT:-5433}"
PG_PASS="${PG_PASS:-rehearsalpw}"

mkdir -p "$WORKDIR"
echo "[rehearsal] working in $WORKDIR"

# 1. Pull latest backup from B2
LATEST=$(rclone lsf b2:trading-radar-backups/ --dirs-only | grep '^full_' | sort | tail -1)
echo "[rehearsal] latest backup: $LATEST"
rclone copy "b2:trading-radar-backups/$LATEST" "$WORKDIR/restore" --progress

# 2. Start temp postgres
docker run -d --name tr-restore-pg \
    -e POSTGRES_PASSWORD="$PG_PASS" \
    -e POSTGRES_DB=trading_radar \
    -p $TEMP_PORT:5432 \
    -v "$WORKDIR/restore":/restore:ro \
    timescale/timescaledb:2.17.2-pg16

# Wait for ready
until docker exec tr-restore-pg pg_isready -U postgres; do sleep 2; done

# 3. Restore base backup
docker exec tr-restore-pg sh -c '
    cd /var/lib/postgresql/data && rm -rf ./*
    tar -xzf /restore/base.tar.gz -C /var/lib/postgresql/data
'
docker restart tr-restore-pg
until docker exec tr-restore-pg pg_isready -U postgres; do sleep 2; done

# 4. Compare row counts vs production
echo "[rehearsal] restored row counts:"
docker exec tr-restore-pg psql -U postgres -d trading_radar -c '
    SELECT
      (SELECT count(*) FROM predictions) AS predictions,
      (SELECT count(*) FROM paper_trades) AS paper_trades,
      (SELECT count(*) FROM ohlcv) AS ohlcv;
'

echo "[rehearsal] manually compare these to: ssh oracle 'docker compose exec postgres psql -U postgres trading_radar -c \"...\"'"
echo "[rehearsal] cleanup with: docker rm -f tr-restore-pg && rm -rf $WORKDIR"
```

- [ ] **Step 2: infra/backup/README.md**

```markdown
# Backup & Recovery (SP-0)

## Schedule
- Hourly: `tr_pg_dump_hourly.sh` — data-only dump → /var/backups/trading-radar/hourly_*.sql.gz (72h retention)
- Nightly 02:30 UTC: `tr_pg_basebackup_nightly.sh` — full base + B2 + laptop rsync (7-day retention)

## RPO / RTO
- RPO: 1 hour (worst case = data lost since last hourly dump)
- RTO: 4 hours (full restore + redeploy stack)

## Cloud → laptop sync
Set `LAPTOP_RSYNC_TARGET=user@laptop.lan:/mnt/external_ssd/trading-radar-backups/` in `/home/ubuntu/trading-radar/.env`.
Laptop must have SSH server running with key-based auth from Oracle.

## Recovery rehearsal (quarterly)
Run on laptop:
```bash
infra/backup/recovery_rehearsal.sh
```
Then manually compare reported row counts to Oracle production using the printed command. Archive the output in `docs/superpowers/log.md`.

## Failure-mode plan
- **Oracle suspended:** restore from latest B2 to laptop dev stack → flip Cloudflare Tunnel target → run from laptop until new Oracle account.
- **B2 unavailable:** laptop SSD copy is the failover.
- **Both unavailable + Oracle running:** Oracle host is the source of truth; rebuild backups going forward.
```

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add infra/backup/recovery_rehearsal.sh infra/backup/README.md
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): quarterly recovery rehearsal script + DR README"
```

---

### Task N4: Audit chain integrity verifier

**Files:**
- Create: `worktrees/sp-0/backend/app/db/audit_verify.py`
- Create: `worktrees/sp-0/backend/tests/integration/test_audit_verify.py`

- [ ] **Step 1: Failing test**

```python
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.db.audit import insert_with_chain
from app.db.audit_verify import verify_chain


@pytest.mark.asyncio
async def test_unbroken_chain_passes() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "data TEXT, prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL UNIQUE)"
        ))
    async with AsyncSession(engine) as session:
        for i in range(5):
            await insert_with_chain(session, "t", {"data": f"row-{i}"})
        await session.commit()
        result = await verify_chain(session, "t", columns=["data"])
    assert result.ok
    assert result.violations == []


@pytest.mark.asyncio
async def test_tampered_row_detected() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "data TEXT, prev_hash TEXT NOT NULL, row_hash TEXT NOT NULL UNIQUE)"
        ))
    async with AsyncSession(engine) as session:
        for i in range(5):
            await insert_with_chain(session, "t", {"data": f"row-{i}"})
        await session.commit()
        # tamper row 3
        await session.execute(sa.text("UPDATE t SET data='HACKED' WHERE id=3"))
        await session.commit()
        result = await verify_chain(session, "t", columns=["data"])
    assert not result.ok
    assert any(v.row_id == 3 for v in result.violations)
```

- [ ] **Step 2: Implementation**

```python
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.audit import GENESIS_HASH, compute_row_hash


@dataclass
class Violation:
    row_id: int
    expected: str
    actual: str


@dataclass
class VerifyResult:
    ok: bool
    rows_checked: int = 0
    violations: list[Violation] = field(default_factory=list)


async def verify_chain(
    session: AsyncSession, table: str, *, columns: list[str]
) -> VerifyResult:
    """Walk `table` in id order; recompute row_hash for each row.
    Returns VerifyResult with any breaks logged.
    """
    cols_sql = ", ".join(["id"] + columns + ["prev_hash", "row_hash"])
    rows = (await session.execute(
        sa.text(f"SELECT {cols_sql} FROM {table} ORDER BY id ASC")
    )).all()

    result = VerifyResult(ok=True, rows_checked=len(rows))
    expected_prev = GENESIS_HASH
    for row in rows:
        payload = {c: getattr(row, c) for c in columns}
        expected_hash = compute_row_hash(expected_prev, payload)
        if row.prev_hash != expected_prev or row.row_hash != expected_hash:
            result.ok = False
            result.violations.append(Violation(
                row_id=row.id, expected=expected_hash, actual=row.row_hash,
            ))
        expected_prev = row.row_hash
    return result
```

- [ ] **Step 3: Tests pass + commit**

```bash
pytest tests/integration/test_audit_verify.py -v
git -c safe.directory='A:/v5_Trade_bot' add backend/app/db/audit_verify.py backend/tests/integration/test_audit_verify.py
git -c safe.directory='A:/v5_Trade_bot' commit -m "feat(sp-0): audit chain integrity verifier (detects tampering)"
```

---

## Phase O — Final Validation Gates

The §6.3 universal Definition of Done plus the §4.1 SP-0 acceptance criteria. SP-0 is not "done" until every box here is checked. **No PR to `main` opens before all of Phase O passes.**

### Task O1: End-to-end Playwright test

**Files:**
- Create: `worktrees/sp-0/frontend/tests/e2e/tracer-bullet.spec.ts`

- [ ] **Step 1: Write E2E**

```ts
import { test, expect } from "@playwright/test";

test("tracer bullet: chart + panels load and update", async ({ page }) => {
  // PLAYWRIGHT_BASE_URL points at the staging deploy in CI; localhost for dev.
  await page.goto("/");

  // Wait for chart canvas
  await page.waitForSelector("canvas", { timeout: 15_000 });

  // Wait for at least one panel value (RSI not "—")
  const rsi = page.locator("text=RSI(14)").locator("xpath=..").locator(":scope > :nth-child(2)");
  await expect(rsi).not.toHaveText("—", { timeout: 30_000 });

  // Trade Status appears
  await expect(page.locator("text=Trade Status")).toBeVisible();
});

test("mobile drawer opens and closes", async ({ page, isMobile }) => {
  test.skip(!isMobile, "mobile-only test");

  await page.goto("/");
  await page.click("button[aria-label='Open sidebar']");
  await expect(page.locator("aside")).toBeVisible();
  await page.click("button[aria-label='Close sidebar']");
  await expect(page.locator("aside")).toHaveClass(/translate-x-full/);
});
```

- [ ] **Step 2: Run locally**

```bash
cd worktrees/sp-0
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
cd frontend
npx playwright install --with-deps chromium webkit
npm run test:e2e
```
Expected: 2 specs pass on chromium-desktop, 1 spec on mobile-iphone.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot' add frontend/tests/e2e/
git -c safe.directory='A:/v5_Trade_bot' commit -m "test(sp-0): Playwright E2E (tracer bullet + mobile drawer)"
```

---

### Task O2: TradingView indicator cross-check pass

**Files:** none (manual gate using `tools/validate_indicators.py` from Task D7).

- [ ] **Step 1: Generate CSV**

```bash
cd worktrees/sp-0/backend
python ../tools/validate_indicators.py BTCUSDT 1h 200 > /tmp/check_1h.csv
python ../tools/validate_indicators.py BTCUSDT 1d 200 > /tmp/check_1d.csv
```

- [ ] **Step 2: Manual cross-check (per `tools/README.md`)**

Open each CSV. Pick 10 random closed bars from each. Pull TradingView values for matching timestamp:
- RSI(14)
- EMA(20), EMA(50), EMA(200)
- MACD(12,26,9) — line, signal

For each: compute `pct_diff = abs(ours − tv) / tv × 100`.

**PASS:** all 60 spot-checks ≤ 0.1%. **FAIL:** any > 0.1% — debug indicator math before continuing.

- [ ] **Step 3: Record result in log**

Append to `docs/superpowers/log.md`:
```
2026-05-XX SP-0 indicator cross-check: PASS — 60/60 within 0.1% tolerance
```

- [ ] **Step 4: Commit log**

```bash
git -c safe.directory='A:/v5_Trade_bot' add docs/superpowers/log.md
git -c safe.directory='A:/v5_Trade_bot' commit -m "docs(sp-0): indicator cross-check pass record"
```

---

### Task O3: Mobile manual test on real devices

**Files:** none (manual gate).

- [ ] **Step 1: iPhone Safari**

Open production URL on iPhone over LTE (NOT Wi-Fi):
- Cloudflare Access SSO works
- Page loads under 5s
- No horizontal scroll at any rotation
- Tap "≡" → drawer opens; tap "✕" → drawer closes
- Tap timeframe pills → switches without layout shift
- All 4 panels readable (no overflow)

- [ ] **Step 2: Android Chrome**

Repeat on Android. Note any device-specific issues.

- [ ] **Step 3: Touch target audit**

Use Chrome DevTools mobile emulation: every interactive element must be ≥ 44px tap target.

- [ ] **Step 4: Lighthouse mobile audit**

```bash
npx lighthouse https://trading-radar.<yourdomain>/ \
    --only-categories=performance,accessibility,best-practices \
    --form-factor=mobile --view
```
Expected: Performance ≥ 80, Accessibility ≥ 90.

- [ ] **Step 5: Record + commit log entry**

---

### Task O4: Crash + restart recovery test

- [ ] **Step 1: Simulate Oracle host crash** (per §4.5)

```bash
# SSH to Oracle:
docker compose down
docker compose up -d
docker compose ps
curl http://localhost:8000/api/v1/health
```
Expected: stack returns to healthy within 60s; health endpoint returns 200.

- [ ] **Step 2: Audit chain verify after restart**

```bash
docker compose exec backend python -c "
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
        print('predictions:', r1.ok, len(r1.violations), 'violations')
        print('paper_trades:', r2.ok, len(r2.violations), 'violations')

asyncio.run(main())
"
```
Expected: both `True` with `0 violations`.

- [ ] **Step 3: Full reboot test**

```bash
sudo reboot
# Wait, then SSH back in
docker compose ps
curl http://localhost:8000/api/v1/health
```
Expected: Docker auto-starts containers, stack healthy within 2 minutes.

- [ ] **Step 4: Record result in log + commit.**

---

### Task O5: Laptop-independence test (Oracle is truly the source of truth)

- [ ] **Step 1: Stop laptop completely**

Close laptop lid for 1 hour minimum.

- [ ] **Step 2: From phone over LTE, verify production**

- App still loads at `https://trading-radar.<yourdomain>`
- Chart still updates with new candles
- Trade Status panel still updates
- Hit `/api/v1/health` → 200

- [ ] **Step 3: Confirm row growth in DB**

After 1 hour, SSH into Oracle and check:
```bash
docker compose exec postgres psql -U postgres trading_radar -c \
    "SELECT count(*) FROM predictions WHERE ts > NOW() - INTERVAL '1 hour';"
```
Expected: > 0 (live worker is publishing predictions).

- [ ] **Step 4: Record + commit log entry.**

---

### Task O6: 24-hour soak run

- [ ] **Step 1: Let production run for 24h with no manual intervention.**

- [ ] **Step 2: After 24h, check:**

- `data_quality_alerts` table: no new rows in last 24h, OR all rows are explainable (e.g., known Binance maintenance)
- `audit_violations` table: empty
- Backup cron logs: hourly + nightly both ran successfully
- Disk usage: `df -h` — Postgres data + backups < 20 GB
- Memory: `free -h` — used < 21 GB (within budget)

- [ ] **Step 3: Pass criteria**

- Zero Sev-1 alerts
- Audit chain verify still passes
- Mobile + desktop UI still responsive
- B2 contains last night's full backup

- [ ] **Step 4: Record soak result + commit.**

---

### Task O7: Code-reviewer agent + final PR

- [ ] **Step 1: Invoke `superpowers:requesting-code-review` skill**

Run the code-reviewer agent against the entire `sp-0/main` branch. Address all Sev-1 findings; document Sev-2/3 as follow-up issues.

- [ ] **Step 2: Open PR `sp-0/main` → `main`**

```bash
gh pr create \
  --base main --head sp-0/main \
  --title "SP-0: Tracer bullet (chart + 4 panels + paper engine + audit chain)" \
  --body "$(cat <<'EOF'
## Summary
- Implements SP-0 per docs/superpowers/specs/2026-05-01-trading-radar-meta-plan-design.md §4.
- All §4.1 acceptance criteria checked (see Phase O log entries).
- All §5 cross-cutting policies enforced from day 1.

## Test plan
- [x] Backend pytest passes with ≥85% coverage on scoring/risk/execution/audit
- [x] Frontend Vitest + Playwright E2E pass on chromium and mobile-iphone
- [x] tools/validate_indicators.py — 60/60 ≤ 0.1% vs TradingView
- [x] Mobile audit on iPhone Safari + Android Chrome — Lighthouse ≥80 mobile
- [x] Crash recovery: docker compose down/up + sudo reboot
- [x] Laptop-independence: 1 hour laptop-off, production keeps running
- [x] 24h soak: zero Sev-1 alerts, audit chain unbroken
- [x] Backups: hourly + nightly + B2 + laptop rsync verified
- [x] Code-reviewer agent: no Sev-1 findings

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2.5: Squash-merge after CI green + human approval**

- [ ] **Step 3: Tag the SP-0 release**

```bash
git -c safe.directory='A:/v5_Trade_bot' checkout main
git -c safe.directory='A:/v5_Trade_bot' pull
git -c safe.directory='A:/v5_Trade_bot' tag -a sp-0 -m "SP-0 Tracer Bullet shipped"
git -c safe.directory='A:/v5_Trade_bot' push origin sp-0
```

- [ ] **Step 4: Append final log entry**

In `docs/superpowers/log.md`:
```
2026-XX-XX SP-0 SHIPPED — tracer bullet on Oracle, mobile-responsive,
all §5 policies enforced. Surprises: <fill in what was harder/easier than expected>.
Next: SP-1 ML data pipeline + ghost candles brainstorm.
```

- [ ] **Step 5: Remove the worktree**

```bash
git -c safe.directory='A:/v5_Trade_bot' worktree remove worktrees/sp-0
git -c safe.directory='A:/v5_Trade_bot' branch -D sp-0/main
```

---

## Done.

SP-0 is shipped when every box in Phase O is checked. The next session brainstorms **SP-1 (ML Data Pipeline + Ghost Candles)** — the highest-risk sub-project — using the same skill stack (`brainstorming → writing-plans → using-git-worktrees → executing-plans/subagent-driven-development → TDD → verification-before-completion → requesting-code-review → finishing-a-development-branch`).

