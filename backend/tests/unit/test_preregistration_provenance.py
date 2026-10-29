"""Every pre-registration that states a BAR must state its σ-PROVENANCE.

WHY THIS IS A CHECK AND NOT A CONVENTION. Two documents already carried
the rule as prose:

  * 2026-09-17-brain-offline-eval-preregistration.md -- "The raw
    per-trade sigma applies, never the paired one. Reusing 2.75% here
    would understate every bar by ~1.8x."
  * 2026-10-21-phase-a-preregistration.md -- "For MFE_R and MAE_R the bar
    is computed from the realised per-cell sd, since those distributions
    have not been characterised."

Both refused a cross-population sigma import. One was made anyway nine
days later in 2026-10-26-mtf-backfill-preregistration.md: sigma = 5.12pp,
back-solved from a BARRIERED shadow-trade figure, applied to a target of
RAW forward returns. The real sigma was 24.21pp -- 4.7x -- and the arm SE
came out 25-37x the decision bar.

Prose did not stop it. A required field forces "back-solved from a
barriered-trade figure" onto the page, where it looks wrong on sight.
Same move as the socket guard and the closed allowlists, applied to
analysis instead of code.

THE GRANDFATHER SET IS CLOSED AND COUNTED, for the reason the bar-fetch
allowlist is: a list that can grow silently is a check that cannot fail.
Each entry carries its audit verdict, so a reader can tell a sound
exemption from a suspect one.
"""
from __future__ import annotations

import pathlib
import re

import pytest


DECISIONS = (
    pathlib.Path(__file__).resolve().parents[2].parent
    / "docs" / "superpowers" / "decisions"
)

# A doc is a pre-registration only if it DECLARES ITSELF one -- in its
# H1 title, or in an explicit status line. MENTIONING the word is not
# enough: every RESULT doc refers back to the pre-registration it is
# executing, and the first version of this detector matched on the bare
# word and flagged four result docs. A result reports against a bar its
# pre-registration already justified; making it restate the provenance
# would be noise, and noise is how a check gets switched off.
#
# Third variant of the same precision lesson in this repo: 'fix' inside
# 'prefix', 'pytestmark' inside a comment, and now 'pre-registration'
# inside a back-reference. Substring matchers get calibrated here, not
# trusted.
_DECLARES_PREREG = re.compile(
    r"^#\s.*pre-?registration|^\s*\*\*status:?\*\*[^\n]*pre-?registration|^\s*\*\*status:\s*pre-?registration",
    re.I | re.M,
)

# A doc states a BAR if it quotes a sigma, an SE, or a decision bar.
_STATES_A_BAR = re.compile(
    r"\bSE\b|\bsigma\b|σ|\bbar\b.{0,20}\d|\d\s*(?:sigma|σ)", re.I
)

_PROVENANCE_LINE = re.compile(
    r"^\s*\*\*(?:σ|sigma)[- ]provenance:\*\*", re.I | re.M
)

# Phrases that are NOT a provenance. Each one is a real sentence from
# this repo's history, not a hypothetical.
_REJECTED = (
    "operator working notes",
    "working notes",
    "from notes",
    "back-solved",
    "back solved",
    "assumed sigma",
    "assumed σ",
)

