"""Page-budget-keyed renderer cap profile, shared by renderer and eval.

The trim cascade in :mod:`curator.renderer` and the eval bands in
:mod:`curator.eval.report` are calibration siblings: the eval rubric must
agree with the on-page geometry the renderer produces for a given page
budget. To prevent drift, both modules consume the same cap profile
defined here.

This module is import-leaf: it depends only on the standard library so it
can be imported by both :mod:`curator.renderer` and
:mod:`curator.eval.report` without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass

# Default certificate floor for 1-page resumes; 2+-page renders use
# ``_caps_for_pages(max_pages).certificate_floor``. Load-bearing
# credentials are preserved under page pressure within the budget-aware
# floor. The constant is retained for test-import compatibility.
CERTIFICATE_FLOOR = 3


@dataclass(frozen=True)
class _PageCaps:
    """Internal renderer cap profile keyed on ``max_pages``.

    Consumers should pass ``max_pages`` and let :func:`_caps_for_pages`
    derive the caps; do not construct directly.

    Per-project bullet cap is intentionally NOT in this profile:
    ``ResumeCuration.projects`` is an ordered list of project IDs only,
    so the AI does not rank highlights *within* a project. Per-project
    highlight order comes from the portfolio. Raising the cap above the
    constant 2 would surface portfolio-position-2 content rather than
    JD-relevance content. The constant 2 is enforced in
    :func:`curator.renderer._apply_selections`; see ``TODO.md`` for the
    ``ProjectRanking`` schema follow-up that would unblock a higher cap.
    """

    recent_role_soft_floor: int
    certificate_floor: int


def _caps_for_pages(max_pages: int) -> _PageCaps:
    """Return the renderer cap profile for a given page budget.

    Floors rise modestly with the page budget: positions 0-1 keep more
    bullet depth, and the top-N certificates carried as load-bearing grow
    in lockstep. Plateaus at ``max_pages >= 3``; future executive-CV
    calibration may add a finer profile for ``max_pages >= 4``.
    """
    if max_pages <= 1:
        return _PageCaps(recent_role_soft_floor=3, certificate_floor=3)
    if max_pages == 2:
        return _PageCaps(recent_role_soft_floor=4, certificate_floor=4)
    return _PageCaps(recent_role_soft_floor=5, certificate_floor=5)
