"""Curated YAML writer, Typst PDF compilation, and page-fitting trimmer.

Takes a validated ``CurationResult`` and ``PortfolioData``, applies the
curation selections (filter, reorder, inject summary), writes per-section
YAML files, invokes Typst to compile a PDF resume, and trims content
deterministically until the PDF fits within the target page count.
"""

from __future__ import annotations

import copy
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path  # noqa: TC003 — used in dataclass field types at runtime
from typing import TYPE_CHECKING, Any

from loguru import logger

from curator.exceptions import CuratorError, RenderError
from curator.io_utils import (
    atomic_json_write,
    atomic_text_write,
    atomic_yaml_write,
    compile_typst,
    get_page_count,
    priority_sort_key,
    sort_work_chronologically,
)
from curator.models import EMPTY_INTERESTS, RENDERER_MANAGED_SECTIONS, RENDERER_SECTIONS
from curator.page_caps import (  # noqa: F401 (re-exported for back-compat)
    CERTIFICATE_FLOOR,
    EDUCATION_FLOOR,
    SKILL_GROUP_FLOOR,
    _caps_for_pages,
    _PageCaps,
    per_entry_emit_cap,
)
from curator.prompt import (
    COVER_LETTER_PROMPT_HASH,
    PROMPT_HASH,
    PROMPT_VERSION,
    SYSTEM_PROMPT_HASH,
)
from curator.rules import COVER_LETTER_WORD_MAX

# ``_PageCaps``, ``_caps_for_pages``, and ``CERTIFICATE_FLOOR`` are imported
# above and live in :mod:`curator.page_caps` so :mod:`curator.eval.report`
# can consume them without importing the renderer (avoids a circular
# dependency). The imports are re-exported via a ruff F401 suppression on
# the import statement rather than ``__all__`` so the module's true public
# API (``render``, ``RenderOutput``, ``TrimKind``, ``TrimStep``) stays
# exportable via ``from x import *``.

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from curator.client import CurationResult
    from curator.config import CuratorSettings
    from curator.models import CoverLetterCuration, PortfolioData, ResumeCuration


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


class TrimKind(Enum):
    """Types of content-trimming operations for page fitting."""

    INTERESTS = "interests"
    PROJECT_HIGHLIGHT = "project_highlight"
    PROJECT = "project"
    CERTIFICATE = "certificate"
    EDUCATION = "education"
    HIGHLIGHT = "highlight"
    SKILL_GROUP = "skill_group"


@dataclass(frozen=True)
class TrimStep:
    """A single content-trimming operation applied during page fitting.

    Attributes:
        kind: Typed action for dispatch in _apply_trim.
        description: Human-readable log string including entry IDs
            (e.g., "Removed certificate: cka").
        target_id: Work or project entry ID for highlight removal
            (identifies which entry to trim within its parent list).
            None for non-highlight operations.
        below_floor: True for the below-floor last-resort tier 8
            (highlight removal on any work position that crosses its
            ``work_position_floors`` entry). The trim loop logs a
            WARNING when one fires so the bypassed protection is
            observable.
    """

    kind: TrimKind
    description: str
    target_id: str | None = None
    below_floor: bool = False


@dataclass(frozen=True)
class RenderOutput:
    """Paths to all files produced by the renderer.

    ``jd_path`` is the ``job_description.txt`` for the API curation path.
    When the curation source is static, ``jd_path`` is ``None`` and
    ``mode_path`` points to the ``mode.txt`` descriptor instead.

    ``cover_letter_yaml_path`` and ``cover_letter_pdf_path`` are populated
    only when the curation carries a cover letter. PDF path is also
    ``None`` when ``skip_pdf`` is set.
    """

    profile_dir: Path
    pdf_path: Path | None
    curated_yaml_path: Path
    curation_log_path: Path
    jd_path: Path | None
    data_files: dict[str, Path] = field(default_factory=dict)
    skipped_ids: int = 0
    safety_net_additions: int = 0
    trim_log: list[str] = field(default_factory=list)
    page_count: int | None = None
    mode_path: Path | None = None
    cover_letter_yaml_path: Path | None = None
    cover_letter_pdf_path: Path | None = None
    safety_valve_fired: bool = False
    """True when the trim cascade exhausted ``max_trim_iterations`` without
    converging, meaning the rendered PDF may exceed ``max_pages``. Distinct
    from the convergence/page-count signal: a 2-page render under a 2-page
    budget reads ``safety_valve_fired=False``; a 2-page render under a
    1-page budget where the cascade gave up reads ``True``. Surfaces the
    "shipped what we could fit" path so downstream eval / dashboards can
    distinguish intentional 2-page output from non-converged output."""


# ---------------------------------------------------------------------------
# Selection logic
# ---------------------------------------------------------------------------


