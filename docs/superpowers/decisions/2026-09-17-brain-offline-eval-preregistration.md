# Brain offline predictive evaluation — pre-registration

**Status: PRE-REGISTERED 2026-09-17, before the first training run.**
Every bar, split, criterion and label below was fixed before any model
was trained or any held-out trade scored. Changes after the first run
are post-hoc and must be recorded as such.

## The question, and why it is answerable

The RL brain (L10, `app/rl/`) was ruled NOT VALIDATABLE on 2026-09-08
because deploying it live changes the entry population -- an unpaired
comparison needing n in the thousands. That ruling conflated two things.
Live *deployment* is unvalidatable. The brain's *predictive value* is
measurable offline today, by exactly the method that audited L1-L6:
train on the past, score held-out future trades, split by the model's
output, compare avg_pnl_pct with n and SE per bucket.

## Phase 1 result: what the brain can see (from `app/rl/obs.py`, verified)

| dims | group | status |
|---|---|---|
| 32 | asset embedding | untested, **FROZEN random code per symbol** -- `nn.Embedding(134, 32)` is NOT in `policy.parameters()` (train_brain.py: "asset embeddings are frozen during PPO training"). No representation learning; the policy net can only memorise fixed codes |
| 6 | L1-L6 scores | tested null, n=3,865 |
| 1 | L8 | constant 0.0 (flag off -> None -> 0.0, `replay_buffer.py:200`) |
| 1 | L9 | tested null; 86.8% abstain -> 0.0 |
| 1 | atr_pct | tested null (`entry_atr`) |
| 1 | funding_rate | untested; 87% populated |
| 1 | oi_delta_24h | untested; **43% populated** (collector started 2026-08-05) |
| 5 | regime one-hot | 3 dims never 1; the other two are ONE binary (94.3% sideways / 5.7% bull) |
| 3 | position state | constant 0 (hardcoded at `replay_buffer.py:416`) |
| 2 | weekend, asia_open | tested null via `hour` (strictly finer) |

**17 of 53 dims are null or dead. The interaction hypothesis rests on
four stored features -- funding, OI, one regime bit, atr -- plus symbol
identity encoded as a FROZEN random 32-dim code.** (Corrected 2026-09-17:
the first draft said "4,288 learned parameters". The embedding is not
trained -- it is Gaussian-initialised once and frozen. The overfitting
mechanism is therefore the 128-unit policy net memorising fixed symbol
codes, not the embedding adapting. Same risk shape, different
mechanism; the neutral baseline and the reduced-embedding rule still
apply.)
The prior is weak and is stated as such. It does not gate the work:
interactions between individually-null features are precisely what a
model can find and a univariate split cannot.

## Data and split

5,602 closed shadow trades with observation components, 2026-05-16
onward, 134 symbols. **Strict temporal split** -- no shuffling, no
k-fold across time. A model that has seen the future is the most
convincing thing one can build and it means nothing.

| window | role | n |
|---|---|---|
| < 2026-08-20 | TRAIN | 3,697 |
| 2026-08-20 -> 2026-09-05 | HELD-OUT PRIMARY | ~1,300 |
| >= 2026-09-05 | HELD-OUT REPLICATION | ~600 |

Train on every eligible trade in the train window, both timeframes and
both directions -- the embedding needs every trade it can get.

## The sigmas -- state which population each describes

Two different sigmas exist and were nearly conflated. Both are real.

| sigma | value | population | what it is |
|---|---|---|---|
| **2.75%** | paired-delta SD | breakeven variant lane, 1h/LONG live-eligible | SD of (variant - base) on the SAME trade; shared path cancels most variance |
| **2.76%** | derived from SE=0.131%, n=443 | full 5-filter intersection (atr_bound + rank filters) | earlier absolute-edge calc; filters plausibly strip high-variance tails |
| **4.995%** | raw per-trade SD | 1h/LONG live-eligible with observations, n=236 | SD of pnl_pct across DIFFERENT trades |
| **3.194%** | raw per-trade SD | all closed trades with observations, n=5,602 | same, full population |

