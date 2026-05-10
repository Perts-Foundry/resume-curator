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

    ``work_position_floors`` indexes by work-entry position
    (``index 0`` = most recent role) after
    ``_sort_work_chronologically``. Positions beyond the tuple length
    receive the last value, so a 7-entry portfolio under 2-page caps
    gets ``(8, 6, 6, 2, 2, 2, 2)`` implicitly. Any change to the work
    sort order must update this profile in lockstep.

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
        )
    if max_pages == 2:
        return _PageCaps(
            work_position_floors=(8, 6, 6, 2, 2),
            certificate_floor=3,
        )
    return _PageCaps(
        work_position_floors=(10, 8, 8, 4, 4),
        certificate_floor=5,
    )
