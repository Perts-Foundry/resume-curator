"""Page-budget-keyed renderer cap profile, shared by renderer and eval.

The trim cascade in :mod:`curator.renderer` and the eval bands in
:mod:`curator.eval.report` are calibration siblings: the eval rubric must
agree with the on-page geometry the renderer produces for a given page
budget. To prevent drift, both modules consume the same cap profile
defined here.

This module is import-leaf relative to the rest of ``curator``: it
depends only on the standard library and :mod:`curator.rules`
(constants only, no transitive imports of other ``curator`` modules),
so it can be imported by both :mod:`curator.renderer` and
:mod:`curator.eval.report` without a cycle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from curator.rules import WORK_HIGHLIGHT_WEIGHT_MAX

# Default certificate floor for 1-page resumes; 2+-page renders use
# ``_caps_for_pages(max_pages).certificate_floor``. Load-bearing
# credentials are preserved under page pressure within the budget-aware
# floor. The constant is retained for test-import compatibility.
CERTIFICATE_FLOOR = 3

# Default skill-group floor for 1-page resumes; 2+-page renders use
# ``_caps_for_pages(max_pages).skill_group_floor``. The floor protects
# breadth-signal skill groups under page pressure: the cascade can
# trim individual groups down to the floor but never below it. There
# is no late-stage skill-group drain to break this floor.
SKILL_GROUP_FLOOR = 4

# Default education-entry floor across all page budgets; the cascade
# never trims education below this count. Constant rather than
# page-budget-aware because the most-recent degree (or highest one
# the candidate lists) is load-bearing on every resume length. A
# future executive-CV profile may want a larger floor to expose
# multiple degrees + thesis on 3+-page renders; tracked in the
# ``_caps_for_pages`` docstring.
EDUCATION_FLOOR = 1


@dataclass(frozen=True)
class _PageCaps:
    """Internal renderer cap profile keyed on ``max_pages``.

    Consumers should pass ``max_pages`` and let :func:`_caps_for_pages`
    derive the caps; do not construct directly.

    ``work_position_floors`` indexes by work-entry position
    (``index 0`` = most recent role) after
    :func:`curator.io_utils.sort_work_chronologically`. Positions
    beyond the tuple length receive the last value, so a 7-entry
    portfolio under 2-page caps gets ``(8, 6, 6, 2, 2, 2, 2)``
    implicitly. Any change to the work sort order must update this
    profile in lockstep.

    ``skill_group_floor`` protects N skill groups under page pressure.
    The cascade's tier-7 skill-group drain stops at this floor and
    falls through to tier 8 (below-floor work) rather than emptying
    the skills section. Profile values:

      - 1-page:  4 groups
      - 2-page:  6 groups
      - 3+-page: 8 groups

    Calibration: a 2-page DevSecOps-style portfolio needs at least
    6 surviving groups for the skills inventory to remain credible
    against a JD checklist.

    ``education_floor`` is the cascade's lower bound on education
    entries. Constant across all page budgets today (the most-recent
    degree is load-bearing on every resume length); a future
    executive-CV profile may want >=2 to expose thesis + degree.

    Per-project bullet cap is intentionally NOT in this profile:
    ``ResumeCuration.projects`` is an ordered list of project IDs only,
    so the AI does not rank highlights *within* a project. Per-project
    highlight order comes from the portfolio. Raising the cap above the
    constant 2 would surface portfolio-position-2 content rather than
    JD-relevance content. The constant 2 is enforced in
    :func:`curator.renderer._apply_selections`; see ``TODO.md`` for the
    ``ProjectRanking`` schema follow-up that would unblock a higher cap.
    """

    work_position_floors: tuple[int, ...]
    certificate_floor: int
    skill_group_floor: int
    education_floor: int

    def __post_init__(self) -> None:
        if not self.work_position_floors:
            msg = "work_position_floors must be non-empty"
            raise ValueError(msg)
        if any(f < 0 for f in self.work_position_floors):
            msg = "work_position_floors values must be >= 0"
            raise ValueError(msg)
        if self.certificate_floor < 0:
            msg = "certificate_floor must be >= 0"
            raise ValueError(msg)
        if self.skill_group_floor < 0:
            msg = "skill_group_floor must be >= 0"
            raise ValueError(msg)
        if self.education_floor < 0:
            msg = "education_floor must be >= 0"
            raise ValueError(msg)

    def floor_for_position(self, position: int) -> int:
        """Return the per-position floor, falling through to the last value.

        ``position`` is 0-indexed (0 = most recent role). Indices beyond
        the tuple length return the last value so a portfolio with more
        work entries than the profile defines explicitly still gets a
        sensible floor.
        """
        if position < 0:
            msg = "position must be non-negative"
            raise ValueError(msg)
        if position < len(self.work_position_floors):
            return self.work_position_floors[position]
        return self.work_position_floors[-1]


def _caps_for_pages(max_pages: int) -> _PageCaps:
    """Return the renderer cap profile for a given page budget.

    Floors rise with the page budget. The 2-page profile introduces
    *graduated* per-position floors so older roles always render
    content (no "ghost rows"); 1-page deliberately keeps positions 2+
    at floor 0 because page space is too constrained to support a
    non-zero floor on older roles. Plateaus at ``max_pages >= 3``;
    future executive-CV calibration may add a finer profile for
    ``max_pages >= 4`` (tracked in ``TODO.md``).
    """
    if max_pages <= 1:
        return _PageCaps(
            work_position_floors=(3, 3, 0, 0, 0),
            certificate_floor=3,
            skill_group_floor=4,
            education_floor=1,
        )
    if max_pages == 2:
        return _PageCaps(
            work_position_floors=(8, 6, 6, 2, 2),
            certificate_floor=3,
            skill_group_floor=6,
            education_floor=1,
        )
    return _PageCaps(
        work_position_floors=(10, 8, 8, 4, 4),
        certificate_floor=5,
        skill_group_floor=8,
        education_floor=1,
    )


def per_entry_emit_cap(work_position: int, max_pages: int) -> int:
    """Soft cap on highlight IDs the model should emit for one work entry.

    Lives here (not in ``output_schema``) because it is part of the
    cross-module contract between the schema description (advisory,
    surfaced to the model), the client adapter (hard enforcement
    post-parse), and the renderer's ``_reorder_with_safety_net`` (which
    also caps its portfolio-order padding here so the AI's ranked subset
    is the authoritative ceiling). All three consumers import this
    single source.

    ``work_position`` is the work entry's **chronological** position
    after :func:`curator.io_utils.sort_work_chronologically` (index 0
    is the most recent role). Callers on both ends of the wire must
    compute position consistently or the cap and the cascade will
    disagree on which entry is "pos 0." Both the renderer (in
    :func:`curator.renderer._apply_selections`) and the client adapter
    (in :func:`curator.client._adapt_curation_dict`) import the same
    helper so the convention is shared by construction.

    Anthropic's structured-output keyword subset does NOT include
    ``maxItems``; the cap is communicated to the model via the
    property's ``description`` text and enforced by the client adapter.

    Formula: ``ceil(floor * WORK_HIGHLIGHT_WEIGHT_MAX)`` for the
    renderer floor at this position, clamped to a minimum of 2 so the
    model has room even on positions where the renderer's per-position
    floor is 0 (1-page mode positions 2..4). ``ceil`` is used (not
    ``round``) to avoid Python's banker's rounding edge cases and to
    always give the model a hair more headroom than the strict scale.

    Design invariant: the multiplier IS ``WORK_HIGHLIGHT_WEIGHT_MAX``
    (imported from :mod:`curator.rules`). Sourcing both from the same
    constant makes the lockstep structural rather than a comment:
    weights at the ceiling stay effective at every position
    (effective floor never exceeds the emit cap), and a future bump
    to ``WORK_HIGHLIGHT_WEIGHT_MAX`` automatically widens the cap in
    step. Diverging the two would either re-introduce portfolio-order
    silent overrides (cap multiplier > MAX) or push effective floors
    past the emit cap, making weights inert (MAX > cap multiplier).
    """
    caps = _caps_for_pages(max_pages)
    floor = caps.floor_for_position(work_position)
    return max(2, math.ceil(floor * WORK_HIGHLIGHT_WEIGHT_MAX))