def _reorder_with_safety_net(
    portfolio_highlights: list[Any],
    ai_highlight_ids: list[str],
    *,
    cap: int | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Reorder highlights per AI ranking, appending omitted ones.

    Returns ``(reordered_highlights, missing_ids)``. ``missing_ids`` are
    the portfolio IDs that the AI omitted and that the safety net
    actually appended (cap-rejected IDs are silently dropped, so the
    caller's safety-net accounting stays accurate).

    When ``cap`` is set, ``len(ordered)`` will never exceed it; the cap
    measures **total** ordered length (AI-emitted plus safety-net
    additions), not safety-net alone. Once the cap is hit, further
    portfolio-order items are dropped without being recorded in
    ``missing_ids``. The default ``cap=None`` is the pre-cap behavior
    (append every portfolio item the AI omitted).
    """
    highlight_by_id = {h.id: h for h in portfolio_highlights}
    seen: set[str] = set()
    ordered: list[dict[str, Any]] = []

    for hid in ai_highlight_ids:
        if hid in seen:
            continue
        if cap is not None and len(ordered) >= cap:
            break
        seen.add(hid)
        h = highlight_by_id.get(hid)
        if h is not None:
            ordered.append(h.model_dump(exclude_none=True))

    missing_ids: list[str] = []
    for h in portfolio_highlights:
        if h.id in seen:
            continue
        if cap is not None and len(ordered) >= cap:
            break
        missing_ids.append(h.id)
        ordered.append(h.model_dump(exclude_none=True))

    return ordered, missing_ids


def _apply_selections(
    curation: ResumeCuration,
    portfolio: PortfolioData,
    *,
    safety_net: bool = True,
    max_pages: int | None = None,
) -> tuple[dict[str, Any], int, int]:
    """Apply curation rankings to portfolio data.

    All portfolio work entries are kept (AI ranks highlights but does not
    select entries). Education and certificates come from the portfolio in
    file order (optionally sorted by priority). Skills and projects are
    filtered and ordered per the AI's ranking.

    Args:
        curation: Validated structured output to apply.
        portfolio: Loaded portfolio.
        safety_net: When True (API path default), highlights omitted from
            ``curation.work_highlights`` are appended in portfolio order.
            Set False for the static path where omissions are intentional
            (``--max-highlights`` cap).
        max_pages: When set with ``safety_net=True``, safety-net additions
            are bounded by :func:`curator.page_caps.per_entry_emit_cap`,
            keyed on each work entry's chronological position. This keeps
            the AI's ranked subset as the authoritative ceiling so
            portfolio-order items do not silently override AI rank when
            ``work_highlight_weights`` push effective floors above the
            cap. ``None`` (default) preserves the pre-cap behavior of
            appending every AI-omitted portfolio highlight; the static
            path (``safety_net=False``) short-circuits before this cap
            applies.

    Returns:
        ``(sections, skipped_count, safety_net_additions)``.
    """
    sections: dict[str, Any] = {}
    skipped = 0
    safety_net_total = 0

    # Pre-compute chronological position for each work entry. The cap
    # in ``_reorder_with_safety_net`` must agree with the renderer's
    # ``floor_for_position`` (also chronological-position-keyed) so
    # the cap and the cascade speak the same indexing convention.
    chrono_position: dict[str, int] = {}
    if max_pages is not None and safety_net:
        sorted_work_lite = sort_work_chronologically(
            [
                {
                    "id": w.id,
                    "start_date": w.start_date,
                    "end_date": w.end_date,
                }
                for w in portfolio.work
            ]
        )
        chrono_position = {w["id"]: i for i, w in enumerate(sorted_work_lite)}

    # Work: all portfolio entries, highlights reordered per AI ranking.
    wh_by_id = {wh.work_id: wh for wh in curation.work_highlights}
    work_entries: list[dict[str, Any]] = []
    for entry in portfolio.work:
        entry_dict = entry.model_dump(exclude_none=True)
        wh = wh_by_id.get(entry.id)
        if wh is None:
            logger.error(
                "Work entry '{}' missing from AI ranking; using portfolio order",
                entry.id,
            )
            entry_dict["highlights"] = [
                h.model_dump(exclude_none=True) for h in entry.highlights
            ]
        elif safety_net:
            cap: int | None = None
            if max_pages is not None:
                cap = per_entry_emit_cap(chrono_position[entry.id], max_pages)
            ordered, missing = _reorder_with_safety_net(
                entry.highlights, wh.highlight_ids, cap=cap
            )
            if missing:
                logger.warning(
                    "Safety net engaged for work entry '{}': AI ranked {} "
                    "highlights, appending {} unranked IDs in portfolio order",
                    entry.id,
                    len(wh.highlight_ids),
                    len(missing),
                )
                safety_net_total += len(missing)
            entry_dict["highlights"] = ordered
        else:
            highlight_by_id = {h.id: h for h in entry.highlights}
            entry_dict["highlights"] = [
                highlight_by_id[hid].model_dump(exclude_none=True)
                for hid in wh.highlight_ids
                if hid in highlight_by_id
            ]
        work_entries.append(entry_dict)
    sections["work"] = sort_work_chronologically(work_entries)

    # Skills: filter keywords per group, order by AI ranking.
    skill_by_id = {s.id: s for s in portfolio.skills}
    skill_entries: list[dict[str, Any]] = []
    for sr in curation.skills:
        skill_entry = skill_by_id.get(sr.skill_id)
        if skill_entry is None:
            logger.warning("Skill group '{}' not found in portfolio", sr.skill_id)
            skipped += 1
            continue
        skill_dict = skill_entry.model_dump(exclude_none=True)
        skill_dict["keywords"] = list(sr.keywords)
        skill_entries.append(skill_dict)
    sections["skills"] = skill_entries

    # Projects: filter by AI ranking, preserve AI order. Cap each project
    # to at most 2 content bullets so the template renders at most 3 lines
    # per project (name-line + up to 2 bullets). The description takes
    # the first bullet slot when present; highlights fill any remainder.
    # Excess highlights are dropped here so they never reach the template
    # and don't waste trim-cascade iterations.
    project_by_id = {p.id: p for p in portfolio.projects}
    project_entries: list[dict[str, Any]] = []
    for pid in curation.projects:
        proj = project_by_id.get(pid)
        if proj is not None:
            pdict = proj.model_dump(exclude_none=True)
            highlight_cap = 1 if pdict.get("description") else 2
            highlights = pdict.get("highlights") or []
            dropped = max(0, len(highlights) - highlight_cap)
            if dropped > 0:
                logger.debug(
                    "Project '{}': dropped {} highlight(s) beyond {}-bullet "
                    "render cap (description={})",
                    proj.id,
                    dropped,
                    highlight_cap,
                    bool(pdict.get("description")),
                )
            pdict["highlights"] = highlights[:highlight_cap]
            project_entries.append(pdict)
        else:
            logger.warning("Project ID '{}' not found in portfolio", pid)
            skipped += 1
    sections["projects"] = project_entries

    # Certificates: all from portfolio, sorted by priority if set.
    cert_dicts = [c.model_dump(exclude_none=True) for c in portfolio.certificates]
    if any(c.priority is not None for c in portfolio.certificates):
        cert_dicts = sorted(cert_dicts, key=priority_sort_key)
    sections["certificates"] = cert_dicts

    # Education: all from portfolio, sorted by priority if set.
    edu_dicts = [e.model_dump(exclude_none=True) for e in portfolio.education]
    if any(e.priority is not None for e in portfolio.education):
        edu_dicts = sorted(edu_dicts, key=priority_sort_key)
    sections["education"] = edu_dicts

    return sections, skipped, safety_net_total


# ---------------------------------------------------------------------------
# Deterministic page-fitting trimmer
# ---------------------------------------------------------------------------
# Trim priority is hardcoded; see docs/architecture.md for rationale.
# After _apply_selections(), work entries are in reverse chronological
# order (index 0 = most recent, last = oldest). _generate_next_trim()
# scans bottom-up so the oldest entries lose highlights first, preserving
# the most recent career content.
#
# RENDERER_BEHAVIOR_INVARIANT: this trimmer preserves every portfolio
# work entry on the rendered page. On 2+-page runs, each preserved
# entry also retains at least one highlight bullet so the row is never
# a dangling header (tier 8 enforces this via the per-entry floor
# keyed on ``work_position_floors[i] > 0``). On 1-page runs,
# positions whose ``work_position_floors[i] == 0`` (positions 2+ under
# the ``(3, 3, 0, 0, 0)`` 1-page tuple) may still render as
# header-only rows (position, company, dates) so the complete
# employment timeline stays visible without consuming the tight 1-page
# budget. This is a deliberate product choice for transparency on bulk
# applications. The Tier 2 judge rubric in
# ``src/curator/eval/judge.py`` ``<conventions>`` block codifies the
# downstream "score against rendered output, do not penalize the
# AI-selected-vs-rendered gap" framing this invariant requires. Any
# change to the per-entry floor or to the conditions under which a
# header-only row may render MUST update the judge convention block in
# lockstep AND bump JUDGE_VERSION; bump PROMPT_VERSION too if
# curator-prompt language refers to it.

# ``CERTIFICATE_FLOOR``, ``_PageCaps``, and ``_caps_for_pages`` live in
# :mod:`curator.page_caps` (imported and re-exported above) so
# :mod:`curator.eval.report` can consume them without a circular import.


#: Default cascade order for the AI-controlled middle band. Used when
#: ``trim_priority`` is empty or absent; partial AI lists are completed
#: by appending whichever default-band entries the AI omitted, in the
#: order they appear here.
_DEFAULT_MIDDLE_BAND: tuple[str, ...] = (
    "project_highlights",
    "projects",
    "certificates",
    "education",
    "skill_groups",
)


def _resolve_tier_order(ai_trim_priority: Sequence[str] | None) -> list[str]:
    """Compose the cascade evaluation order.

    Pinned positions:
      - Always first: ``interests``
      - Always last: ``highlight`` (to-floor), then ``highlight_below_floor``

    The AI controls the middle band's order via ``trim_priority``. Any
    middle-band tier the AI omits is appended at the end of the middle
    band in default order, so a partial AI list still produces a
    complete cascade. Unknown entries in the AI list (defense in depth;
    schema enum prevents them) are dropped silently.
    """
    order: list[str] = ["interests"]
    if ai_trim_priority:
        ai_middle = [s for s in ai_trim_priority if s in _DEFAULT_MIDDLE_BAND]
        # Dedupe while preserving first-seen order.
        seen: set[str] = set()
        deduped: list[str] = []
        for s in ai_middle:
            if s not in seen:
                seen.add(s)
                deduped.append(s)
        appended = [s for s in _DEFAULT_MIDDLE_BAND if s not in seen]
        order.extend(deduped + appended)
    else:
        order.extend(_DEFAULT_MIDDLE_BAND)
    order.extend(["highlight", "highlight_below_floor"])
    return order


def _generate_next_trim(
    sections: dict[str, Any],
    interests: dict[str, Any] | None,
    *,
    work_position_floors: tuple[int, ...] = (3, 3, 0, 0, 0),
    certificate_floor: int = CERTIFICATE_FLOOR,
    skill_group_floor: int = SKILL_GROUP_FLOOR,
    education_floor: int = EDUCATION_FLOOR,
    trim_priority: Sequence[str] | None = None,
    work_highlight_weight_hints: Mapping[str, float] | None = None,
) -> TrimStep | None:
    """Return the next trim operation, or None if nothing left to cut.

    Evaluates from tier 1 (lowest-value) through the last-resort tier,
    returning the first applicable operation. Operations self-exhaust
    as data is removed.

    Work entries are never removed: every portfolio work entry renders
    on the output to preserve the complete employment timeline, even if
    its highlight list is drained to zero. Only highlights, not entries
    themselves, are cut.

    Skill groups are removed wholesale at tier 7, one group per
    iteration, lowest-priority group last-first. This is faster to
    converge than draining keywords one-by-one: each iteration frees a
    whole section's worth of vertical space rather than a single line
    item. Empty groups are also dropped by ``_prune_empty_sections``
    before each compile (defense in depth). Tier 7 stops at
    ``skill_group_floor`` (page-budget-aware: 4 on 1-page, 6 on 2-page,
    8 on 3+-page; see :func:`_caps_for_pages`) and the cascade falls
    through to tier 8 (below-floor work highlights) rather than
    emptying the skills section. There is no late-stage skill-group
    drain to break this floor.

    Projects are cut early in the cascade so page budget preferentially
    goes to work and skills. Their highlights drain first (tier 2), then
    tier 3 removes the lowest-ranked project wholesale (keeping at least
    2 so weight-1 and weight-2 picks always survive). Project
    descriptions ride with their project entry and are never trimmed
    independently: once a project's highlights drain to 0 the template
    renders the description as the single remaining bullet until tier 3
    cuts the whole entry.

    Certificates are trimmed bottom-up early in the cascade (tier 4)
    but ``certificate_floor`` entries are always preserved as
    load-bearing credentials. The floor is page-budget-aware: 3 on
    1-page renders, 3 on 2-page, 5 on 3+-page (see
    :func:`_caps_for_pages`). There is no late-stage cert drain to
    break this floor -- if page pressure persists after tier 7
    skill-group removal, the below-floor tier 8 fires as the final
    escape hatch rather than removing the top ``certificate_floor``
    certs.

    Tier 6 trims work highlights to ``work_position_floors[i]`` per
    position, scanning N-1..0 (oldest-first) so older roles drain
    toward their floor before the top role loses any content. The
    tuple is page-budget-aware; positions beyond its length receive
    the last value (a 6-entry portfolio under a 5-element floor tuple
    gets the last value applied to position 5). For 1-page profiles
    where positions 2+ have floor 0, tier 6 drains them fully (the
    timeline still renders as header-only rows; "ghost rows" are
    intentional on 1-page where page space cannot support a non-zero
    older-role floor). For 2+-page profiles with non-zero older-role
    floors, tier 6 stops at the floor and the cascade falls through to
    tier 7 (skill groups, which also stops at ``skill_group_floor``)
    before tier 8 (below-floor) breaks any floor.

    "Soft" floors: tier 8 is a last-resort cascade that CAN trim below
    any per-position floor once tiers 1-7 have nothing left to cut.
    Scanned bottom-up (older positions first) so the most-recent role
    is the absolute last to lose content. Each tier 8 step emits a
    WARNING via the trim loop with ``below_floor=True``.
    """
    weight_hints = work_highlight_weight_hints or {}

    def _eval_interests() -> TrimStep | None:
        if interests is None:
            return None
        hobbies = interests.get("hobbies", [])
        facts = interests.get("fun_facts", [])
        if not (hobbies or facts):
            return None
        return TrimStep(
            kind=TrimKind.INTERESTS,
            description="Removed interests section",
        )

    def _eval_project_highlights() -> TrimStep | None:
        projects = sections.get("projects", [])
        for i in range(len(projects) - 1, -1, -1):
            project_highlights = projects[i].get("highlights") or []
            if len(project_highlights) > 0:
                pid = projects[i].get("id", "unknown")
                hid = project_highlights[-1].get("id", "unknown")
                return TrimStep(
                    kind=TrimKind.PROJECT_HIGHLIGHT,
                    description=f"Removed highlight: {hid} from project: {pid}",
                    target_id=pid,
                )
        return None

    def _eval_projects() -> TrimStep | None:
        projects = sections.get("projects", [])
        if len(projects) > 2:
            pid = projects[-1].get("id", "unknown")
            return TrimStep(
                kind=TrimKind.PROJECT,
                description=f"Removed project: {pid}",
            )
        return None

    def _eval_certificates() -> TrimStep | None:
        certificates = sections.get("certificates", [])
        if len(certificates) > certificate_floor:
            cid = certificates[-1].get("id", "unknown")
            return TrimStep(
                kind=TrimKind.CERTIFICATE,
                description=f"Removed certificate: {cid}",
            )
        return None

    def _eval_education() -> TrimStep | None:
        # Mirrors the certificate / skill-group floor-check pattern.
        # ``education_floor`` is page-budget-aware via ``_PageCaps`` but
        # is currently constant 1 across budgets; sourcing it as a
        # parameter keeps the cascade structurally consistent and opens
        # the door to executive-CV profiles raising it.
        education = sections.get("education", [])
        if len(education) <= education_floor:
            return None
        eid = education[-1].get("id", "unknown")
        return TrimStep(
            kind=TrimKind.EDUCATION,
            description=f"Removed education: {eid}",
        )

    def _eval_skill_groups() -> TrimStep | None:
        # Mirror the ``_eval_certificates`` floor-check pattern: protect
        # ``skill_group_floor`` non-empty groups so the skills section
        # never renders empty under page pressure. ``_prune_empty_sections``
        # has already dropped keyword-empty groups before this point, so
        # ``len(skills)`` counts surviving non-empty groups; the floor
        # protects exactly that count.
        skills = sections.get("skills", [])
        if len(skills) <= skill_group_floor:
            return None
        for i in range(len(skills) - 1, -1, -1):
            if skills[i].get("keywords"):
                sid = skills[i].get("id", "unknown")
                return TrimStep(
                    kind=TrimKind.SKILL_GROUP,
                    description=f"Removed skill group: {sid}",
                    target_id=sid,
                )
        return None

    def _eval_work_highlights_to_floor() -> TrimStep | None:
        # Per-position floors are scaled by AI-emitted weights when
        # present. Weight 1.0 (default) means unchanged; >1 keeps more
        # highlights from this role, <1 keeps fewer. ``max(0, ...)``
        # protects against pathological weights driving the effective
        # floor negative.
        work = sections.get("work", [])
        floors_len = len(work_position_floors)
        last_floor = work_position_floors[-1] if floors_len > 0 else 0
        for i in range(len(work) - 1, -1, -1):
            base_floor = work_position_floors[i] if i < floors_len else last_floor
            wid = work[i].get("id", "unknown")
            weight = weight_hints.get(wid, 1.0)
            effective_floor = max(0, round(base_floor * weight))
            highlights = work[i].get("highlights", [])
            if len(highlights) > effective_floor:
                hid = highlights[-1].get("id", "unknown")
                return TrimStep(
                    kind=TrimKind.HIGHLIGHT,
                    description=f"Removed highlight: {hid} from work entry: {wid}",
                    target_id=wid,
                )
        return None

    def _eval_work_highlights_below_floor() -> TrimStep | None:
        # Per-entry floor (RENDERER_BEHAVIOR_INVARIANT): when the
        # per-position ``base_floor`` is positive, the entry must retain
        # at least one bullet so the rendered row is never a dangling
        # header. Positions whose ``base_floor == 0`` (1-page mode
        # positions 2+ under the ``(3, 3, 0, 0, 0)`` tuple) preserve
        # the historical "header-only older role" behavior because the
        # 1-page budget was designed for that asymmetry. Position-index
        # reasoning matches tier 6 above.
        work = sections.get("work", [])
        floors_len = len(work_position_floors)
        last_floor = work_position_floors[-1] if floors_len > 0 else 0
        for i in range(len(work) - 1, -1, -1):
            highlights = work[i].get("highlights", [])
            base_floor = work_position_floors[i] if i < floors_len else last_floor
            min_keep = 1 if base_floor > 0 else 0
            if len(highlights) > min_keep:
                wid = work[i].get("id", "unknown")
                hid = highlights[-1].get("id", "unknown")
                return TrimStep(
                    kind=TrimKind.HIGHLIGHT,
                    description=f"Removed highlight: {hid} from work entry: {wid}",
                    target_id=wid,
                    below_floor=True,
                )
        return None

    evaluators = {
        "interests": _eval_interests,
        "project_highlights": _eval_project_highlights,
        "projects": _eval_projects,
        "certificates": _eval_certificates,
        "education": _eval_education,
        "skill_groups": _eval_skill_groups,
        "highlight": _eval_work_highlights_to_floor,
        "highlight_below_floor": _eval_work_highlights_below_floor,
    }
    for tier_name in _resolve_tier_order(trim_priority):
        step = evaluators[tier_name]()
        if step is not None:
            return step
    return None


def _apply_trim(
    sections: dict[str, Any],
    interests: dict[str, Any] | None,
    step: TrimStep,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Apply a trim step, returning new copies of sections and interests.

    Dispatches on ``step.kind`` (typed enum), not on description strings.
    Does not mutate the inputs. Uses deep copies to match the codebase's
    frozen-model convention.
    """
    sections = copy.deepcopy(sections)
    interests = copy.deepcopy(interests) if interests is not None else None

    match step.kind:
        case TrimKind.INTERESTS:
            interests = copy.deepcopy(EMPTY_INTERESTS)

        case TrimKind.PROJECT_HIGHLIGHT:
            projects = sections["projects"]
            for i in range(len(projects) - 1, -1, -1):
                pid = projects[i].get("id", "unknown")
                if pid == step.target_id and projects[i].get("highlights"):
                    projects[i]["highlights"] = projects[i]["highlights"][:-1]
                    break

        case TrimKind.PROJECT:
            sections["projects"] = sections["projects"][:-1]

        case TrimKind.CERTIFICATE:
            sections["certificates"] = sections["certificates"][:-1]

        case TrimKind.EDUCATION:
            sections["education"] = sections["education"][:-1]

        case TrimKind.HIGHLIGHT:
            # Invariant: ``work[*].id`` is unique (enforced upstream by
            # the portfolio loader). The bottom-up scan finds the single
            # entry matching ``target_id`` regardless of direction.
            work = sections["work"]
            for i in range(len(work) - 1, -1, -1):
                wid = work[i].get("id", "unknown")
                if wid == step.target_id and work[i].get("highlights"):
                    work[i]["highlights"] = work[i]["highlights"][:-1]
                    break

        case TrimKind.SKILL_GROUP:
            skills = sections["skills"]
            sections["skills"] = [
                g for g in skills if g.get("id", "unknown") != step.target_id
            ]

    return sections, interests


def _prune_empty_sections(
    sections: dict[str, Any],
) -> dict[str, Any]:
    """Remove empty or zero-content entries from the sections dict.

    Cleans up defects the trim cascade can leave behind:

    - Skill groups whose ``keywords`` list is empty. Tier 7 now
      removes whole groups atomically so the cascade itself never
      produces an empty-keyword group, but this guard still runs
      defensively in case upstream construction leaves one behind.
    - Empty lists for optional sections are left in place because
      the Typst template already conditionally renders them
      (``if projects.len() > 0``); the explicit list still serializes
      to an empty YAML list without rendering a heading.

    Work entries with zero highlights are intentionally preserved: the
    output always renders every portfolio work entry as a header row
    (position, company, dates) so the complete employment timeline is
    visible, even when the trim cascade has drained its highlight list.
    Note that as of the per-entry floor in tier 8 (see
    ``RENDERER_BEHAVIOR_INVARIANT``), the cascade itself no longer
    produces zero-highlight work entries on 2+-page runs except in a
    rare safety-valve overflow. A zero-highlight entry surfacing here
    on a 2+-page render therefore indicates either a non-cascade
    source (manual edit, partial reload) or that safety valve.

    Called by ``_trim_to_fit`` before each write/compile pass so the
    rendered PDF never contains a skeleton skill group with no keywords.
    Returns a new dict; does not mutate the input.
    """
    pruned: dict[str, Any] = dict(sections)

    skills = pruned.get("skills") or []
    if skills:
        pruned["skills"] = [
            s for s in skills if isinstance(s, dict) and (s.get("keywords") or [])
        ]

    return pruned


def _trim_to_fit(
    sections: dict[str, Any],
    basics: dict[str, Any],
    interests: dict[str, Any] | None,
    output_dir: Path,
    template_path: Path,
    section_order: list[str],
    *,
    max_pages: int,
    max_trim_iterations: int,
    work_position_floors: tuple[int, ...] = (3, 3, 0, 0, 0),
    certificate_floor: int = CERTIFICATE_FLOOR,
    skill_group_floor: int = SKILL_GROUP_FLOOR,
    education_floor: int = EDUCATION_FLOOR,
    trim_priority: Sequence[str] | None = None,
    work_highlight_weight_hints: Mapping[str, float] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, list[str], int, bool]:
    """Iteratively trim content until the PDF fits within max_pages.

    Writes data files, compiles Typst, checks page count, and applies
    trim steps one at a time. Each Typst compile is <1s so even 25
    iterations is fast.

    Args:
        sections: Curated section data (work, skills, etc.).
        basics: Basics dict with injected summary/label.
        interests: Interests data dict, or None.
        output_dir: Profile output directory.
        template_path: Absolute path to the Typst template.
        section_order: Section order from settings.
        max_pages: Target page count.
        max_trim_iterations: Safety valve for trim loop.
        work_position_floors: Per-position soft minimum highlights
            retained on each work entry, indexed by reverse-chronological
            position (0 = most recent). Tier 6 respects these floors;
            tier 8 bypasses them as a last resort and emits a WARNING
            when it does. Positions beyond the tuple length receive the
            last value. Defaults to short-form ``(3, 3, 0, 0, 0)``.
        certificate_floor: Hard minimum certificates preserved (top
            entries). Tier 4 never trims below this count; there is no
            bypass path. Defaults to ``CERTIFICATE_FLOOR``.
        skill_group_floor: Hard minimum skill groups preserved. Tier 7
            never trims below this count; there is no bypass path
            (skills are credible-breadth signal, not last-resort
            content). Defaults to ``SKILL_GROUP_FLOOR``.
        education_floor: Hard minimum education entries preserved.
            The cascade never trims below this count; defaults to
            ``EDUCATION_FLOOR``. Constant across page budgets today.
        trim_priority: Optional AI-emitted ordering of middle-band
            sections by drop priority. Forwarded to
            ``_generate_next_trim``.
        work_highlight_weight_hints: Optional per-work-entry
            multipliers applied to the per-position floor in tier 6.
            Forwarded to ``_generate_next_trim``.

    Returns:
        Tuple of (final_sections, final_interests, trim_log,
        page_count, safety_valve_fired). The boolean is True if the
        cascade exhausted ``max_trim_iterations`` without converging.
    """
    trim_log: list[str] = []
    pages = 0

    for iteration in range(1, max_trim_iterations + 1):
        # Drop skeleton content (0-highlight work entries, 0-keyword
        # skill groups) before rendering so the PDF never shows headings
        # with no bullets. Pre-render rather than post-render so the
        # trim loop sees the true page count after cleanup.
        sections = _prune_empty_sections(sections)

        # Write data files and layout for this iteration.
        _write_data_files(output_dir, sections, basics, interests)
        _write_layout(output_dir, section_order)

        # Compile and check page count.
        _invoke_typst(output_dir, template_path)
        pages = get_page_count(output_dir / "resume.pdf")

        if pages <= max_pages:
            if trim_log:
                logger.info(
                    "Trim converged: {} page(s) after {} trim(s)",
                    pages,
                    len(trim_log),
                )
            return sections, interests, trim_log, pages, False

        # Generate next trim operation.
        step = _generate_next_trim(
            sections,
            interests,
            work_position_floors=work_position_floors,
            certificate_floor=certificate_floor,
            skill_group_floor=skill_group_floor,
            education_floor=education_floor,
            trim_priority=trim_priority,
            work_highlight_weight_hints=work_highlight_weight_hints,
        )
        if step is None:
            logger.warning(
                "Nothing left to trim, still {} page(s) (target: {})",
                pages,
                max_pages,
            )
            # Treat "nothing left to trim" as a safety-valve event for
            # downstream observability: the rendered PDF exceeds the
            # budget and the cascade has no remaining moves, which is
            # the same operational concern as iteration exhaustion.
            return sections, interests, trim_log, pages, True

        # Observability: warn if we cross the prior default iteration
        # count (15) so pathological convergence cases surface even while
        # the loop keeps going, and warn louder when the soft floor is
        # breached because recent-role protection was bypassed.
        if iteration == 15:
            logger.warning(
                "Trim iteration count reached {} (prior default); "
                "loop still running up to max {}",
                iteration,
                max_trim_iterations,
            )
        if step.below_floor:
            logger.warning(
                "Trim crossed work_position_floors (below-floor last resort): {}",
                step.description,
            )

        logger.info("Trim {}/{}: {}", iteration, max_trim_iterations, step.description)
        trim_log.append(step.description)
        sections, interests = _apply_trim(sections, interests, step)

    # Safety valve: max iterations reached.
    logger.warning("Trim safety valve: {} iterations exhausted", max_trim_iterations)
    # Write final state and compile one last time.
    _write_data_files(output_dir, sections, basics, interests)
    _write_layout(output_dir, section_order)
    _invoke_typst(output_dir, template_path)
    pages = get_page_count(output_dir / "resume.pdf")

    return sections, interests, trim_log, pages, True


# ---------------------------------------------------------------------------
# File writers
# ---------------------------------------------------------------------------


def _make_output_dir(base_dir: Path, company_slug: str) -> Path:
    """Create the output directory for this curation run.

    Uses ``mkdir(exist_ok=False)`` + ``FileExistsError`` retry to close the
    TOCTOU window that an ``exists()``-check + ``mkdir(exist_ok=True)`` pair
    left open (CWE-367), and asserts the resulting path stays under
    *base_dir* as a belt-and-suspenders path-traversal guard (CWE-22).
    """
    base_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    dir_name = f"{date_str}-{company_slug}"

    counter = 1
    while True:
        candidate = base_dir / (dir_name if counter == 1 else f"{dir_name}-{counter}")
        try:
            candidate.mkdir(parents=False, exist_ok=False)
            break
        except FileExistsError:
            counter += 1
            continue

    resolved_base = base_dir.resolve()
    resolved_candidate = candidate.resolve()
    if not resolved_candidate.is_relative_to(resolved_base):
        msg = (
            f"Refusing to create output dir outside base: {resolved_candidate} "
            f"not under {resolved_base}"
        )
        raise RenderError(msg)

    (candidate / "data").mkdir(exist_ok=True)
    return candidate


def _write_data_files(
    output_dir: Path,
    sections: dict[str, Any],
    basics: dict[str, Any],
    interests: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """Write per-section YAML files to the data/ subdirectory.

    Every section file the Typst template loads is written -- unselected
    sections get an empty list so Typst's ``yaml()`` does not error.
    Interests are written from portfolio data (renderer-managed, not
    AI-selected).

    Returns a mapping of section names to their file paths.
    """
    data_dir = output_dir / "data"
    written: dict[str, Path] = {}

    # Basics is always written.
    basics_path = data_dir / "basics.yaml"
    atomic_yaml_write(basics_path, basics)
    written["basics"] = basics_path

    # Write selectable sections (empty list if not selected).
    for section_name in RENDERER_SECTIONS:
        path = data_dir / f"{section_name}.yaml"
        data = sections.get(section_name, [])
        atomic_yaml_write(path, data)
        written[section_name] = path

    # Write renderer-managed interests section.
    interests_data = (
        interests if interests is not None else copy.deepcopy(EMPTY_INTERESTS)
    )
    interests_path = data_dir / "interests.yaml"
    atomic_yaml_write(interests_path, interests_data)
    written["interests"] = interests_path

    return written


def _write_layout(output_dir: Path, section_order: list[str]) -> Path:
    """Write layout.yaml with section ordering.

    Appends ``RENDERER_MANAGED_SECTIONS`` after the configured selectable
    sections; renderer-managed sections always appear last on the resume.
    """
    path = output_dir / "layout.yaml"
    full_order = [*section_order, *RENDERER_MANAGED_SECTIONS]
    atomic_yaml_write(path, {"section_order": full_order})
    return path


def _write_audit_artifacts(
    output_dir: Path,
    curation: CurationResult,
    jd_text: str | None,
    *,
    trim_log: list[str] | None = None,
    max_pages: int,
) -> tuple[Path, Path, Path | None, Path | None]:
    """Write curated.yaml, curation_log.json, and per-source descriptor.

    API runs (``jd_text`` provided) write ``job_description.txt``. Static
    runs (``jd_text=None``) write ``mode.txt`` with a brief descriptor
    instead; ``curation_log.json.source`` is the authoritative provenance
    signal.

    When ``trim_log`` is provided (after the trim loop), the curation log
    is rewritten with the trim history for auditability.

    Returns:
        ``(curated_path, log_path, jd_path, mode_path)`` where exactly one of
        ``jd_path`` / ``mode_path`` is non-None per the curation source.
    """
    # Curated YAML — the full ResumeCuration for audit trail.
    curated_path = output_dir / "curated.yaml"
    atomic_yaml_write(curated_path, curation.curation.model_dump())

    # Curation log — provenance, API metadata, and trim history.
    # ``format_version`` 2.3 adds ``max_pages`` for downstream eval band
    # selection (``bands_for_pages``). 2.5 adds the optional
    # ``ai_hints.work_highlight_weights_raw`` mirror so an over-emitting
    # AI is visible in the audit trail even when the validator clamped
    # the primary field. 2.6 adds ``cache_ttl`` (the configured TTL for
    # this request) and ``cache_outcome`` (a derived signal of whether
    # the prompt cache hit, missed, or was just created), so a cost-
    # conscious operator can answer "did my 2x write pay off?" without
    # manually correlating tokens across runs. Renderer caps are
    # deterministic from ``max_pages`` via ``_caps_for_pages`` and are
    # intentionally not persisted; storing both invites drift.
    #
    # Version semantics: a minor bump (2.x -> 2.y) covers all additive
    # field surfaces shipped in the same PR. The number identifies the
    # UNION of fields present in the renderer at the moment of the
    # bump, not a per-field ratchet. Readers should use ``key in
    # log_data`` per-field probes rather than minor-version feature
    # detection. Major bumps (2.x -> 3.x) are reserved for
    # non-additive changes that require reader updates.
    log_path = output_dir / "curation_log.json"
    # cache_outcome derivation lives on CurationResult.cache_outcome so a
    # non-renderer consumer can read it without reconstructing the
    # three-branch ladder from raw token counts.
    log_data: dict[str, Any] = {
        "format_version": "2.6",
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": PROMPT_HASH,
        "system_prompt_hash": SYSTEM_PROMPT_HASH,
        "cover_letter_prompt_hash": COVER_LETTER_PROMPT_HASH,
        "source": curation.source,
        "model": curation.model,
        "input_tokens": curation.input_tokens,
        "output_tokens": curation.output_tokens,
        "cache_creation_input_tokens": curation.cache_creation_input_tokens,
        "cache_read_input_tokens": curation.cache_read_input_tokens,
        "cache_ttl": curation.cache_ttl,
        "cache_outcome": curation.cache_outcome,
        "max_pages": max_pages,
        "timestamp": datetime.now(tz=UTC).isoformat(),
    }
    if trim_log is not None:
        log_data["trim_log"] = trim_log
    # AI-emitted hints (2026-05-19): record the per-entry weights and
    # trim_priority list when the model emitted them, so post-hoc
    # debugging can correlate a profile's trim pattern with the AI's
    # JD-driven suggestions. Absent when neither field carries data.
    #
    # ``work_highlight_weights`` is the post-clamp value the renderer
    # consumed; ``work_highlight_weights_raw`` mirrors the AI's
    # pre-clamp emission so an over-emitting AI is visible even when
    # the validator clamped silently. The fields are equal when the
    # AI emitted in-range values; they diverge when any weight exceeded
    # ``[WORK_HIGHLIGHT_WEIGHT_MIN, WORK_HIGHLIGHT_WEIGHT_MAX]``.
    ai_hints: dict[str, Any] = {}
    if curation.curation.work_highlight_weights:
        ai_hints["work_highlight_weights"] = dict(
            curation.curation.work_highlight_weights
        )
    if curation.curation.work_highlight_weights_raw:
        ai_hints["work_highlight_weights_raw"] = dict(
            curation.curation.work_highlight_weights_raw
        )
    # Surface clamp drift at curate time (in addition to the audit
    # log) so an over-emitting AI is visible to the operator without
    # log-spelunking. Persistent divergence across runs suggests the
    # ``[WORK_HIGHLIGHT_WEIGHT_MIN, WORK_HIGHLIGHT_WEIGHT_MAX]`` band
    # is too narrow for the AI's natural distribution and should be
    # retuned. Fires only when at least one key was actually clamped.
    if (
        curation.curation.work_highlight_weights_raw
        and curation.curation.work_highlight_weights
        != curation.curation.work_highlight_weights_raw
    ):
        drifted = {
            wid: (raw, curation.curation.work_highlight_weights.get(wid))
            for wid, raw in curation.curation.work_highlight_weights_raw.items()
            if curation.curation.work_highlight_weights.get(wid) != raw
        }
        logger.warning(
            "work_highlight_weights clamped on {} key(s); raw->clamped: {}",
            len(drifted),
            drifted,
        )
    if curation.curation.trim_priority:
        ai_hints["trim_priority"] = list(curation.curation.trim_priority)
    if ai_hints:
        log_data["ai_hints"] = ai_hints
    if curation.cover_letter is not None:
        word_count = _cover_letter_word_count(curation.cover_letter)
        log_data["cover_letter"] = {
            "enabled": True,
            "word_count": word_count,
            "over_cap": word_count > COVER_LETTER_WORD_MAX,
        }
    else:
        log_data["cover_letter"] = {"enabled": False}
    atomic_json_write(log_path, log_data)

    # Per-source descriptor: JD text for API runs, mode.txt for static runs.
    jd_path: Path | None = None
    mode_path: Path | None = None
    if jd_text is not None:
        jd_path = output_dir / "job_description.txt"
        atomic_text_write(jd_path, jd_text)
    else:
        mode_path = output_dir / "mode.txt"
        slug = curation.curation.company_slug
        atomic_text_write(
            mode_path,
            f"source: {curation.source}\ncompany_slug: {slug}\n",
        )

    return curated_path, log_path, jd_path, mode_path


def _invoke_typst(output_dir: Path, template_path: Path) -> Path:
    """Compile the curated resume to PDF via Typst.

    Args:
        output_dir: Profile directory (used as --root for Typst path resolution).
        template_path: Absolute path to the .typ template file.

    Returns:
        Path to the compiled PDF.

    Raises:
        RenderError: If Typst is not installed, fails to compile, or times out.
    """
    pdf_path = output_dir / "resume.pdf"
    template_path = template_path.resolve()

    # Copy template into the output dir so Typst can access it under
    # its --root sandbox (Typst restricts file access to the root subtree).
    local_template = output_dir / template_path.name
    shutil.copy2(template_path, local_template)

    logger.debug("Compiling Typst template: {}", local_template)

    try:
        t0 = time.perf_counter()
        compile_typst(
            root_dir=output_dir,
            template_path=local_template,
            output_path=pdf_path,
        )
        logger.info("Typst compiled in {:.1f}s", time.perf_counter() - t0)
    except FileNotFoundError:
        msg = (
            "Typst is not installed or not on PATH. "
            "Install from https://typst.app/ or via your package manager."
        )
        raise RenderError(msg) from None
    except subprocess.TimeoutExpired as e:
        msg = f"Typst compilation timed out after {e.timeout}s"
        raise RenderError(msg) from e
    except CuratorError as e:
        raise RenderError(str(e)) from e

    logger.info("PDF compiled: {}", pdf_path)
    return pdf_path


# ---------------------------------------------------------------------------
# Cover letter artifact writer and compiler
# ---------------------------------------------------------------------------


def _count_words(text: str) -> int:
    """Count word tokens; uses the same regex as the policy validator."""
    import re

    return len(re.findall(r"\b\w+\b", text))


def _cover_letter_word_count(letter: CoverLetterCuration) -> int:
    total = _count_words(letter.opening)
    total += sum(_count_words(p) for p in letter.body_paragraphs)
    total += _count_words(letter.closing)
    return total


def _render_cover_letter(
    output_dir: Path,
    letter: CoverLetterCuration,
    template_path: Path,
    *,
    skip_pdf: bool,
) -> tuple[Path, Path | None, int | None]:
    """Write cover_letter.yaml and (optionally) compile cover_letter.pdf.

    Returns ``(yaml_path, pdf_path, page_count)``. The PDF path is None
    when ``skip_pdf`` is True. Page count is None when not compiled.

    The renderer performs a single-pass Typst compile; there is no trim
    cascade. If the rendered PDF exceeds one page, a WARNING is logged
    but the result is still returned; we never retry or re-call the API.
    """
    # Write cover_letter.yaml under data/ alongside the resume section YAMLs.
    # ``rendered_date`` is computed Python-side (not in Typst) so a rerender
    # can choose to preserve the original date by passing an existing value
    # via ``letter`` or by editing the YAML before recompile.
    payload: dict[str, Any] = {
        "salutation": letter.salutation,
        "opening": letter.opening,
        "body_paragraphs": list(letter.body_paragraphs),
        "closing": letter.closing,
        "sign_off": letter.sign_off,
        "word_count": _cover_letter_word_count(letter),
        "rendered_date": datetime.now(tz=UTC).strftime("%B %d, %Y"),
    }
    data_dir = output_dir / "data"
    data_dir.mkdir(exist_ok=True)
    yaml_path = data_dir / "cover_letter.yaml"
    atomic_yaml_write(yaml_path, payload)
    logger.info("Cover letter YAML written: {}", yaml_path.name)

    if skip_pdf:
        return yaml_path, None, None

    # Single-pass Typst compile (no trim cascade).
    pdf_path = output_dir / "cover_letter.pdf"
    template_path = template_path.resolve()
    local_template = output_dir / template_path.name
    shutil.copy2(template_path, local_template)
    try:
        t0 = time.perf_counter()
        compile_typst(
            root_dir=output_dir,
            template_path=local_template,
            output_path=pdf_path,
        )
        logger.info("Cover letter PDF compiled in {:.1f}s", time.perf_counter() - t0)
    except FileNotFoundError:
        msg = (
            "Typst is not installed or not on PATH. "
            "Install from https://typst.app/ or via your package manager."
        )
        raise RenderError(msg) from None
    except subprocess.TimeoutExpired as e:
        msg = f"Cover letter Typst compile timed out after {e.timeout}s"
        raise RenderError(msg) from e
    except CuratorError as e:
        msg = f"Cover letter Typst compile failed: {e}"
        raise RenderError(msg) from e

    page_count = get_page_count(pdf_path)
    if page_count > 1:
        logger.warning(
            "Cover letter compiled to {} pages; target is 1. Consider "
            "shortening toward {} words. Not retried.",
            page_count,
            _cover_letter_word_count(letter),
        )
    return yaml_path, pdf_path, page_count


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render(
    curation: CurationResult,
    portfolio: PortfolioData,
    jd_text: str | None,
    settings: CuratorSettings,
    *,
    skip_pdf: bool = False,
    safety_net: bool | None = None,
) -> RenderOutput:
    """Apply curation selections, produce a PDF, and trim to fit.

    When the compiled PDF exceeds ``settings.max_pages``, the renderer
    applies deterministic trim steps (removing lowest-value content first)
    and re-compiles until the PDF fits or nothing remains to trim.

    Args:
        curation: Validated curation with selections and provenance.
        portfolio: Full portfolio data from the loader.
        jd_text: Original job description text (API path) or ``None``
            (static path — a ``mode.txt`` descriptor is written instead).
        settings: Application settings (output_dir, template_path).
        skip_pdf: Skip Typst PDF compilation when True. Writes all other
            artifacts (data files, curated.yaml, curation_log.json).
        safety_net: Whether to re-append omitted highlights in portfolio
            order. When ``None`` (default), derived from ``curation.source``
            — True for ``"api"`` (trusts AI to rank correctly; safety net
            catches accidental omissions), False for ``"static"`` (honors
            ``--max-highlights`` caps verbatim). Pipelines can override.

    Returns:
        ``RenderOutput`` with paths to all generated files.
        ``pdf_path`` is ``None`` when *skip_pdf* is True.

    Raises:
        RenderError: On file I/O errors or Typst compilation failure.
    """
    rc = curation.curation

    if not skip_pdf and not settings.template_path.exists():
        msg = f"Template not found: {settings.template_path}"
        raise RenderError(msg)

    logger.info(
        "Rendering curated resume for {}{}",
        rc.company_slug,
        " (skip-pdf)" if skip_pdf else "",
    )

    resolved_safety_net = (
        safety_net if safety_net is not None else curation.source != "static"
    )

    try:
        # Create output directory.
        output_dir = _make_output_dir(settings.output_dir, rc.company_slug)

        # Apply selections to portfolio data. ``max_pages`` is forwarded
        # so the safety-net additions inside ``_reorder_with_safety_net``
        # respect ``per_entry_emit_cap`` and the AI's ranked subset stays
        # the authoritative ceiling (matches the per-entry cap the client
        # adapter already enforces on the wire side).
        sections, skipped_count, safety_net_count = _apply_selections(
            rc,
            portfolio,
            safety_net=resolved_safety_net,
            max_pages=settings.max_pages,
        )

        # Log render statistics.
        work_entries = sections.get("work", [])
        total_highlights = sum(len(e.get("highlights", [])) for e in work_entries)
        skill_entries = sections.get("skills", [])
        total_keywords = sum(len(e.get("keywords", [])) for e in skill_entries)
        populated_sections = sum(1 for s in RENDERER_SECTIONS if sections.get(s))
        logger.info(
            "Render stats: {} work entries, {} highlights, "
            "{} skill groups ({} keywords), {} populated sections",
            len(work_entries),
            total_highlights,
            len(skill_entries),
            total_keywords,
            populated_sections,
        )

        # Prepare basics with injected summary and label.
        basics_dict = portfolio.basics.model_dump(exclude_none=True)
        # AI's summary replaces the portfolio's generic basics.summary.
        basics_dict["summary"] = rc.summary
        basics_dict["label"] = rc.suggested_label

        # Prepare interests from portfolio data (renderer-managed, not AI-selected).
        interests_dict: dict[str, Any] | None = None
        if portfolio.interests is not None:
            interests_dict = portfolio.interests.model_dump(exclude_none=True)

        # Compile PDF with page-fitting trim loop.
        caps = _caps_for_pages(settings.max_pages)
        pdf_path: Path | None = None
        trim_log: list[str] = []
        final_page_count: int | None = None
        safety_valve_fired = False
        if not skip_pdf:
            (
                sections,
                interests_dict,
                trim_log,
                final_page_count,
                safety_valve_fired,
            ) = _trim_to_fit(
                sections,
                basics_dict,
                interests_dict,
                output_dir,
                settings.template_path,
                list(settings.section_order),
                max_pages=settings.max_pages,
                max_trim_iterations=settings.max_trim_iterations,
                work_position_floors=caps.work_position_floors,
                certificate_floor=caps.certificate_floor,
                skill_group_floor=caps.skill_group_floor,
                education_floor=caps.education_floor,
                trim_priority=rc.trim_priority or None,
                work_highlight_weight_hints=rc.work_highlight_weights or None,
            )
            pdf_path = output_dir / "resume.pdf"
        else:
            # No-PDF mode: write data files and layout without compiling.
            _write_data_files(output_dir, sections, basics_dict, interests_dict)
            _write_layout(output_dir, list(settings.section_order))

        # Write audit artifacts (after trimming so trim_log is persisted).
        curated_path, log_path, jd_path, mode_path = _write_audit_artifacts(
            output_dir,
            curation,
            jd_text,
            trim_log=trim_log or None,
            max_pages=settings.max_pages,
        )

        # Cover letter (if present on the curation result). Runs after the
        # resume trim cascade so it never interacts with page-fit logic.
        cover_letter_yaml_path: Path | None = None
        cover_letter_pdf_path: Path | None = None
        if curation.cover_letter is not None:
            cover_letter_yaml_path, cover_letter_pdf_path, _ = _render_cover_letter(
                output_dir,
                curation.cover_letter,
                settings.cover_letter_template_path,
                skip_pdf=skip_pdf,
            )

        # Collect final data file paths.
        data_dir = output_dir / "data"
        data_files: dict[str, Path] = {}
        for section_name in [*RENDERER_SECTIONS, "interests", "basics"]:
            p = data_dir / f"{section_name}.yaml"
            if p.exists():
                data_files[section_name] = p
        if cover_letter_yaml_path is not None:
            data_files["cover_letter"] = cover_letter_yaml_path

    except RenderError:
        raise
    except OSError as e:
        msg = f"File I/O error during rendering: {e}"
        raise RenderError(msg) from e

    logger.info("Curation output: {}", output_dir)

    return RenderOutput(
        profile_dir=output_dir,
        pdf_path=pdf_path,
        curated_yaml_path=curated_path,
        curation_log_path=log_path,
        jd_path=jd_path,
        data_files=data_files,
        skipped_ids=skipped_count,
        safety_net_additions=safety_net_count,
        trim_log=trim_log,
        page_count=final_page_count,
        mode_path=mode_path,
        cover_letter_yaml_path=cover_letter_yaml_path,
        cover_letter_pdf_path=cover_letter_pdf_path,
        safety_valve_fired=safety_valve_fired,
    )
