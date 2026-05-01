# CLAUDE.md — Project Rules for `trading-radar`

This file is read by Claude Code at the start of every session. Follow these rules strictly.

## Project Goal
Build a zero-cost retail trading research platform with 3 tabs (Live Prediction / Paper Lab / Scanner Radar) that helps a human trader make manual decisions, NOT an autonomous trading bot. See `MASTER_PLAN.md` for full architecture.

## Hard Rules (Never Violate)

1. **Freqtrade runs in DRY-RUN ONLY.** Never enable live trading in any code path. The `dry_run: true` config flag must always be true.
2. **API keys must be trade-only, never withdrawal.** If implementing API key configuration, validate that withdrawal permission is disabled.
3. **Never invent indicators or patterns beyond the spec.** Stick to the 43 indicators + 82 candle patterns + 76 chart patterns listed in MASTER_PLAN.md.
4. **Match UI colors exactly.** Use CSS variables from `--bg-base` to `--text-tertiary`. Do not introduce new colors.
5. **Match UI dimensions exactly.** Sidebar = 230px fixed, panel padding = 0.4rem 0.55rem, panel gap = 3px, font sizes per spec.
6. **Use JetBrains Mono for data displays, Inter for UI.** Never use any other font.
7. **Always log to audit trail (M22).** Every prediction, every trade decision, every brain update logged with timestamp + model version.
8. **Brain trains nightly only.** Never implement real-time RL training. Use Stable-Baselines3 PPO with replay buffer, train at 00:00 UTC on 256 samples.
9. **Shorts require +2 layer threshold higher than longs.** Asymmetric thresholds for asymmetric risk.
10. **Use Half-Kelly position sizing.** Never full Kelly. Maximum 1.5% per trade.

## Build Order (Strict)

Follow phases in order. Do not start Phase N+1 until Phase N validation passes.

1. **Phase 1 (3 weeks):** Foundation — Docker Compose, FastAPI skeleton, React shell with theme, PostgreSQL+TimescaleDB, Freqtrade dry-run
2. **Phase 2 (10 weeks):** Tab 1 — 10-layer scoring, 158 patterns, 12 traps, all 14 sidebar panels, chart with key levels
3. **Phase 3 (8 weeks):** Ghost candle predictor + RL brain + per-asset checkpoints
4. **Phase 4 (6 weeks):** Tab 3 scanner with parallel async + Redis caching
5. **Phase 5 (6 weeks):** Tab 2 paper lab + news intelligence + 20-day cleanup
6. **Phase 6 (ongoing):** Polish, monitoring, mobile-responsive

## Code Style

- **Python:** Black formatter, isort, type hints required, `pyproject.toml` with strict ruff config
- **TypeScript:** strict mode on, no `any` types, ESLint with `@typescript-eslint/strict`
- **React:** Functional components only, hooks for state, no class components
- **CSS:** Tailwind utility classes preferred, custom CSS only for theme variables
- **Tests:** pytest for backend (>80% coverage on scoring layers), Vitest for frontend

## File Naming

- Python files: `snake_case.py`
- TypeScript files: `PascalCase.tsx` for components, `camelCase.ts` for utilities/hooks
- CSS files: `kebab-case.css`
- Config files: lowercase (e.g., `tailwind.config.ts`)

## Testing Requirements

Before marking any module complete:
- Unit tests for each scoring layer (validate against known values)
- Integration tests for WebSocket pipeline
- Cross-validate indicators against TradingView on 100 random data points
- Backtest each strategy change before merging

## Forbidden Actions

- Do NOT use paid API tiers — only free tier endpoints
- Do NOT enable Freqtrade live mode in any environment
- Do NOT skip validation steps to save time
- Do NOT introduce framework or library outside the approved stack in MASTER_PLAN.md section 2
- Do NOT modify the 10-layer weights without documenting reasoning
- Do NOT cache predictions longer than 110 seconds (Redis TTL)
- Do NOT use any UI elements not specified in the reference screenshots

## When Stuck

1. Re-read the relevant section of `MASTER_PLAN.md`
2. Check the reference screenshots in `docs/references/`
3. Match against the exact data contracts in section 12 of MASTER_PLAN.md
4. If still unclear, ask the user — do not invent specs

## Validation Checklist Per Phase

Before declaring a phase complete, verify:
- [ ] All modules in scope are implemented
- [ ] Tests pass with >80% coverage
- [ ] UI matches reference screenshots pixel-perfect
- [ ] No paid API calls made
- [ ] No live trading code paths active
- [ ] Audit trail logging working
- [ ] Docker compose up brings everything online cleanly
- [ ] Documentation updated in `docs/`

## Reference Files

- `MASTER_PLAN.md` — Complete architecture and requirements
- `docs/references/tab1.png` — Tab 1 UI reference
- `docs/references/tab3.png` — Tab 3 scanner UI reference
- `.env.example` — Environment variables template
- `docker-compose.yml` — Service orchestration

---

**Remember:** This is a research and analysis platform first, a trading platform second. The goal is to build an institutional-grade analytical tool for manual trading decisions. The bot's job is to learn, not to trade.