A brain evaluation compares strong vs weak BUCKETS of different trades.
The raw per-trade sigma applies, never the paired one. Reusing 2.75%
here would understate every bar by ~1.8x.

## The bars, from measured sigmas

| evaluation | held-out n | per bucket | sigma | SE_diff | 3-sigma bar |
|---|---|---|---|---|---|
| PRIMARY: all trades, Aug20-Sep05 | ~1,300 | ~650 | 3.19% | 0.177% | **0.53%** |
| SECONDARY: 1h/LONG eligible | ~88 (whole tail) | ~44 | 5.00% | 1.07% | **3.2%** |

**The 1h/LONG secondary is UNINFORMATIVE, not directional.** A 3.2%
per-trade bar against layer gaps of 0.06-0.08% cannot produce a
reading. It is reported because the operator trades that population and
should see the number, with the bar printed beside it. Its label,
verbatim, is: *"directional only in the sense that it has a sign."* The
sign is never to be quoted on its own.

## Pass criteria -- all four must hold

1. **PRIMARY separation**: strong-vs-weak avg_pnl_pct gap >= 3 x SE_diff
   on the Aug20-Sep05 held-out window, established symbols.
2. **REPLICATION is consistency, not re-significance.** On the Sep05+
   window: SAME SIGN, and the replication point estimate lies within
   2 x SE_diff(replication) of the primary point estimate. An
   independent 3-sigma bar on ~300/bucket would be ~0.78% -- it would
   reject a true 0.5% effect that cleared primary, purely from the
   smaller n. A test designed to reject its own positives is not a test.
3. **NEUTRAL BASELINE**: a randomly-initialised, untrained brain scored
   on the identical harness. If trained ~ untrained, the model learned
   nothing and the pipeline produced the number.
4. **TRAIN vs HELD-OUT gap reported every time.** Train >> held-out is
   the overfitting signature and, at 4,288 embedding parameters on
   5,602 trades, the most likely outcome. If it appears, run a second
   pass with the embedding cut to 4-8 dims. If THAT closes the gap
   without losing held-out separation, the 32 dims were memorising
   noise. Do NOT run the reduced variant pre-emptively -- let the gap
   say whether to.

## Three further disciplines, fixed now

- **New symbols in held-out are reported SEPARATELY, never silently
  excluded.** 18 symbols (215 held-out trades) have no training row and
  therefore an untrained embedding. Two numbers: established-symbol
  held-out (the model's real test) and new-symbol held-out (whether it
  generalises to symbols it has never seen, which live use requires).
- **oi_delta_24h missing values get a MISSINGNESS INDICATOR, not
  zero-fill.** The collector started 2026-08-05. Zero-fill would let the
  model read "pre-August trade" off that one dim -- a temporal leak
  wearing a feature's clothes.
- **Training starts from random initialisation.** No warm start from any
  checkpoint whose lineage touches rl_checkpoints.id=70 (the poisoned
  NaN run) or id=62 (58-dim, incompatible with OBS_DIM 53). The report
  names the initial weights.

## Phase 3 gate

Only if all four criteria hold: flip-screen -- how many entry decisions
would brain_adjust change, times max per-trade swing, against the
measurement floor and fees, exactly as L9 was screened. Then and only
then a design for live use. If Phase 2 shows nothing, it closes on the
evidence and says so plainly. That is a real answer, not a failure.

## Related: entry-timing recon f at n=55

Same document, because the same discipline applies. The pre-registered
rule (f<40% close; f>=50% design; 40-50% operator decides) does NOT
move. But binomial SE on a proportion near 0.5 at n=55 is ~6.7%, so a
reading inside 40-50% on the 8-day window is **"unresolved at n=55"**,
not a judgement call. f is reported WITH its SE. The recon accrues ~7
1h/LONG crossings/day; one more week roughly doubles n and halves the
uncertainty. The judgement branch is not invoked before that.