# ---------------------------------------------------------------------------
# CLOSED grandfather set. Pre-dates the rule; each with its audit verdict.
# Adding an entry is a DECISION, not a fix -- see the message below.
# ---------------------------------------------------------------------------
GRANDFATHERED: dict[str, str] = {
    # SOUND -- sigma measured on the target population.
    "2026-10-21-phase-a-preregistration.md":
        "SOUND: 'computed from sigma(pnl_pct) ~= 3.156, the measured LONG "
        "per-trade sd', and explicitly refuses to import for MFE_R/MAE_R",
    "2026-09-17-brain-offline-eval-preregistration.md":
        "SOUND: '## The bars, from measured sigmas', and warns against "
        "reusing the paired 2.75%",
    "2026-10-21-breakeven-reversal.md":
        "SOUND: 'at the observed armed-cell SE of 0.46% on n=60'",
    # SUSPECT -- flagged by the 2026-10-27 audit, deliberately NOT reopened.
    "2026-10-21-measurement-scope-notes.md":
        "SUSPECT: the 0.45% floor is '2 sigma at n=150 with paired sigma "
        "= 2.75%', and the doc itself says the constants 'came from "
        "operator working notes' -- no in-repo derivation from the "
        "target. NOT reopened: entry-timing closed on sigma-FREE ground "
        "(f_capped 0.354 < 0.40), and only a ~40% SMALLER paired sigma "
        "could have mattered.",
    # BROKEN -- the instance that produced this rule.
    "2026-10-26-mtf-backfill-preregistration.md":
        "BROKEN: sigma = 5.12pp back-solved from a BARRIERED shadow-trade "
        "figure and applied to RAW forward returns; real sigma 24.21pp. "
        "Left unrevised on purpose -- the stopping condition was honoured "
        "and rewriting a pre-registration after the fact is the thing "
        "pre-registration exists to prevent.",
    # Rule/decision docs that mention bars without pre-registering one.
    "2026-10-22-a0-escalation-amendment.md":
        "amendment to a pre-registration; bars inherited from #589/#590",
    "2026-10-20-btc-trend-filter-preregistration.md":
        "pre-dates the rule; closed on a sign disagreement, not a bar",
    "2026-10-26-short-overshoot-resolvability.md":
        "pre-dates the rule; its discriminator is a RATIO, not a sigma bar",
}

EXPECTED_GRANDFATHERED = 8

_ARGUMENT = (
    "\n\nA PRE-REGISTRATION THAT STATES A BAR MUST STATE ITS "
    "sigma-PROVENANCE.\n\n"
    "Add a line of the form:\n\n"
    "    **sigma-provenance:** <population sigma was MEASURED on> -- "
    "<the query, table and column, or figure it came from>\n\n"
    "It must name the population and where the number came from. Two "
    "docs in this repo already carried that rule as PROSE and a "
    "cross-population import happened anyway nine days later: sigma = "
    "5.12pp taken from BARRIERED shadow trades and applied to RAW "
    "forward returns, where the real sigma was 24.21pp. The field exists "
    "so that 'back-solved from a barriered-trade figure' has to be "
    "written on the page, where it looks wrong on sight.\n\n"
    "See docs/superpowers/PREREGISTRATION_TEMPLATE.md."
)


def _prereg_docs() -> list[pathlib.Path]:
    out = []
    for p in sorted(DECISIONS.glob("*.md")):
        head = p.read_text(encoding="utf-8")[:1500]
        if _DECLARES_PREREG.search(head):
            out.append(p)
    return out


def test_the_decisions_directory_is_where_we_think_it_is() -> None:
    """Guard the guard. A wrong path would make every test below pass by
    finding nothing, which is the shape this project calls a check that
    cannot fail."""
    assert DECISIONS.is_dir(), DECISIONS
    docs = _prereg_docs()
    assert len(docs) >= 5, (
        f"only {len(docs)} pre-registration docs found under {DECISIONS} -- "
        "the discovery is probably broken, so the verdicts below mean "
        "nothing"
    )


def test_every_new_prereg_that_states_a_bar_states_its_provenance() -> None:
    """The rule. Grandfathered docs are exempt and counted separately."""
    offenders = []
    for p in _prereg_docs():
        if p.name in GRANDFATHERED:
            continue
        text = p.read_text(encoding="utf-8")
        if not _STATES_A_BAR.search(text):
            continue
        if not _PROVENANCE_LINE.search(text):
            offenders.append(p.name)
    assert not offenders, (
        "pre-registration states a bar with NO sigma-provenance line:\n  "
        + "\n  ".join(offenders) + _ARGUMENT
    )


def test_no_provenance_line_uses_a_REJECTED_phrase() -> None:
    """A provenance that says "assumed" is not a provenance.

    Applies to NEW docs only; the grandfathered ones are listed with
    their verdicts precisely because two of them would fail this.
    """
    offenders = []
    for p in _prereg_docs():
        if p.name in GRANDFATHERED:
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not _PROVENANCE_LINE.match(line):
                continue
            low = line.lower()
            for phrase in _REJECTED:
                if phrase in low:
                    offenders.append(f"{p.name}: {phrase!r} in {line.strip()}")
    assert not offenders, (
        "sigma-provenance line uses a rejected phrase:\n  "
        + "\n  ".join(offenders) + _ARGUMENT
    )


