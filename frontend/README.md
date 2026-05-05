# trading-radar-frontend

React + Vite + Tailwind frontend for the V5 Trading Radar.

## Develop

```bash
npm install
npm run dev          # Vite dev server on http://localhost:5173
```

## Verify

```bash
npm run lint         # ESLint, --max-warnings 0
npm run build        # tsc -b && vite build
npm test             # Vitest unit tests (~329 cases)
npm run test:e2e     # Playwright E2E (chromium-desktop + mobile-iphone)
```

## Lighthouse CI (SP-6 Phase F2)

Lighthouse CI runs against the production `dist/` bundle. Targets:
Performance >=80, Accessibility >=80, Best-Practices >=80, SEO >=70.
All assertions are `warn`-level so the CLI does not gate CI until the
operator opts in by switching them to `error`.

Run locally:

```bash
npm run build
npm run lighthouse   # @lhci/cli autorun against ./dist over a static server
```

Requires Chromium installed and reachable by the `chrome-launcher` package
(`@lhci/cli` brings its own static server, so no `npm run dev` is needed).
Reports are uploaded to `temporary-public-storage` by default — flip
`upload.target` in `lighthouserc.json` to `filesystem` for offline use.
