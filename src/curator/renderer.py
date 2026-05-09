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
)
from curator.models import EMPTY_INTERESTS, RENDERER_MANAGED_SECTIONS, RENDERER_SECTIONS
from curator.prompt import PROMPT_HASH, PROMPT_VERSION
from curator.rules import COVER_LETTER_WORD_MAX

if TYPE_CHECKING:
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
        below_floor: True for tiers 11-12 (last-resort work-highlight
            removal on positions 0 or 1 that crosses the
            ``recent_role_soft_floor`` protection). The trim loop logs a
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


# ---------------------------------------------------------------------------
# Selection logic
# ---------------------------------------------------------------------------


def _reorder_with_safety_net(
    portfolio_highlights: list[Any],
    ai_highlight_ids: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Reorder highlights per AI ranking, appending omitted ones.

    Returns (reordered_highlights, missing_ids). Missing IDs are highlights
    the AI omitted; they are appended in portfolio order as a safety net.
    """
    highlight_by_id = {h.id: h for h in portfolio_highlights}
    seen: set[str] = set()
    ordered: list[dict[str, Any]] = []

    for hid in ai_highlight_ids:
        if hid in seen:
            continue
        seen.add(hid)
        h = highlight_by_id.get(hid)
        if h is not None:
            ordered.append(h.model_dump(exclude_none=True))

    missing_ids: list[str] = []
    for h in portfolio_highlights:
        if h.id not in seen:
            missing_ids.append(h.id)
            ordered.append(h.model_dump(exclude_none=True))

    return ordered, missing_ids


def _parse_partial_date(raw: Any) -> tuple[int, int]:
    """Parse a portfolio date string into a ``(year, month)`` tuple for sorting.

    Accepts ``YYYY``, ``YYYY-M``, ``YYYY-MM``, ``YYYY-MM-DD``, as well as
    integer years and empty/``None`` values. Returns ``(0, 0)`` for
    anything unparseable so that empty/malformed dates sort as
    oldest-first (they still end up after real dates under ``reverse=True``
    because the rest of the values are larger).

    Using a numeric tuple instead of a lexicographic string compare
    avoids bugs on non-zero-padded months (``2022-6`` would otherwise
    sort after ``2022-12``).
    """
    if raw is None or raw == "":
        return (0, 0)
    s = str(raw).strip()
    if not s:
        return (0, 0)
    parts = s.split("-", 2)
    try:
        year = int(parts[0])
    except ValueError:
        return (0, 0)
    month = 0
    if len(parts) > 1 and parts[1]:
        try:
            month = int(parts[1])
        except ValueError:
            month = 0
    return (year, month)


def _sort_work_chronologically(
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return work entries in reverse chronological order.

    Current roles (no ``end_date``) come first, ordered by ``start_date``
    descending. Past roles follow, ordered by ``end_date`` descending
    (then ``start_date`` descending as a tiebreaker).

    Sort keys are numeric ``(year, month)`` tuples parsed via
    ``_parse_partial_date`` to handle non-zero-padded month inputs
    correctly.
    """
    current: list[dict[str, Any]] = []
    past: list[dict[str, Any]] = []
    for entry in entries:
        end_date = entry.get("end_date") or ""
        if end_date:
            past.append(entry)
        else:
            current.append(entry)
    current.sort(
        key=lambda e: _parse_partial_date(e.get("start_date")),
        reverse=True,
    )
    past.sort(
        key=lambda e: (
            _parse_partial_date(e.get("end_date")),
            _parse_partial_date(e.get("start_date")),
        ),
        reverse=True,
    )
    return current + past


def _apply_selections(
    curation: ResumeCuration,
    portfolio: PortfolioData,
    *,
    safety_net: bool = True,
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

    Returns:
        ``(sections, skipped_count, safety_net_additions)``.
    """
    sections: dict[str, Any] = {}
    skipped = 0
    safety_net_total = 0

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
            ordered, missing = _reorder_with_safety_net(
                entry.highlights, wh.highlight_ids
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
    sections["work"] = _sort_work_chronologically(work_entries)

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
# work entry on the rendered page, even when its highlight list is
# drained to zero. Older roles render as header-only rows (position,
# company, dates) so the complete employment timeline stays visible.
# This is a deliberate product choice for transparency on bulk
# applications. The Tier 2 judge rubric in
# ``src/curator/eval/judge.py`` ``<conventions>`` block codifies the
# downstream "score against rendered output, do not penalize the
# AI-selected-vs-rendered gap" framing this invariant requires. Any
# change to the empty-work-entry preservation policy here MUST update
# the judge convention block in lockstep AND bump JUDGE_VERSION; bump
# PROMPT_VERSION too if curator-prompt language refers to it.

# Default certificate floor for 1-page resumes; 2+-page renders use
# ``_caps_for_pages(max_pages).certificate_floor``. Load-bearing
# credentials are preserved under page pressure within the budget-aware
# floor. The constant is retained for test-import compatibility.
CERTIFICATE_FLOOR = 3


@dataclass(frozen=True)
class _PageCaps:
    """Internal renderer cap profile keyed on ``max_pages``.

    Consumers should pass ``max_pages`` and let :func:`render` derive the
    caps via :func:`_caps_for_pages`; do not construct directly.

    Per-project bullet cap is intentionally NOT in this profile:
    ``ResumeCuration.projects`` is an ordered list of project IDs only,
    so the AI does not rank highlights *within* a project. Per-project
    highlight order comes from the portfolio. Raising the cap above the
    constant 2 would surface portfolio-position-2 content rather than
    JD-relevance content. The constant 2 is enforced in
    :func:`_apply_selections`; see ``TODO.md`` for the ``ProjectRanking``
    schema follow-up that would unblock a higher cap.
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


def _generate_next_trim(
    sections: dict[str, Any],
    interests: dict[str, Any] | None,
    *,
    recent_role_soft_floor: int = 3,
    certificate_floor: int = CERTIFICATE_FLOOR,
) -> TrimStep | None:
    """Return the next trim operation, or None if nothing left to cut.

    Evaluates from tier 1 (lowest-value) through the last-resort tiers,
    returning the first applicable operation. Operations self-exhaust
    as data is removed.

    Work entries are never removed: every portfolio work entry renders
    on the output to preserve the complete employment timeline, even if
    its highlight list is drained to zero. Only highlights, not entries
    themselves, are cut.

    Skill groups are removed wholesale at tier 10, one group per
    iteration, lowest-priority group last-first. This is faster to
    converge than draining keywords one-by-one: each iteration frees a
    whole section's worth of vertical space rather than a single line
    item. Empty groups are also dropped by ``_prune_empty_sections``
    before each compile (defense in depth).

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
    1-page renders, 4 on 2-page, 5 on 3+-page (see
    :func:`_caps_for_pages`). There is no late-stage cert drain to
    break this floor -- if page pressure persists after tier 10
    skill-group removal, the below-floor work-highlight tiers (11-12)
    fire as the final escape hatch rather than removing the top
    ``certificate_floor`` certs.

    The two most recent work entries (positions 0 and 1 after reverse
    chronological sort) are protected by ``recent_role_soft_floor``,
    also page-budget-aware (3 on 1-page, 4 on 2-page, 5 on 3+-page).
    They keep at least that many highlights until every other trim
    avenue has been exhausted. "Soft" because tiers 11-12 are a
    last-resort cascade that CAN trim below the floor once tiers 1-10
    have nothing left to cut; the trim loop emits a WARNING when that
    happens.
    """
    # Tier 1: Remove interests section.
    if interests is not None:
        hobbies = interests.get("hobbies", [])
        facts = interests.get("fun_facts", [])
        if hobbies or facts:
            return TrimStep(
                kind=TrimKind.INTERESTS,
                description="Removed interests section",
            )

    # Tier 2: Drain project highlights bottom-up (lowest-ranked project
    # first). Allows full depletion to 0.
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

    # Tier 3: Remove the lowest-ranked project wholesale (keep at least
    # 2 so weight-1 and weight-2 picks always survive). The AI orders
    # projects by (JD fit x weight), strongest first, so ``projects[-1]``
    # is the least valuable to cut. Description rides with its project:
    # there is no separate description-drain tier — template slot 0
    # already shows the description alone once highlights=0, so the
    # description disappears only when the whole project is cut here.
    if len(projects) > 2:
        pid = projects[-1].get("id", "unknown")
        return TrimStep(
            kind=TrimKind.PROJECT,
            description=f"Removed project: {pid}",
        )

    # Tier 4: Trim certificates bottom-up, keeping the top
    # ``certificate_floor`` entries (default ``CERTIFICATE_FLOOR`` = 3).
    # The top certs are load-bearing credentials that should survive
    # any amount of page pressure; there is no later cert-drain tier,
    # so once the floor is reached the cascade skips certificates
    # permanently.
    certificates = sections.get("certificates", [])
    if len(certificates) > certificate_floor:
        cid = certificates[-1].get("id", "unknown")
        return TrimStep(
            kind=TrimKind.CERTIFICATE,
            description=f"Removed certificate: {cid}",
        )

    # Tier 5: Remove last education (only if >1 remains).
    education = sections.get("education", [])
    if len(education) > 1:
        eid = education[-1].get("id", "unknown")
        return TrimStep(
            kind=TrimKind.EDUCATION,
            description=f"Removed education: {eid}",
        )

    # Tier 6: Remove last highlight from positions 2..N-1 (keep >=1 each).
    # Positions 0 and 1 are protected from tiers 6 and 7 so the two
    # most recent roles retain their highlights longest.
    work = sections.get("work", [])
    for i in range(len(work) - 1, 1, -1):
        highlights = work[i].get("highlights", [])
        if len(highlights) > 1:
            wid = work[i].get("id", "unknown")
            hid = highlights[-1].get("id", "unknown")
            return TrimStep(
                kind=TrimKind.HIGHLIGHT,
                description=f"Removed highlight: {hid} from work entry: {wid}",
                target_id=wid,
            )

    # Tier 7: Continue removing highlights from positions 2..N-1 (allows 0).
    for i in range(len(work) - 1, 1, -1):
        highlights = work[i].get("highlights", [])
        if len(highlights) > 0:
            wid = work[i].get("id", "unknown")
            hid = highlights[-1].get("id", "unknown")
            return TrimStep(
                kind=TrimKind.HIGHLIGHT,
                description=f"Removed highlight: {hid} from work entry: {wid}",
                target_id=wid,
            )

    # Tier 8: Trim position 1 (prior role) down to recent_role_soft_floor.
    if len(work) > 1:
        highlights_1 = work[1].get("highlights", [])
        if len(highlights_1) > recent_role_soft_floor:
            wid = work[1].get("id", "unknown")
            hid = highlights_1[-1].get("id", "unknown")
            return TrimStep(
                kind=TrimKind.HIGHLIGHT,
                description=f"Removed highlight: {hid} from work entry: {wid}",
                target_id=wid,
            )

    # Tier 9: Trim position 0 (current role) down to recent_role_soft_floor.
    if len(work) > 0:
        highlights_0 = work[0].get("highlights", [])
        if len(highlights_0) > recent_role_soft_floor:
            wid = work[0].get("id", "unknown")
            hid = highlights_0[-1].get("id", "unknown")
            return TrimStep(
                kind=TrimKind.HIGHLIGHT,
                description=f"Removed highlight: {hid} from work entry: {wid}",
                target_id=wid,
            )

    # Tier 10: Remove the lowest-priority skill group wholesale
    # (lowest priority is the last entry in ``skills``). Dropping a
    # whole group per iteration frees far more vertical space than
    # one-keyword-at-a-time drain and converges the page-fit loop in
    # dramatically fewer passes. Tier runs after recent-role floor
    # trims so skill breadth is preserved until the cascade is running
    # low on options. Groups with zero keywords are skipped here (they
    # don't take page space, and ``_prune_empty_sections`` removes them).
    skills = sections.get("skills", [])
    for i in range(len(skills) - 1, -1, -1):
        if skills[i].get("keywords"):
            sid = skills[i].get("id", "unknown")
            return TrimStep(
                kind=TrimKind.SKILL_GROUP,
                description=f"Removed skill group: {sid}",
                target_id=sid,
            )

    # Tier 11: Last resort — trim position 1 below the soft floor.
    if len(work) > 1:
        highlights_1 = work[1].get("highlights", [])
        if len(highlights_1) > 0:
            wid = work[1].get("id", "unknown")
            hid = highlights_1[-1].get("id", "unknown")
            return TrimStep(
                kind=TrimKind.HIGHLIGHT,
                description=f"Removed highlight: {hid} from work entry: {wid}",
                target_id=wid,
                below_floor=True,
            )

    # Tier 12: Absolute last resort — trim position 0 below the soft floor.
    if len(work) > 0:
        highlights_0 = work[0].get("highlights", [])
        if len(highlights_0) > 0:
            wid = work[0].get("id", "unknown")
            hid = highlights_0[-1].get("id", "unknown")
            return TrimStep(
                kind=TrimKind.HIGHLIGHT,
                description=f"Removed highlight: {hid} from work entry: {wid}",
                target_id=wid,
                below_floor=True,
            )

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

    - Skill groups whose ``keywords`` list is empty. Tier 10 now
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
    recent_role_soft_floor: int = 3,
    certificate_floor: int = CERTIFICATE_FLOOR,
) -> tuple[dict[str, Any], dict[str, Any] | None, list[str], int]:
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
        recent_role_soft_floor: Soft minimum highlights retained on
            positions 0 and 1 (the two most recent roles). Tiers 8-9
            respect this floor; tiers 11-12 bypass it as a last resort
            and emit a WARNING when they do.
        certificate_floor: Hard minimum certificates preserved (top
            entries). Tier 4 never trims below this count; there is no
            bypass path. Defaults to ``CERTIFICATE_FLOOR``.

    Returns:
        Tuple of (final_sections, final_interests, trim_log, page_count).
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
            return sections, interests, trim_log, pages

        # Generate next trim operation.
        step = _generate_next_trim(
            sections,
            interests,
            recent_role_soft_floor=recent_role_soft_floor,
            certificate_floor=certificate_floor,
        )
        if step is None:
            logger.warning(
                "Nothing left to trim, still {} page(s) (target: {})",
                pages,
                max_pages,
            )
            return sections, interests, trim_log, pages

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
                "Trim crossed recent_role_soft_floor (tier 11/12 last resort): {}",
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

    return sections, interests, trim_log, pages


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
    log_path = output_dir / "curation_log.json"
    log_data: dict[str, Any] = {
        "format_version": "2.2",
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": PROMPT_HASH,
        "source": curation.source,
        "model": curation.model,
        "input_tokens": curation.input_tokens,
        "output_tokens": curation.output_tokens,
        "cache_creation_input_tokens": curation.cache_creation_input_tokens,
        "cache_read_input_tokens": curation.cache_read_input_tokens,
        "timestamp": datetime.now(tz=UTC).isoformat(),
    }
    if trim_log is not None:
        log_data["trim_log"] = trim_log
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

        # Apply selections to portfolio data.
        sections, skipped_count, safety_net_count = _apply_selections(
            rc, portfolio, safety_net=resolved_safety_net
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
        if not skip_pdf:
            sections, interests_dict, trim_log, final_page_count = _trim_to_fit(
                sections,
                basics_dict,
                interests_dict,
                output_dir,
                settings.template_path,
                list(settings.section_order),
                max_pages=settings.max_pages,
                max_trim_iterations=settings.max_trim_iterations,
                recent_role_soft_floor=caps.recent_role_soft_floor,
                certificate_floor=caps.certificate_floor,
            )
            pdf_path = output_dir / "resume.pdf"
        else:
            # No-PDF mode: write data files and layout without compiling.
            _write_data_files(output_dir, sections, basics_dict, interests_dict)
            _write_layout(output_dir, list(settings.section_order))

        # Write audit artifacts (after trimming so trim_log is persisted).
        curated_path, log_path, jd_path, mode_path = _write_audit_artifacts(
            output_dir, curation, jd_text, trim_log=trim_log or None
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
    )