def test_the_grandfather_set_is_CLOSED() -> None:
    """Both directions, and the count is pinned.

    An open exemption list is a check that cannot fail: the next
    cross-population import would be appended to the guard rather than
    failing it, which is exactly how every instance of that family
    entered this codebase.
    """
    assert len(GRANDFATHERED) == EXPECTED_GRANDFATHERED, (
        f"{len(GRANDFATHERED)} grandfathered, "
        f"{EXPECTED_GRANDFATHERED} signed off.\n\n"
        "ADDING AN ENTRY IS A DECISION, NOT A FIX. If a pre-registration "
        "genuinely cannot state its provenance, that is a finding about "
        "the pre-registration." + _ARGUMENT
    )
    missing = [n for n in GRANDFATHERED if not (DECISIONS / n).is_file()]
    assert not missing, (
        "grandfathered entries name files that do not exist -- the list "
        f"has rotted: {missing}"
    )
    for name, verdict in GRANDFATHERED.items():
        assert verdict.strip(), f"{name}: no audit verdict recorded"


def test_the_template_exists_and_carries_the_mandatory_field() -> None:
    """The template is what a future author copies, so the field has to
    be IN it -- a rule enforced only at review time is the prose that
    already failed."""
    tpl = DECISIONS.parent / "PREREGISTRATION_TEMPLATE.md"
    assert tpl.is_file(), tpl
    text = tpl.read_text(encoding="utf-8")
    assert _PROVENANCE_LINE.search(text), (
        "the template does not contain a sigma-provenance line"
    )
    low = text.lower()
    assert "barriered" in low and "raw" in low, (
        "the template must name the BARRIERED-vs-RAW trap specifically; "
        "that is the import that produced the rule"
    )
    for phrase in ("operator working notes", "back-solved"):
        assert phrase in low, (
            f"the template must list {phrase!r} as rejected, or an author "
            "will not know it is"
        )


@pytest.mark.parametrize(
    ("sample", "should_flag"),
    [
        ("**σ-provenance:** shadow_trades.pnl_pct LONG 60d -- measured", False),
        ("**sigma-provenance:** target population, measured 2026-10-27", False),
        ("**σ-provenance:** back-solved from the operator's SE figure", True),
        ("**σ-provenance:** from operator working notes", True),
        ("**σ-provenance:** assumed sigma of 5.12pp", True),
    ],
)
def test_the_rejected_phrase_check_is_calibrated(
    sample: str, should_flag: bool,
) -> None:
    """Positive AND negative control on the phrase matcher.

    Without the negative cases the check could reject every provenance
    and nobody could satisfy it; without the positive cases it could
    reject none. The first version of a sibling guard in this repo
    matched 'fix' inside 'prefix', so substring matchers here get
    calibrated rather than trusted.
    """
    assert _PROVENANCE_LINE.match(sample), sample
    low = sample.lower()
    flagged = any(phrase in low for phrase in _REJECTED)
    assert flagged is should_flag, (sample, flagged, should_flag)


@pytest.mark.parametrize(
    ("head", "is_prereg"),
    [
        ("# Phase A -- pre-registration" + chr(10), True),
        ("# X" + chr(10) + chr(10) + "**Status: pre-registration. Written before.**", True),
        ("# X" + chr(10) + chr(10) + "**Status:** pre-registration, committed first", True),
        # RESULT docs that merely refer back -- must NOT be flagged
        ("# RESULT -- the flip screen" + chr(10) + chr(10)
         + "**Pre-registration:** 2026-10-26-foo.md, committed before.", False),
        ("# A0 result" + chr(10) + chr(10) + "Run under the pre-registration filed earlier.", False),
        ("# Some decision" + chr(10) + chr(10) + "**Status:** measured, read-only.", False),
    ],
)
def test_the_prereg_DETECTOR_is_calibrated(head: str, is_prereg: bool) -> None:
    """Positive AND negative control on the self-declaration matcher.

    The negative cases are the ones that matter: the first version
    matched the bare word and flagged four RESULT docs, which would
    have made the guard noisy enough to be deleted.
    """
    assert bool(_DECLARES_PREREG.search(head)) is is_prereg, head
