# Architecture

Current architecture for resume-curator. This document is kept up to date as the
project evolves.

Last updated: 2026-04-29

---

## Overview

resume-curator is a Python CLI tool that curates resume content by ranking and
prioritizing entries from a structured YAML portfolio to match specific job descriptions.
It uses the Claude API with structured outputs (Pydantic models) to rank content,
then compiles the result into a PDF via Typst.

**Core principle:** AI ranks and reorders existing content; it does not fabricate
new bullet points. The one exception is the summary section, where light tailoring
is expected. The AI produces exactly 6 fields (summary, label, slug, work highlight
rankings, skill rankings, project rankings). The renderer handles everything else:
education, certificates, interests, page fitting, and section ordering.

---

## System Context

```
                         ┌──────────────────────┐
                         │  Portfolio source     │
                         │  directory (BYO)      │
                         │                       │
                         │  YAML data per        │
                         │  docs/portfolio-      │
                         │  schema.md            │
                         └──────────┬───────────┘
                                    │ reads
                                    ▼
┌──────────┐   JD    ┌──────────────────────────┐   API    ┌──────────────┐
│   User   │ ──────► │     resume-curator       │ ───────► │ Claude API   │
│          │ ◄────── │     (this repo)           │ ◄─────── │ (Anthropic)  │
└──────────┘  PDF    └──────────────────────────┘          └──────────────┘
                                    │
                                    │ writes
                                    ▼
                         ┌──────────────────────┐
                         │  profiles/            │
                         │  per-job output       │
                         │  (curated YAML + PDF) │
                         └──────────────────────┘
```

---

## Technology Stack

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Language | Python 3.12+ | Anthropic SDK, Pydantic integration, ecosystem |
| Package manager | uv | 10-100x faster than pip, lockfile, Python management |
| CLI framework | Typer + Rich | Modern CLI UX, auto-generated help, progress/tables |
| AI model | Claude Sonnet 4.6 | Cost/quality balance for selection/ranking tasks |
| Structured output | Pydantic v2 + `messages.stream()` | Constrained decoding guarantees schema compliance |
| Data format | YAML | Portfolio source format, human-readable |
| PDF compilation | Typst | Fast, modern typesetting |
| Configuration | pydantic-settings | Type-safe, env var + .env + defaults |
| Retry logic | Anthropic SDK built-in | SDK handles 429/529 with backoff + jitter |
| Logging | Loguru | Always-on DEBUG file + console, InterceptHandler for SDK, regex redaction |
| PDF page counting | pypdf | Page-count enforcement for renderer-side trim loop |
| PDF layout analysis | pdfplumber | Eval metrics: font sizes, whitespace, text extraction |
| Clipboard access | pyperclip (optional) | `--clipboard` JD input; install with `uv sync --extra clipboard` |
| Testing | pytest | Fixtures, parametrize, plugins |
| CI | GitHub Actions | Single-job validation with PR comment reporting |

---

## Project Structure

```
src/curator/
  __init__.py         # Public helper: default_template_path() resolves the
                      #   bundled Typst template via importlib.resources
  __main__.py         # Entry point for `python -m curator`
  cli.py              # Typer commands, Rich output, JD input handling, error display
  pipeline.py         # Pipeline orchestration: load → curate once → render with trim
  models.py           # Pydantic models (ResumeCuration, WorkHighlightRanking,
                      #   CoverLetterCuration, ResumeCurationWithCoverLetter).
                      #   Also: RENDERER_SECTIONS, RENDERER_MANAGED_SECTIONS,
                      #   RENDERABLE_SECTIONS, AI_RANKED_SECTIONS, EMPTY_INTERESTS.
                      #   Public validators: validate_curation_ids,
                      #   validate_cover_letter (both run in API and static paths).
  prompt.py           # System prompt text, message construction, XML tags.
                      #   PROMPT_VERSION audit constant (logged to curation_log.json).
                      #   _COVER_LETTER_PROMPT_BLOCK appended only when
                      #   --cover-letter is on.
  loader.py           # YAML loading from portfolio-source directory.
                      #   Loads optional data/cover-letter.yaml into
                      #   PortfolioData.cover_letter when present.
  client.py           # Anthropic API wrapper, prompt caching, usage tracking.
                      #   CurationResult.source distinguishes "api" from "static";
                      #   CurationResult.cover_letter populated when bundled.
                      #   On cover-letter HARD validator failure (under-min
                      #   total, per-paragraph band violation, forbidden
                      #   content, placeholder tokens), persists the resume
                      #   to <output_dir>/curation_partial-*.yaml for recovery
                      #   via scripts/rerender.py --partial. Total word count
                      #   above COVER_LETTER_WORD_MAX is a soft warning only;
                      #   the letter still ships and no partial is written.
  renderer.py         # Curated YAML writer, Typst compilation, page-fitting trimmer.
                      #   _render_cover_letter writes data/cover_letter.yaml and
                      #   compiles cover_letter.pdf (single pass, no trim cascade).
                      #   Writes prompt_version + source into curation_log.json
                      #   (format_version "2.2", nested cover_letter sub-object).
                      #   Static runs write mode.txt instead of job_description.txt.
  static_mode.py      # Zero-API curation synthesis: synthesize_curation,
                      #   build_static_result, synthesize_cover_letter
                      #   (verbatim pass-through of PortfolioData.cover_letter).
                      #   Reused by run_static_pipeline.
  config.py           # CuratorSettings (pydantic-settings), env/file hierarchy.
                      #   max_pages 1..5, cover_letter_template_path.
  exceptions.py       # CuratorError hierarchy (incl. StaticModeError,
                      #   CurationValidationError)
  rules.py            # Shared resume quality constants (word lists, action verbs,
                      #   scoring thresholds, category weights, cover-letter
                      #   forbidden words/phrases/sign-offs/word bands). Single
                      #   source of truth for the curation prompt, the cover
                      #   letter validator, and the eval framework.
  io_utils.py         # Shared I/O: atomic file writes, YAML safe loading, PDF page
                      #   counting, Typst compilation, slugify, priority_sort_key
  templates/
    curated.typ       # Typst resume template (packaged as resource;
                      #   located via curator.default_template_path()).
    cover_letter.typ  # Typst cover letter template (single-page; located via
                      #   curator.default_cover_letter_template_path()).
  eval/
    __init__.py       # Public API: EvalContext, EvalMetricResult, EvalReport,
                      #   Tier2Report, evaluate_tier1(), evaluate_tier2(),
                      #   from_golden_case(), from_pipeline_result(),
                      #   from_profile_dir().
    _text_helpers.py  # Shared text extraction (highlight collection)
    report.py         # EvalMetricResult, EvalMetricStatus, EvalMetricValue,
                      #   EvalReport, score aggregation, EVAL_SCHEMA_VERSION
    judge.py          # Tier 2 LLM judge: JudgeResponse, Tier2Report,
                      #   evaluate_tier2(), 8-dimension rubric scoring
    content.py        # Content Density metrics (3 metrics, 10% weight)
    selection.py      # Selection Quality metrics (11 metrics, 15% weight; summary_word_count replaces has_reasoning)
    writing.py        # Writing Quality metrics (17 metrics, 25% weight)
    alignment.py      # JD Alignment metrics (6 metrics: 4 scored + 2
                      #   informational; 25% category weight). Informational
                      #   metrics are excluded from the category aggregate via
                      #   the informational flag (not via weight=0); they
                      #   retain weight=1.0 for serialization symmetry.
    dates.py          # Date & Format Consistency metrics (3 metrics, 5% weight)
    pdf.py            # PDF Output Quality metrics (11 metrics: 10 scored
                      #   + 1 informational; 15% category weight). See alignment.py
                      #   note on the informational-vs-weight=0 distinction.
                      #   evaluate_pdf accepts page_margin_pt threaded from the
                      #   template (eval/__init__.py reads via template helper).
    template.py       # Template Correctness metrics (9 metrics, 5% weight).
                      #   Also: get_uniform_page_margin_pt(template_path).
    golden.py         # Golden dataset: GoldenCase (with tier, calibration_source),
                      #   BaselineRange (with optional status for STATUS_FLIP),
                      #   _JUDGE_DIMENSION_TOLERANCES, comparison, materializer,
                      #   PDF renderer, GOLDEN_SKIP_METRICS
testing/
  jds/                # Real-world JD files for manual validation (10 fit-tiered cases)
  results/            # Eval JSON output from test runs (gitignored)
  notes.md            # Per-case human observations from JD testing
scripts/
  rerender.py         # Dev helper: re-runs the renderer against an existing
                      #   curated.yaml (no API call). Useful for iterating on
                      #   templates or the trim cascade without re-paying for
                      #   curation. With --partial, recovers a resume PDF from
                      #   a curation_partial-*.yaml side file written when a
                      #   bundled cover-letter call failed validation. Picks up
                      #   data/cover_letter.yaml automatically when present.
                      #   See "never re-run a paid API call" rule in CLAUDE.md.
```

### Module Dependencies

```
cli.py
  ├── config.py        (settings)
  ├── pipeline.py      (orchestration)
  ├── loader.py         (portfolio loading for dry-run)
  ├── models.py         (PortfolioData, ResumeCuration for display)
  ├── rules.py          (shared constants)
  ├── io_utils.py       (atomic file writes)
  ├── eval/             (eval command)
  ├── eval/golden.py    (golden dataset evaluation)
  ├── eval/judge.py     (Tier 2 judge)
  ├── eval/report.py    (eval report types)
  └── exceptions.py     (error types)

pipeline.py
  ├── client.py        (API calls)
  ├── io_utils.py      (page counting for final check)
  ├── loader.py        (portfolio data)
  ├── renderer.py      (output generation + page-fitting trimmer)
  └── exceptions.py    (RenderError)

client.py
  ├── models.py        (structured output types)
  ├── prompt.py        (message construction)
  └── exceptions.py

loader.py
  ├── io_utils.py      (YAML safe loading)
  ├── models.py
  └── exceptions.py

renderer.py
  ├── io_utils.py      (atomic writes, Typst compilation)
  ├── models.py        (RENDERER_SECTIONS, RENDERER_MANAGED_SECTIONS,
  │                     EMPTY_INTERESTS)
  ├── prompt.py        (PROMPT_VERSION for curation_log.json)
  └── exceptions.py    (CuratorError, RenderError)

prompt.py
  ├── rules.py         (word lists for prompt composition)
  ├── models.py
  └── exceptions.py

io_utils.py
  └── exceptions.py

config.py
  └── exceptions.py    (ConfigError)

models.py
  └── (standalone, no internal deps)

rules.py
  └── (standalone, no internal deps)

exceptions.py
  └── (standalone, no internal deps)
```

`cli.py` delegates orchestration to `pipeline.py` and handles only CLI presentation
(Rich tables, status spinners, error display). `pipeline.py` coordinates the full
pipeline: load portfolio, call Claude API once, render PDF with page-fitting trimming.
Dependencies flow downward.
`models.py`, `exceptions.py`, `config.py`, and `rules.py` are leaf modules with no
internal imports. `io_utils.py` depends only on `exceptions.py`.

```
eval/__init__.py
  ├── eval/content.py    (content density metrics)
  ├── eval/selection.py  (selection quality metrics)
  ├── eval/writing.py    (writing quality metrics)
  ├── eval/alignment.py  (JD alignment metrics)
  ├── eval/dates.py      (date consistency metrics)
  ├── eval/pdf.py        (PDF output metrics)
  ├── eval/template.py   (template correctness metrics)
  ├── eval/report.py     (scoring infrastructure)
  ├── eval/judge.py      (Tier 2 LLM judge)
  ├── io_utils.py        (YAML loading)
  ├── models.py          (RENDERABLE_SECTIONS, RENDERER_SECTIONS, PortfolioData, ResumeCuration)
  └── exceptions.py      (EvalError)
```

Tier 1 `eval/` modules depend only on `rules.py`, `models.py`, `io_utils.py`,
`exceptions.py` (leaf modules), and `eval/_text_helpers.py` (internal shared
helper). No coupling to `client.py`, `prompt.py`, or `pipeline.py` —
evaluation runs independently of curation. `eval/judge.py` additionally
depends on `anthropic`, `httpx` (external), `eval/report.py` (schema version),
and `config.py` (TYPE_CHECKING only for settings type).

```
eval/judge.py
  ├── anthropic          (Anthropic client, SDK exceptions)
  ├── httpx              (Timeout configuration)
  ├── yaml               (safe_dump for message serialization)
  ├── eval/report.py     (EVAL_SCHEMA_VERSION)
  ├── rules.py           (MAX_JD_LENGTH)
  ├── exceptions.py      (APIError hierarchy, EvalError)
  └── config.py          (CuratorSettings — TYPE_CHECKING only)
```

```
eval/golden.py
  ├── io_utils.py        (load_yaml_safe, atomic_yaml_write, atomic_text_write,
  │                        atomic_json_write, compile_typst)
  ├── exceptions.py      (EvalError)
  ├── curator (root)     (default_template_path)
  ├── models.py          (EMPTY_INTERESTS, RENDERABLE_SECTIONS,
  │                        RENDERER_MANAGED_SECTIONS; ResumeCuration local import)
  ├── shutil             (copy2 — template copy for PDF rendering)
  ├── subprocess         (TimeoutExpired — exception type for Typst timeout)
  ├── eval/report.py     (EvalReport — TYPE_CHECKING only)
  └── eval/judge.py      (Tier2Report — TYPE_CHECKING only)
```

`cli.py` orchestrates between `eval/__init__.py` and `eval/golden.py`
for the `--golden` flag — `golden.py` itself has no dependency on
`eval/__init__.py`.

---

## Data Flow

The pipeline has two paths that share the same renderer:

- **API path** (`curator curate`): portfolio + JD -> Claude API -> `ResumeCuration` -> renderer.
- **Static path** (`curator static`): portfolio -> deterministic synthesis -> `ResumeCuration` -> renderer (zero API call).

Both paths produce the same `CurationResult` shape, distinguished by a `source: Literal["api", "static"]` field. The renderer is unaware of the source; it just consumes the curation.

### Phase 1: "Plug in a JD, get a PDF"

```
1. User runs: curator curate [--dry-run | --no-pdf | --clipboard] <file | ->
2. cli.py loads CuratorSettings, reads JD from file, stdin (`-`), or clipboard
3. If --dry-run: display preview (portfolio stats, JD length, cost estimate) and exit (no API call)
4. cli.py calls pipeline.run_pipeline(settings, jd_text, skip_pdf=...)
5. pipeline.py: loader.py reads all YAML from portfolio-source directory
6. pipeline.py: client.py calls Claude once via messages.stream() with ResumeCuration schema
   - prompt.py constructs system prompt (with portfolio data) + user message (with JD)
   - Streaming prevents timeouts; get_final_message() returns ParsedMessage
   - Portfolio data is prompt-cached (stable across requests)
   - Job description varies per request
7. Claude returns structured ResumeCuration (summary, label, slug, work highlight rankings, skill rankings, project rankings)
8. client.py validates response (all IDs exist in portfolio, all work entries covered, keywords verbatim-match portfolio)
9. pipeline.py: renderer.py applies selections, writes per-section YAML, compiles PDF
10. renderer.py: if PDF exceeds max_pages, trims content deterministically and re-compiles
    - 12-tier trim cascade: interests > project highlights (lowest project first,
      drained to 0) > lowest-ranked project wholesale (keep >=2 so weight-1 and
      weight-2 survive) > certificates bottom-up (keep top `CERTIFICATE_FLOOR`=3)
      > education > older work highlights (positions 2+ with >1) > older work
      highlights to 0 > recent-role highlights down to `recent_role_soft_floor`
      > skill groups removed atomically (lowest-priority group first)
      > recent-role highlights below floor (last resort, position 1 then position 0)
    - Projects trim early (tiers 2-3) so the page budget preferentially goes
      to work and skills. Project descriptions ride with their entry: there is
      no separate description-drain tier, so when highlights reach 0 the template
      renders the description as the single remaining bullet until tier 3 cuts
      the whole entry. Work entries are never removed wholesale: every
      AI-selected work entry stays (as a header-only row if drained) to preserve
      the employment timeline
    - Skill groups are removed atomically at tier 10, one whole group per
      iteration (lowest-priority first). Dropping a group frees a full section
      of vertical space, which converges the page-fit loop in dramatically fewer
      iterations than the old keyword-at-a-time drain
    - Certificates trim bottom-up at tier 4 but the top `CERTIFICATE_FLOOR` (3)
      are treated as load-bearing credentials and never cut. There is no late-
      stage cert drain: once the floor is reached the cascade skips certs and
      falls through to skill-group removal and, as last resort, below-floor
      work-highlight removal
    - Positions 0 and 1 (renderer-side reverse-chron ordering) are protected by
      `recent_role_soft_floor=3` until tiers 11-12 last-resort paths fire
    - Each project renders with at most 2 content bullets (description first
      when present, then highlights filling any remainder); excess highlights
      are dropped at hydration so the cascade never wastes iterations on them
    - After the cascade, `_prune_empty_sections` drops skill groups whose
      keywords list is empty (defensive; tier 10 removes groups atomically
      so this rarely matters in practice), then re-checks page count
    - Each Typst compile is <1s; up to `max_trim_iterations` (default 150, max 200);
      a WARNING is logged if the loop crosses 15 iterations (prior default, convergence signal)
11. pipeline.py returns PipelineResult (with trim_log) to cli.py
12. cli.py displays results table and output path
```

### Phase 1b: Static Mode (Zero-API)

```
1. User runs: curator static [--name X] [--pages N] [--max-highlights M]
                              [--no-pdf | --json] [--cover-letter]
2. cli.py loads CuratorSettings (with --pages override) and forbids --json + --no-pdf
3. If --json: load portfolio, synthesize a ResumeCuration via static_mode.synthesize_curation,
   print it as JSON to stdout, exit (no profile dir written, no PDF compiled).
   When --cover-letter is also on, the JSON envelope includes the portfolio
   cover_letter verbatim.
4. cli.py calls pipeline.run_static_pipeline(settings, name=..., max_highlights=...,
   skip_pdf=..., with_cover_letter=...)
5. pipeline.py: loader.py reads portfolio YAML, including optional
   data/cover-letter.yaml into PortfolioData.cover_letter
6. pipeline.py: static_mode.build_static_result synthesizes a CurationResult deterministically:
   - summary = portfolio basics.summary verbatim (truncated to 600 if oversized)
   - suggested_label = portfolio basics.label verbatim (truncated to 60)
   - company_slug = io_utils.slugify(name)
   - work_highlights = one ranking per work entry, all highlights in portfolio order,
     capped to max_highlights when set
   - skills = one ranking per skill group, all keywords in portfolio order;
     groups with zero keywords are skipped with a WARNING
   - projects = all project IDs sorted by `weight` ascending (stable; ties preserved)
   - source = "static", model = "n/a", all token counts = 0
   - Final round-trip through ResumeCuration.model_validate and validate_curation_ids
   - If --cover-letter: cover_letter = synthesize_cover_letter(portfolio), which
     returns portfolio.cover_letter verbatim. Missing content raises
     StaticModeError. validate_cover_letter is then run; validator failures
     are wrapped as StaticModeError with a pointer to the COVER_LETTER_*
     constants in src/curator/rules.py.
7. pipeline.py: renderer.py renders via the same trim cascade as the API path.
   When cover_letter is set, _render_cover_letter writes data/cover_letter.yaml
   and runs a single-pass Typst compile to cover_letter.pdf.
8. renderer.py writes mode.txt instead of job_description.txt; curation_log.json
   carries source = "static", model = "n/a", input_tokens = 0, and a
   cover_letter sub-object: {"enabled": True, "word_count": N} when the flag
   is on, else {"enabled": False}. The key is always present.
```

### Output Structure

```
profiles/
  2026-03-14-acme-corp-sre/
    data/                     # Per-section YAML files for Typst
      basics.yaml             # Always present, with injected summary + label
      work.yaml               # All work entries with AI-reordered highlights
      skills.yaml             # AI-ranked skill groups with JD-filtered keywords
      certificates.yaml       # All certs (portfolio order, priority-sorted if set)
      education.yaml           # All education (portfolio order, priority-sorted if set)
      projects.yaml            # AI-ranked projects
      interests.yaml           # Renderer-managed interests (from portfolio, not AI)
    layout.yaml               # Fixed section order from config
    curated.typ               # Template copy (only when rendering, not --no-pdf)
    job_description.txt       # Original JD (API path only)
    mode.txt                  # source descriptor (static path only;
                              #   replaces job_description.txt)
    curated.yaml              # ResumeCuration (summary, label, slug, rankings)
    curation_log.json         # Metadata: format_version (currently "2.2"),
                              #   prompt_version, source ("api" | "static"),
                              #   model, tokens (in/out + cache), timestamp,
                              #   optional trim_log, cover_letter sub-object
                              #   (always present; {"enabled": true,
                              #   "word_count": N, "over_cap": bool} on;
                              #   {"enabled": false} off). over_cap fires
                              #   when word_count > COVER_LETTER_WORD_MAX
                              #   (the over-cap validator is a soft warn,
                              #   not a hard reject, so the letter still
                              #   ships; over_cap flags it for downstream
                              #   consumers without re-importing rules.py)
    resume.pdf                # Final compiled output (only when rendering, not --no-pdf)
    cover_letter.pdf          # Compiled cover letter (only when --cover-letter)
    data/cover_letter.yaml    # Cover letter content (only when --cover-letter)
```

---

## Structured Output Schema

```python
class WorkHighlightRanking(BaseModel):
    work_id: str                     # Portfolio work entry ID
    highlight_ids: list[str]         # ALL highlights, strongest-first for JD;
                                     # renderer trims from the bottom for page fit

class SkillRanking(BaseModel):
    skill_id: str                    # Portfolio skill group ID
    keywords: list[str]              # Verbatim subset of the portfolio group's
                                     # keywords list; validator rejects on mismatch

class ResumeCuration(BaseModel):
    summary: str              # 50-65 word tailored paragraph (must include founder mention)
    suggested_label: str      # Professional title tailored to JD (2-5 words)
    company_slug: str         # Kebab-case company name for output dir naming
    work_highlights: list[WorkHighlightRanking]  # One per portfolio work entry (all required)
    skills: list[SkillRanking]                   # Relevant groups, JD-ordered
    projects: list[str]                           # 3-5 project IDs ordered by (JD fit x weight)
```

### Portfolio signal fields

The portfolio carries two integer fields the AI and renderer use as ranking signals.
They are optional (`int | None`); absence means "no preference".

- **`ProjectEntry.weight`** (`models.py`): `1 = highest portfolio preference`. Sent
  to Claude in the serialized portfolio. The prompt instructs Claude to rank
  projects by `(JD fit x weight)` strongest-first and to keep weight-1 / weight-2
  projects in the selection unless they are genuinely unrelated to the JD.
- **`EducationEntry.priority`** and **`CertificateEntry.priority`** (`models.py`):
  Renderer-only. The renderer sorts these sections by `priority` ascending when any
  entry sets it; AI does not see or rank them.

The AI produces exactly 6 fields. Education, certificates, and interests are
renderer-managed (loaded from portfolio in file order, optionally priority-sorted).
Section order is a fixed config setting (`CuratorSettings.section_order`), not an AI decision.
The default order is: work, skills, projects, certificates, education.
The renderer always appends interests at the end.
Sections with no content are automatically hidden by the Typst template.

### Section Categories

Three constants in `models.py` partition the resume sections:

**AI-ranked sections** (`AI_RANKED_SECTIONS`): work, skills, projects.
Claude ranks highlights within each work entry, filters/orders skill keywords,
and ranks projects by JD relevance. All portfolio work entries are always
included (the AI ranks highlights, it does not select entries).

**Renderer-written sections** (`RENDERER_SECTIONS`): work, skills, projects,
certificates, education. These are the sections the renderer writes as YAML
files. Certificates and education are loaded from the portfolio in file order
(optionally sorted by `priority` field) without AI input.

**Renderer-managed sections** (`RENDERER_MANAGED_SECTIONS`): interests
(hobbies/fun facts). Not sent to the AI. Appended at the bottom of the resume
after `RENDERER_SECTIONS` and trimmed first when the page overflows. Empty
payloads default to `EMPTY_INTERESTS`.

`RENDERABLE_SECTIONS = (*RENDERER_SECTIONS, *RENDERER_MANAGED_SECTIONS)` is
the full ordered list that appears on the rendered PDF.

### Cover Letter Generation

Optional cover-letter generation is gated by `--cover-letter` on both the
`curate` and `static` commands. Defaults to off in both paths.

**Schema (composition over inheritance):**

```python
class CoverLetterCuration(BaseModel):
    salutation: str          # "Dear ...,". Always ends with a comma.
    opening: str             # 2-3 sentence company-specific hook.
    body_paragraphs: list[str]   # 2-3 STAR paragraphs, JD-ordered.
    closing: str             # Value recap + subtle CTA.
    sign_off: str            # Sincerely | Best regards | ... (no comma).

class ResumeCurationWithCoverLetter(BaseModel):
    resume: ResumeCuration
    cover_letter: CoverLetterCuration
```

`ResumeCuration` is unchanged. The wrapper is used as `output_format` only
when `with_cover_letter=True`; otherwise the existing `ResumeCuration` schema
is used. This keeps every existing consumer working without modification and
preserves prompt-cache hits on the off path.

The same `CoverLetterCuration` also serves as the portfolio-boundary model:
`PortfolioData.cover_letter: CoverLetterCuration | None` is populated by
the loader from `<portfolio>/data/cover-letter.yaml` (optional object
section) for the static path to use. One model, one validator, no drift.

**Single API call invariant:** when on, the cover letter rides through the
same `messages.stream(...)` call as the resume curation. There is never a
second paid call. A unit test
(`tests/unit/test_client.py::TestCurateSingleCallInvariant`) asserts no
billable Anthropic message method (`stream`, `create`, `count_tokens`) is
invoked more than once per `curate()`. `client.py` adds
`COVER_LETTER_MAX_TOKENS_HEADROOM` (1024) to `max_tokens` only when the flag
is on; it never mutates the cached client setting.

**Validator placement:** structural checks (sign-off membership, salutation
trailing comma, em-dash absence, paragraph count, control-char absence) live
on the Pydantic model so they fire at parse time. Policy checks (total /
per-paragraph word counts, forbidden words and phrases, salutation-specific
phrases, unfilled `[UPPERCASE]` placeholders) live in
`validate_cover_letter` and apply uniformly to both paths. Both the prompt
block and the validator read the same constants in `rules.py`
(`COVER_LETTER_FORBIDDEN_WORDS`, `COVER_LETTER_FORBIDDEN_PHRASES`,
`COVER_LETTER_VALID_SIGN_OFFS`, `COVER_LETTER_PLACEHOLDER_PATTERN`,
`COVER_LETTER_WORD_*`).

**Static path:** `synthesize_cover_letter(portfolio)` is a verbatim
pass-through of `portfolio.cover_letter` (loaded from
`<portfolio>/data/cover-letter.yaml`). It accepts no `name` parameter and
performs no assembly, no placeholder substitution, and no tailoring.
Missing content raises `StaticModeError` with a pointer to the
`COVER_LETTER_*` constants in `src/curator/rules.py`. Validator failures
at `build_static_result` time are wrapped as `StaticModeError` with the
same pointer so the candidate can find and fix the YAML. There is no
TEMPLATE banner and no `is_template` field; the rendered PDF is fully
submittable.

**Cache partitioning:** off-path system prompt is byte-identical to today.
On-path appends a new `<cover_letter_rules>` block before the cached
portfolio block, so on-path and off-path keep separate cache entries.
Toggling the flag drops the cache; additionally, Anthropic invalidates the
cache when `output_format` changes. Cache verification must be done within
one flag state. `PROMPT_VERSION` stays a pure date; flag state is recorded
separately via the `cover_letter` sub-object in `curation_log.json`.

**Failure recovery:** when `validate_cover_letter` raises a **hard**
failure on the API path (total word count below `COVER_LETTER_WORD_MIN`,
per-paragraph band violation, forbidden word/phrase, unfilled
`[UPPERCASE]` placeholder, salutation-scope forbidden phrase), the client
persists the otherwise-valid resume curation to
`<output_dir>/curation_partial-<timestamp>-<slug>-<request_id>.yaml`. Run
`uv run python scripts/rerender.py --partial <path>` to rebuild the resume
PDF without re-paying for the API call. Total word count **above**
`COVER_LETTER_WORD_MAX` is a soft warning logged via `loguru`; the letter
still ships to disk, the overshoot delta is recorded in the log line, and
`curation_log.json` captures the final `word_count` so downstream tooling
can flag overshoots post-hoc without re-importing `rules.py`.

**`max_tokens` truncation behavior:** off-path (no cover letter), a
`stop_reason == "max_tokens"` response raises `APIResponseError` and the
caller must increase `CURATOR_MAX_TOKENS` and retry (pre-existing behavior,
unchanged). On-path (`--cover-letter`), if the structured-output parser
still produced a usable object (the bundled JSON happened to close cleanly
before the budget ran out), the client logs a WARNING (with `request_id`
and the effective `max_tokens`) and returns the partial result rather than
raising. Any downstream `validate_cover_letter` failure that originates from
the truncation surfaces with a `(response was truncated at max_tokens)`
hint in the error message so the user can distinguish a content drift from
a budget cap.

**Renderer artifacts:** `_render_cover_letter` writes
`data/cover_letter.yaml` (letter fields plus `word_count`, `rendered_date`)
and runs a single Typst compile to `cover_letter.pdf`. No trim cascade
applies to the cover letter; if the PDF exceeds one page the renderer logs
a WARNING but does not retry. The Typst template is
`src/curator/templates/cover_letter.typ`, packaged via
`curator.default_cover_letter_template_path()`.

---

## Claude API Integration

### Authentication

The Anthropic API is a separate product from Claude chat subscriptions (Pro, Max, Team).
API access requires its own account and billing at [console.anthropic.com](https://console.anthropic.com).

Setup: Console → Settings → API Keys → Create Key → store in `.env` as
`CURATOR_ANTHROPIC_API_KEY=sk-ant-...`

### Structured Outputs

Structured outputs are GA on Claude Sonnet 4.6, Opus 4.6, and Haiku 4.5. No beta
header required.

`client.messages.stream()` with `output_format` is the primary integration point:

1. Define a Pydantic `BaseModel` (e.g. `ResumeCuration`)
2. Pass it as `output_format` to `messages.stream()`
3. Claude's token sampling is **constrained at generation time** to match the schema —
   output is guaranteed valid, not "ask nicely and hope"
4. The SDK auto-transforms the schema before sending (strips unsupported constraints
   like `minimum`/`maximum`/`pattern`, adds `additionalProperties: false`, moves
   constraints into field descriptions)
5. The SDK validates the response against the **original** Pydantic model post-response,
   catching anything the JSON Schema couldn't express
6. `get_final_message()` returns a `ParsedMessage[ResumeCuration]` — access the result
   via `response.parsed_output`, a typed Python object, no JSON parsing

```python
with client.messages.stream(
    model="claude-sonnet-4-6-20260217",
    max_tokens=4096,
    system=[...],
    messages=[...],
    output_format=ResumeCuration,
) as stream:
    response = stream.get_final_message()
curation: ResumeCuration = response.parsed_output
```

**Parameter migration:** `output_format` is moving to `output_config.format`. Both work
during the transition period. We use `output_format` for now (simpler, SDK handles it).
The `effort` parameter is passed via `output_config={"effort": value}` alongside
`output_format` — the SDK merges them internally.

### Prompt Architecture

The prompt is split into three content blocks across two message types so the
cacheable prefix stays maximally stable:

- **System block 1 (static instruction text):** role definition,
  `<scope_and_ownership>` (what the AI owns vs what the renderer owns),
  `<constraints>` (no-fabrication, rank-every-work-entry, verbatim-keyword rule
  for `skills.keywords`, triple-reinforced against Layer 3 hallucination; see the
  `## Curation Reliability` section in `TODO.md` for the class-of-bug fix),
  `<output_guidance>` (per-field shape in schema order), `<curation_rules>`
  (quality rules, keyword strategy). Word lists and thresholds are interpolated
  once at module load from `rules.py`: weak phrases and AI-red-flag lists via
  `render_weak_phrases_for_prompt()` / `render_ai_red_flag_*_for_prompt()`,
  summary length guidance via `render_summary_length_guidance_for_prompt()`,
  and the mandatory founder mention via `SUMMARY_MANDATORY_MENTION`. A
  prompt-injection defense clause closes the block. A module-load invariant
  `_validate_reserved_tags()` ensures every XML tag emitted by the prompt is
  covered by the input-sanitization reserved list. Fully static: no per-request
  interpolation.
- **System block 2 (cached portfolio):** serialized `PortfolioData` wrapped in
  `<portfolio_data>` tags with `cache_control: ephemeral`. Only AI-relevant
  sections are serialized: `<basics>`, `<work>`, `<skills>`, `<projects>`.
  Education, certificates, and context-only sections are omitted (renderer-managed).
- **User message:** per-request values only. `<job_description>` carries the
  untrusted JD text.

**Data first, query last:** Following Anthropic's best practice for long-context
(up to 30% quality improvement).

### Trust Boundary

JD content is treated as untrusted data. Two complementary defenses run on every
call:

1. **Input sanitization in `build_user_message`** (`src/curator/prompt.py`).
   A case-insensitive regex (`_RESERVED_DELIMITER_RE`) rejects JDs containing
   any tag-like token whose name matches a reserved system-prompt tag. The
   reserved list covers all wrappers emitted by `build_user_message`, every
   section tag in `_serialize_portfolio`, and every top-level block inside
   `_SYSTEM_PROMPT_TEXT`. An adversarial JD cannot close `</portfolio_data>`
   then open a fake `<curation_rules>` block because the match tolerates
   case, whitespace, attributes, and self-closing forms. `build_user_message`
   is the single source of truth for JD validity (empty/whitespace, length,
   delimiter); `cli.py::_read_jd_text` handles only I/O concerns (file
   existence, stdin TTY, clipboard availability, bounded reads).
2. **Anti-injection directive in the system prompt.** A trailing clause tells
   Claude that content inside `<job_description>` tags is untrusted and must
   be treated strictly as data. Combined with the structured-output schema
   (`extra="forbid"`, no free-text exfiltration fields) and constrained
   decoding, a successful injection has no usable payload channel.

The injection-hardening attack scenarios considered, layered defense
rationale, and reserved-tag invariant are documented inline in
`src/curator/prompt.py` (`_RESERVED_TAGS` / `_validate_reserved_tags`)
and exercised by `tests/unit/test_prompt.py`.

### Prompt Caching

Portfolio data (~10k tokens) is stable across requests and marked as cacheable.
The job description varies per request and is never cached.

Two cache duration tiers are available:

| Tier | Write Cost | Read Cost | Duration |
|------|-----------|-----------|----------|
| 5-minute | 1.25x base input | 0.1x base input | 5 min |
| 1-hour | 2x base input | 0.1x base input | 1 hour |

We use the **5-minute cache** (`cache_control: {"type": "ephemeral"}`). This pays off
after a single cache read (1.25x write vs 0.1x read). The 1-hour tier costs 2x to write
and only makes sense for sustained high-volume use (Phase 2 batch scoring).

Minimum cacheable size is 2,048 tokens. Portfolio data exceeds this easily (~10k tokens).

---

## Configuration

Managed via `CuratorSettings` (pydantic-settings). Priority hierarchy:

1. CLI arguments (highest)
2. Environment variables (`CURATOR_` prefix)
3. `.env` file
4. Default values (lowest)

Key settings: `anthropic_api_key` (SecretStr), `model` (pinned snapshot),
`max_tokens`, `effort` (quality tuning), `portfolio_path`, `output_dir`,
`template_path` (Typst template location),
`max_pages` (page count enforcement, default 1, range 1..5; 1-3 typical for `curate` JD-tailored output, 4-5 supports `static` multi-page resumes),
`max_trim_iterations` (renderer-side trim loop limit, default 150, max 200;
a WARNING fires when the loop crosses 15 iterations for convergence observability),
`api_max_retries`,
`allow_api_spend` (bool, default `False` — must be explicitly set to `true`
to authorize Anthropic API calls; prevents surprise charges),
`judge_model` (Tier 2 judge model, default `claude-sonnet-4-6`),
`judge_effort` (judge quality tuning).

---

## Logging & Observability

Logging uses Loguru with two sinks:

- **stderr** (always active): INFO by default, DEBUG with `--verbose`. Colored,
  human-readable format. In verbose mode, includes source location and timestamps.
- **JSON file** (always active at DEBUG): Structured JSON Lines written to
  `~/.local/state/curator/log/debug.jsonl`. Rotated at 10 MB, retained 3 days.

A regex redaction filter scrubs API keys and secret-like patterns from all log
messages (defense-in-depth alongside `SecretStr` and `diagnose=False`).

### Pipeline Observability

At INFO level, the pipeline logs:
1. **Config dump** -- model, max_tokens, effort, max_pages,
   max_trim, retries, portfolio path, output directory
2. **Portfolio summary** — entry counts per section (e.g., `work=6, skills=16, ...`)
3. **Page enforcement** — max_pages, max_trim_iterations
4. **API request** — prompt size (chars), JD size (chars), max_tokens, effort
5. **API response** — model used, stop_reason, token counts (input, output,
   cache_create, cache_read) for cost tracking
6. **Curation summary** — company slug, work entries and highlight count, skills,
   projects, certs
7. **Render statistics** — work entries, highlights, skill groups, keyword count,
   total sections
8. **Trim steps** — each trim operation logged (e.g., "Removed certificate: cka"). The `trim_log` strings in `curation_log.json` are free-form human-readable descriptions, **not a stable interface**: phrasing changes when the cascade evolves (e.g., the 2026-04-14 `KEYWORD` → `SKILL_GROUP` rename also changed the log wording). Downstream tooling that needs structured trim data should re-render or track the `TrimKind` enum, not parse the strings.
9. **Trim convergence** — page count after trimming, number of trims applied
10. **Pipeline result** — success with page count and token totals, or warning
    if page count still exceeds target after all trims exhausted
11. **Timing** — portfolio loading, API call, Typst compilation, rendering, total
    pipeline duration

### Third-Party Logger Suppression

Noisy stdlib loggers (`httpx`, `httpcore`, `anthropic`, etc.) are routed through
Loguru via `InterceptHandler`. Levels: WARNING by default, INFO when `--verbose`
(avoids request/response body floods while exposing useful SDK context).

---

## Error Handling

Custom exception hierarchy rooted in `CuratorError`:

- `ConfigError` — missing/invalid configuration (wraps Pydantic `ValidationError` from settings)
- `PortfolioError` → `PortfolioNotFoundError`, `PortfolioValidationError`
- `APISpendGuardError` — API spend not authorized (`CURATOR_ALLOW_API_SPEND` not set to `true`)
- `APIError` → `APIAuthError`, `APIRateLimitError`, `APIResponseError`, `APIRefusalError`
- `RenderError` — Typst compilation failure
- `JobDescriptionError` — unreadable JD input
- `EvalError` — evaluation framework error (metric computation, golden loading)

All exceptions are caught at the CLI boundary (`cli.py`) and displayed as
user-friendly Rich-formatted messages. Unhandled exceptions are bugs.

---

## CI/CD

GitHub Actions workflow (`.github/workflows/ci.yml`) runs on every PR and on manual
`validate` comment trigger. Single-job design.

**Checks:** ruff check, ruff format, mypy, pytest, golden eval, pip-audit, gitleaks, trufflehog.

**Features:**
- PR comment with structured validation report (pass/fail table + expandable details)
- Comment upsert (updates existing comment, no duplicates)
- Manual re-run via `validate` comment with permission check
- SHA-pinned actions, `ubuntu-24.04`, `uv sync --locked`, 15-min timeout

**Branch protection check:** `CI / validate`

### Dependabot auto-merge

A second workflow at `.github/workflows/dependabot-automerge.yml` chains off `ci.yml` via `workflow_run` and auto-merges Dependabot patch/minor PRs after CI green. Trust model: CI-green precondition + `trustedSha` threading + 4-way bot identity gate (verified signature + author + committer + PR user all == `dependabot[bot]`) + major-bump regex on title/body + SHA-pinned `pulls.merge({ sha })`. Majors stay open for human merge. The workflow uses no checkout, no secrets beyond `GITHUB_TOKEN`, no environment binding. Recovery from a bad bump is `git revert`. The trigger filter is name-based (`workflow_run: ["CI"]`), so renaming `ci.yml`'s `name:` field silently breaks auto-merge.

---

## Cost Model

Token budget: ~12k input (portfolio + JD), ~800 output.

### Per-Request Pricing (Sonnet 4.6)

| Component | First Call (cache write) | Subsequent (cache read) |
|-----------|------------------------|------------------------|
| Input (15k tokens) | 15k × $3.75/MTok = $0.056 | 15k × $0.30/MTok = $0.005 |
| Output (1k tokens) | 1k × $15/MTok = $0.015 | 1k × $15/MTok = $0.015 |
| **Total** | **~$0.07** | **~$0.02** |

### At Scale

| Scenario | Approx Cost |
|----------|-------------|
| 10 resumes in a session (1 write + 9 reads) | ~$0.25 |
| 50 resumes over a month | ~$1-2 |
| Job scoring per JD (Haiku 4.5) | ~$0.003 |

### Batch API (Phase 2)

For non-time-sensitive bulk operations (e.g. scoring dozens of job postings), the
Batch API provides a 50% discount on all token costs. Sonnet 4.6 batch pricing:
$1.50/MTok input, $7.50/MTok output.

---

## Claude API Design Decisions

This section documents key API integration choices, their rationale, and when to
reconsider. Each includes a plain-language explanation and technical details.

### Retry Strategy: SDK Built-In Retries

**In simple terms:** When an API call fails temporarily (server busy, rate limit hit),
the Anthropic SDK automatically retries a few times with increasing wait times between
attempts. We use this built-in behavior instead of adding a separate retry library.

**Technical details:** The SDK retries with exponential backoff + jitter, respects
`retry-after` headers on 429 (rate limit), and applies separate backoff for 529
(server overloaded). Default is 2 retries; we configure `max_retries=5` via
`CuratorSettings.api_max_retries`. We do NOT layer Tenacity on top — doing so
would cause double-retries (up to `SDK_retries × Tenacity_retries` attempts)
with mismatched backoff strategies.

**When to reconsider:** If we add non-Anthropic API calls (e.g., job board APIs in
Phase 2), those would use Tenacity since they lack built-in retry support.

### Model Version Pinning

**In simple terms:** Claude models get periodic updates (snapshots). We pin to a
specific snapshot so curation results stay consistent. When a new snapshot comes out,
we test it first, then deliberately update the pin.

**Technical details:** The default `CURATOR_MODEL` is the alias `claude-sonnet-4-6`,
matching the judge model default. Aliases are used for the default to keep the
out-of-the-box `curator curate` invocation working against whatever the current
sonnet-4-6 release is. Forks that want reproducibility against a specific model
release should override `CURATOR_MODEL` with a published snapshot ID
(e.g. `claude-sonnet-4-6-20260217` once published) via env var, CLI arg, or
`.env`. The trade-off is documented: aliases follow new model releases silently,
which is usually what a public-facing tool wants but is not what a frozen
benchmark wants.

### Refusal Handling

**In simple terms:** Claude can refuse to process a request if the content triggers
safety filters. Our code checks for this and gives a clear error message instead of
crashing or returning garbage.

**Technical details:** After every `get_final_message()` call, check
`response.stop_reason`. If `"refusal"`, raise `APIRefusalError` with an actionable
message. If `"max_tokens"`, raise `APIResponseError` telling the user to increase
`CURATOR_MAX_TOKENS`. These are distinct exceptions so the CLI can display specific,
helpful messages.

### Streaming

**In simple terms:** Instead of waiting for Claude to finish its entire response before
we get anything, we use streaming to keep the connection alive. For our use case we
don't show partial results — we just grab the final result at the end. This prevents
network timeouts on longer responses.

**Technical details:** Use `client.messages.stream()` with `.get_final_message()` as
the default API call pattern. Output is identical to `messages.create()` with no
event-handling code needed. The SDK requires streaming when `max_tokens > ~21,333`.
Even below this threshold, streaming prevents timeout errors on slower responses and
future-proofs against config changes.

### Reasoning Strategy: Field Ordering (not Extended Thinking)

**In simple terms:** We want Claude to think carefully about how to rank resume
content. Our approach: the response schema orders `summary` first (the largest
commitment and tone anchor), followed by simpler fields (label, slug), then the
ranking fields (work_highlights, skills, projects). Constrained decoding generates
fields in this order, so Claude's initial summary choices anchor subsequent rankings.

**Technical details:** As of the 2026-04-12 AI scope refactor, the explicit
`reasoning` field was dropped. The summary field (50-65 words) now serves as the
implicit reasoning anchor via field ordering in constrained decoding. This reduces
output tokens by ~200-400 per call. Debuggability is reduced (weird curations can't
be explained from a single artifact), mitigated by `--verbose` logging and re-running
with a different JD.

The `effort` parameter is available as a separate quality dial (`CURATOR_EFFORT` env
var). It controls overall response quality independent of thinking blocks. When `None`
(default), the parameter is omitted from the API call entirely, letting Anthropic's
server-side defaults apply (which may change between API versions; this is intentional
to benefit from upstream improvements). Set to `"low"`, `"medium"`, `"high"`, or
`"max"` to override with an explicit level.

**When to reconsider extended thinking:**
- Phase 2 complex scoring logic that weighs many competing factors
- Phase 3 multi-step pipelines with sequential reasoning chains
- If curation quality is insufficient after tuning `effort` to `"high"`

### Post-Response Validation: Three Hard Layers + Soft Validators

**In simple terms:** After Claude returns a response, we check it three ways
hard (the API ensures it's valid JSON matching our format, the SDK ensures
numbers are in range, our code ensures the selected entries actually exist in
the portfolio) plus soft Pydantic-time observability validators that log
warnings without rejecting.

**Technical details:**
1. **Grammar-level** (constrained decoding) -- guarantees JSON structure matches schema
2. **Pydantic re-validation** (SDK) -- enforces field-level constraints such as
   `min_length=1` on `SkillRanking.keywords`, `pattern` on `company_slug`,
   and `extra="forbid"` on all models. Pydantic-time **soft validators** also live
   here: `DimensionScore._mentions_curation_scope_token` logs a WARNING when a judge
   justification lacks any of the recognized curation/JD/portfolio scope tokens
   (calibration-drift signal; PII-redacted log line per CLAUDE.md, no justification
   text in the WARNING), and never rejects.
3. **Application-level** (our code) -- validates with three independent check groups:
   - **work_highlights**: unknown/duplicate/missing work_ids (hard-fail), unknown
     highlight_ids (hard-fail), partial highlight lists (WARNING, safety-net appends
     missing IDs in portfolio order)
   - **skills**: unknown skill_id (hard-fail), unknown keyword for the group
     (**soft-drop** with WARNING -- offending keyword removed from the returned
     curation, keeps paid calls usable; callers MUST consume the returned
     `ResumeCuration` rather than the original instance)
   - **projects**: unknown project ID (hard-fail); empty list is valid
   Raises a single `APIResponseError` with all accumulated hard errors. The
   skill-keyword soft-drop is deliberately recoverable: a single hallucinated
   keyword does not abort a paid call; the renderer sees only the validated set.
   Currently no semantic-retry path; see `TODO.md` `## Curation Reliability` for
   the retry-with-feedback architecture deferred to a follow-up PR.

Note: work entry and highlight ordering is NOT a Pydantic or prompt-level contract.
The renderer sorts work entries reverse chronologically (`_sort_work_chronologically`
in `renderer.py`) and preserves list order for highlights within each entry. The
`priority: int` field that previously encoded ranking was removed during the
S1 sanity-fix series; see the 2026-04-11 entry in the Design Decisions Log below.

### Error Telemetry

**In simple terms:** When an API call fails, we log a unique request ID. This is
required if we ever need to contact Anthropic support to debug an issue.

**Technical details:** Log `e.request_id` on all `anthropic.APIError` exceptions.
Available on all SDK exception types. Example:
`logger.error("API error (request_id={}): {}", e.request_id, e)`

### Token Budget

Input: ~15k tokens (portfolio ~10k + JD ~5k). Output: ~1k tokens. Well within the
1M context window. See the Cost Model section for pricing details.

### Cost Tracking

Log `response.usage` fields after every API call: `input_tokens`, `output_tokens`,
`cache_creation_input_tokens`, `cache_read_input_tokens`. Store in `curation_log.json`.

All planned work is tracked in `TODO.md` at the project root.

---

## Curation Quality vs Portfolio-JD Fit

The eval framework distinguishes two things it could measure:

1. **Curation quality** — given the candidate's portfolio, did the curator select
   the right subset, write good bullets, produce a coherent narrative? This is
   what the curator is *responsible for* and what should drive aggregate scores.
2. **Portfolio-JD fit** — does the candidate's career (the portfolio itself)
   match the JD's requirements? This is a property of the candidate, not of
   the curator. Penalizing the curator for portfolio gaps scores the wrong
   object.

**Rule**: all aggregate-contributing metrics measure curation quality.
Portfolio-fit signals remain visible as informational (`informational=True`
on `EvalMetricResult`) so they surface portfolio-expansion opportunities
without dragging curation scores. `score_category` skips informational
metrics; the dedicated `PortfolioFitReport` sidecar (emitted on every
`EvalReport.to_dict()`) aggregates the portfolio-fit subset into its own
0-100 score so the user can read "curator quality" and "candidate-JD fit"
as two distinct signals.

**Current informational metrics** (set via `informational=True`):

*Portfolio-JD fit signals (roll into `PortfolioFitReport`):*
- `alignment.jd_match_rate` — fraction of JD keywords present in the portfolio.
  A 5% match means the candidate's career doesn't cover much of the JD; the
  curator can't fix that. Status is uniformly PASS post-2026-04-27 (the
  rate value carries the actionable signal, not the status); the metric is
  scheduled to migrate to a typed `PortfolioFitReport.coverage_rate` field
  on the next `EVAL_SCHEMA_VERSION` bump (see TODO.md `[CALIBRATE-3]`).
- `alignment.acronym_expansion_pairs` — whether common JD acronyms (SRE, TLS,
  IAM, etc.) appear on the resume. Two failure paths: (a) the portfolio lacks
  the underlying work and the curator can't surface the acronym, or (b) the
  portfolio has the work but the curator failed to expand the acronym on
  first mention as the prompt rule requires (post-2026-04-28, per the
  inline acronym-expansion guidance in `prompt.py`'s Keyword strategy
  block; see also `rules.py:ACRONYM_EXPANSIONS`).

*Deferred-detection stubs (informational but NOT portfolio-fit; excluded
from `PortfolioFitReport` via the `PORTFOLIO_FIT_METRIC_NAMES` allowlist):*
- `pdf.font_embedding_valid` — Typst embeds by default; retained as a marker
  for future real detection work.

**Tier 2 LLM judge** is also scoped to curation quality via the `<scope>`
block at the top of `_RUBRIC_SYSTEM_PROMPT` in `eval/judge.py`. Score-5
anchors read "best possible given the portfolio," and the judge is
explicitly told not to lower any score for JD terms absent from the portfolio
(they are `jd_match_rate`'s concern). Keep this scoping in sync when adding
or editing dimensions.

### Versioning Policy

Any prose artifact that influences model output gets a `*_VERSION: str`
date constant *in its own module*, bumped on any textual change, paired
with a content-hash tripwire where feasible. Schema shapes get an
integer `EVAL_SCHEMA_VERSION`-style counter.

**Today's version constants**:

| Constant | Module | Type | Tripwire | Bumps |
|---|---|---|---|---|
| `PROMPT_VERSION` | `curator.prompt` | date | `PROMPT_HASH` (sha256[:12] of system + cover-letter prompts) | any prompt text edit |
| `JUDGE_VERSION` | `curator.eval.judge` | date | `JUDGE_PROMPT_HASH` (sha256[:12] of `_RUBRIC_SYSTEM_PROMPT`) | any rubric text edit |
| `EVAL_SCHEMA_VERSION` | `curator.eval.report` | int | none | additive +1 / breaking +1 (see below) |
| `format_version` | `curation_log.json` | semver-ish string | none | major.minor (additive bumps minor; breaking bumps major) |

**When to bump `EVAL_SCHEMA_VERSION`**:

- **Additive change** (new optional field, new metric, new informational
  flag, new sidecar key): +1, mark as additive in the design log entry.
  Old consumers that ignore unknown keys keep working.
- **Breaking change** (renamed metric, removed key, threshold change
  that flips a previously-PASS metric to FAIL on existing inputs): +1,
  mark as breaking in the design log, and add a migration note for
  downstream tools that read `report.json`. Schema-version mismatch is
  ERROR-severity in `compare_against_golden` since the 2026-04-26
  schema-version-mismatch upgrade: consumers must explicitly re-stamp
  golden YAMLs once baselines are confirmed compatible.
- **Threshold tuning that doesn't change keys** (e.g., recalibrating
  PASS/WARN bands without renaming): no bump. Document in design log
  only.

**When to bump `*_VERSION` date strings**:

- Any text edit to the prose artifact, however small.
- The hash tripwire (`PROMPT_HASH` / `JUDGE_PROMPT_HASH`) is computed at
  module-load time and emitted alongside the version into audit logs.
  An un-bumped version after a prose edit is detectable in CI.
- Date collisions (e.g., editing both `PROMPT_VERSION` and
  `JUDGE_VERSION` on the same day) are acceptable: the hash tripwires
  disambiguate the actual content.

Keep constants collocated with their subject (prompt → prompt module,
rubric → judge module, eval shape → report module); collocation beats
centralization for contributor visibility.

---

## Design Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-14 | Separate repos for data and tool | Different dependency profiles, independent release cycles |
| 2026-03-14 | AI curates, does not fabricate | 19.6% of hiring managers reject AI-generated content |
| 2026-03-14 | Sonnet for curation, Sonnet for scoring | Haiku had 23-point reasoning gap; Sonnet's rubric discrimination worth ~$0.03/eval premium |
| 2026-03-14 | Structured outputs via messages.stream() | Constrained decoding guarantees schema compliance |
| 2026-03-14 | Pydantic at edges, dataclasses in core | Performance + validation where it matters |
| 2026-03-14 | Loguru over stdlib logging | Zero-config for CLI, structured output for debug |
| 2026-03-14 | 5-min cache over 1-hour | Pays off after 1 read; 1-hour tier reserved for Phase 2 bulk scoring |
| 2026-03-14 | Separate API billing from Max subscription | Claude API is pay-per-token via console.anthropic.com; Max subscription is chat-only |
| 2026-03-14 | All portfolio sections loaded | 100% of portfolio data available to Claude for curation decisions |
| 2026-03-14 | Selectable vs context-only sections | **SUPERSEDED 2026-04-12**: originally 5 selectable + 4 context-only. See 2026-04-12 AI scope refactor entry |
| 2026-03-15 | SDK built-in retries over Tenacity | SDK handles 429/529 natively; Tenacity layered on top causes double-retry risk |
| 2026-03-15 | Pin model snapshots | Aliases can change behavior; pin for reproducibility, override via env var |
| 2026-03-15 | Check stop_reason for refusal | Claude can refuse; code must handle gracefully with APIRefusalError |
| 2026-03-15 | Streaming as default API pattern | Prevents timeouts, future-proofs max_tokens increases, no code overhead |
| 2026-03-15 | Schema reasoning over extended thinking | **SUPERSEDED 2026-04-12**: reasoning field dropped; field ordering now serves as implicit anchor. See Reasoning Strategy section |
| 2026-03-15 | Configurable effort parameter | Quality tuning independent of thinking blocks; None = API default |
| 2026-03-15 | Always-on DEBUG file sink + INFO console | Complete persistent record for post-hoc debugging/AI analysis; `--verbose` only controls console |
| 2026-03-15 | `diagnose=False` on all sinks | Loguru diagnose dumps local variable values in tracebacks, bypassing redaction filters; disabled everywhere |
| 2026-03-15 | InterceptHandler for stdlib→Loguru bridge | Unified logging for httpx/httpcore/Anthropic SDK through Loguru sinks |
| 2026-03-15 | Regex redaction filter on all sinks | Defense-in-depth against API key leakage (`sk-ant-*` pattern) |
| 2026-03-15 | XDG log directory (`~/.local/state/curator/log`) | Standard location, Linux/WSL only, no `platformdirs` dependency |
| 2026-03-15 | Suppress noisy third-party loggers | Prevents httpx/asyncio flooding the always-on DEBUG file sink |
| 2026-03-15 | Explicit stop_reason handling for refusal and max_tokens | Distinct user guidance: refusal = check JD content; max_tokens = increase CURATOR_MAX_TOKENS |
| 2026-03-15 | Exception chaining (`from e`) on all SDK errors | Preserves tracebacks in debug file; safe because diagnose=False, SDK errors don't leak request bodies |
| 2026-03-15 | `pretty_exceptions_show_locals=False` on Typer app | Prevents Typer from dumping local variables (including API keys) in exception tracebacks |
| 2026-03-15 | APITimeoutError caught before APIConnectionError | APITimeoutError is a subclass; handler ordering matters for distinct user messages |
| 2026-03-15 | CurationResult dataclass (not Pydantic) | Internal transfer object; follows "Pydantic at edges, dataclasses in core" convention |
| 2026-03-15 | CuratorClient accepts CuratorSettings | Single validated object avoids parameter proliferation; annotation-only import under TYPE_CHECKING |
| 2026-03-15 | Templates owned by resume-curator, not portfolio-source | portfolio-source is pure data; resume-curator owns all rendering and template logic |
| 2026-03-15 | Snake_case YAML output for Typst template | model_dump() produces snake_case naturally; template adapted to match instead of adding serialization aliases |
| 2026-04-09 | Fixed section order (configurable, not AI-decided) | section_order moved from ResumeCuration to CuratorSettings; golden data showed AI always picked the same order (work first in 24/24 cases); reduces output tokens, simplifies validation, improves predictability |
| 2026-03-15 | company_slug extracted by Claude from JD | ~5 extra tokens; used for output directory naming and audit trail; kebab-case validated |
| 2026-03-15 | Atomic file writes with fsync | tempfile.NamedTemporaryFile(dir=target_dir) + os.fsync + os.replace for crash-safe writes on same filesystem |
| 2026-03-15 | Template copied into output dir for Typst --root | Typst sandboxes file access to --root subtree; template must be inside it |
| 2026-03-16 | Date formatting in Typst, not Python | format-date() helper in template converts ISO dates (YYYY-MM) to human-readable (Mon YYYY); keeps renderer simple — it writes raw dates, template handles display |
| 2026-03-16 | Education minor/honors/GPA rendered conditionally | Portfolio data already has these fields; template conditionally renders them with graceful fallbacks for missing fields |
| 2026-03-16 | Per-keyword skill filtering over whole-group selection | SkillRanking model with keywords (renamed from SkillSelection/selected_keywords in 2026-04-12 refactor); avoids irrelevant keyword noise on resume and prevents cross-group keyword duplication. Layer 3 validates keywords exist in portfolio |
| 2026-03-16 | max_highlights_per_entry default 4 -> 6 (upper bound 8 -> 10) | **SUPERSEDED 2026-04-12**: max_highlights_per_entry removed. AI now ranks all highlights per entry; renderer trims from the bottom based on page fit |
| 2026-03-16 | Sans-serif font for tech resumes over serif | Best practices §2.2: "screening happens on screens." Font fallback chain: Inter → Ubuntu Sans → DejaVu Sans |
| 2026-03-16 | Left-aligned header with split social links | Name 20pt left, LinkedIn/GitHub right-aligned in navy blue. Matches F-pattern scanning (§1.4) and 71% callback boost from LinkedIn (§1.5) |
| 2026-03-16 | Proficiency levels removed from skills display | Best practices §4.7: "invites skepticism about beginner skills." Portfolio data retains levels; template simply doesn't render them |
| 2026-03-16 | ALL CAPS section headings with horizontal rules | 13pt bold, thin gray rule. Stronger visual hierarchy per §2.4; matches hand-crafted resume pattern |
| 2026-03-16 | Navy blue hyperlinks (#003366) | WCAG AAA compliant (13.4:1 contrast). §2.6: "safest accent color for any industry" |
| 2026-03-16 | GPA omitted for experienced candidates | §4.8: "only if 3.5+ and < 5 years since graduation." Template removes GPA rendering; portfolio data retains it |
| 2026-03-16 | Soft-skill groups NOT excluded via prompt | Decided against prompt-level exclusion — if Agile/PM groups shouldn't appear, fix at the portfolio-source data level (resume_variants tagging) rather than adding prompt complexity. Let Claude decide naturally per JD |
| 2026-03-16 | Pipeline extraction — orchestration in pipeline.py, presentation in cli.py | cli.py was mixing orchestration (load→curate→render) with Rich display. Extracting pipeline.py enables unit testing without the CLI runner and supports features like no-pdf mode. (The re-curation loop variant was tried after this refactor and later replaced by renderer-side trimming; see 2026-04-09 entry below.) |
| 2026-03-16 | suggested_label generated before summary in schema | **SUPERSEDED 2026-04-12**: field order changed to summary-first (summary anchors tone for subsequent rankings). Label (2-5 words) now generated second. max_length=60 for layout safety |
| 2026-03-16 | Presence-equals-inclusion for work/highlights | **SUPERSEDED 2026-04-12**: all work entries are now always included (AI ranks highlights, does not select entries). WorkHighlightRanking replaces WorkEntrySelection |
| 2026-03-20 | --dry-run redefined as zero-cost preview, --no-pdf replaces old behavior | `--dry-run` now makes no API call — loads portfolio, validates JD, shows preview stats (cost estimate, section counts). `--no-pdf` calls the API and writes all artifacts but skips Typst compilation. Internal parameter renamed from `dry_run` to `skip_pdf`. `anthropic_api_key` made optional in `CuratorSettings` with `require_api_key()` method for safe access |
| 2026-04-09 | Renderer-side deterministic trimming over multi-API-call re-curation | Replaced the re-curation loop (up to 5 API calls with tightening constraints) with a single API call + renderer-side trim loop. The renderer removes lowest-value content first (interests, then projects/certs/education, then work highlights/skills) and re-compiles Typst. Default iteration cap was 15 at the time; raised to 100 (ceiling 200) on 2026-04-13. Note: the original cascade also removed work entries wholesale; the 2026-04-13 preserve-all-entries cascade entry below supersedes that piece — work entries and skill groups are now never removed wholesale. Eliminates multiple API calls ($0.07+ each), makes page-fitting deterministic, and lets the AI focus on quality without worrying about page count. |
| 2026-03-16 | pypdf as runtime dependency (>=6.9.1,<7) | Pure Python, page-count-only usage path. Pin >=6.9.1 excludes all known CVEs (CVE-2025-55197 through CVE-2026-31826). Upper bound <7 for major version safety. 10 MB file size guard in get_page_count(). |
| 2026-03-16 | format_version in curation_log.json | Output format versioning (bumped to `"2.0"` in 2026-04-12 refactor) for forward compatibility. Tools reading previous outputs can detect schema changes without guessing |
| 2026-03-16 | ConfigError wraps Pydantic ValidationError | Settings validation failures now surface as `ConfigError` (part of `CuratorError` hierarchy) instead of raw Pydantic `ValidationError`, so the CLI catch-all handler displays them consistently |
| 2026-03-16 | _apply_selections returns skipped ID count | Defensive rendering logs + counts IDs not found in portfolio; count propagated to CLI for user visibility. Upstream validation in client.py should prevent this, but belt-and-suspenders defense surfaces silent failures |
| 2026-03-16 | CI enforces 80% branch coverage gate | `--cov --cov-report=term-missing` added to CI pytest step. The `fail_under = 80` in pyproject.toml was configured but never executed in CI |
| 2026-03-16 | Eval metrics split by category (7 modules) | One module per category instead of monolithic `metrics.py`. Enables selective test runs and parallel development |
| 2026-03-16 | `Eval` prefix on types | `EvalMetricResult`, `EvalMetricStatus`, `EvalReport` avoid collision with `Metric` class in `models.py` |
| 2026-03-16 | `EvalMetricStatus` as `IntEnum` | PASS=2, WARN=1, FAIL=0 enables arithmetic in score aggregation without branching |
| 2026-03-16 | Portfolio optional for alignment metrics | `keyword_coverage` and `jd_match_rate` need full portfolio; when absent, return WARN instead of failing |
| 2026-03-16 | `eval_schema_version` as module constant | `EVAL_SCHEMA_VERSION` in `report.py` (bumped to 3 in 2026-04-12 refactor, to 4 in 2026-04-24 when the `informational` field + `portfolio_fit` sidecar landed), bumped manually on metric/weight/threshold/shape changes |
| 2026-03-16 | 85/75 thresholds provisional | PASS >=85, WARN 75-84, FAIL <75. Will calibrate against golden cases in Phase B |
| 2026-03-16 | Self-contained golden YAML files | Each golden case embeds JD, curation, section data, and basics — no API calls needed for regression tests |
| 2026-03-16 | Generic must_include/must_exclude | Dict keyed by section name scales to all section types without per-section fields |
| 2026-03-16 | Regression severity levels | ERROR (test-failing): score drops, status flips, missing entries. WARNING (informational): baseline range violations, schema mismatch |
| 2026-03-16 | Golden baseline calibration | Set baselines 5 points below actual scores for margin. Per-case baselines, not per-tier |
| 2026-03-16 | `EvalReport.to_dict()` serializes status as names | "PASS"/"WARN"/"FAIL" strings for readability and JSON compatibility, not IntEnum integers |
| 2026-03-17 | Golden tests render PDFs via Typst | Eliminates 15% PDF metric ceiling. Golden cases now exercise all 11 PDF metrics with real PDFs |
| 2026-03-17 | `GOLDEN_SKIP_METRICS` for portfolio-dependent metrics | Skip `keyword_coverage` and `jd_match_rate` in golden eval — need full portfolio data not available in golden cases |
| 2026-03-17 | `compile_typst()` extracted to `io_utils.py` | Shared Typst invocation helper used by both `renderer.py` and `golden.py`. Eliminates duplication |
| 2026-03-17 | Aggregate-only golden baselines | Per-metric baselines (1,440 values across 24 cases) would be high-maintenance for informational-only findings |
| 2026-03-17 | Home-based cache dirs for Typst tests | Snap-confined Typst cannot access `/tmp`. Golden and E2E tests use `$HOME/.cache/` subdirectories |
| 2026-03-19 | Sonnet 4.6 for judge over Haiku 4.5 | 23-point reasoning gap on GPQA Diamond (41.6% vs 65.0%). Nuanced 1-5 rubric discrimination requires stronger model. ~$0.03/eval premium negligible at volume |
| 2026-03-19 | 8 judge dimensions (7 original + narrative_coherence) | Excluded 3 candidates (ats_optimization, role_level_calibration, quantification_quality) for >0.85 Pearson overlap risk. narrative_coherence has no Tier 1 analog |
| 2026-03-19 | Single API call for all 8 dimensions | Simpler, cheaper (~$0.05 vs ~$0.10 for two calls). justification-before-score mitigates anchoring. Split to two calls only if calibration shows Pearson >0.85 between groups |
| 2026-03-19 | Rubric-anchored scoring over pairwise comparison | Single resume against JD — no second resume to compare against. 5-level concrete anchors with observable criteria. Documented departure from best practices §12.5 |
| 2026-03-19 | Separate Tier2Report from EvalReport | Different scoring models (3-state PASS/WARN/FAIL vs 5-point numeric). Avoids contaminating 60-metric Tier 1 test surface |
| 2026-03-19 | judge.py creates own client, not reusing CuratorClient | Different prompt and output format. Optional shared client parameter for golden batch reuse (24 cases, one TCP connection) |
| 2026-03-19 | `-m "not llm"` in pytest addopts | Prevents CI breakage when real-API calibration tests land in PR 2 |
| 2026-04-07 | Removed awards, references, engagements sections | Portfolio-source deliberately deleted these empty-since-creation files (commit b12efd8). Removed models, loader entries, prompt references, template rendering, structured output fields, and tests. Loader still gracefully handles missing files for forward compatibility |
| 2026-04-07 | Replaced LinkedIn with personal website in header | LinkedIn profile link removed from header; `basics.url` (personal website) promoted from contact line to header position alongside GitHub. Renamed `linkedin_present` eval metric to `website_present` (checks `basics.url`). Upgraded `github_present` from WARN to FAIL (resumes should always include both). Bumped `EVAL_SCHEMA_VERSION` to 2 |
| 2026-04-10 | Prompt writing rules, eval recalibration, template overhaul, 10-JD test matrix | Three prompt constraints added: no em dashes in generated prose, always include current/most recent role regardless of JD fit, minimize visible employment gaps by including an early-career anchor. Seven eval metrics recalibrated: `jd_match_rate` switched from 2/3-grams to bigrams only (40%/25% thresholds → 15%/8%), `bullet_word_count` PASS range 10-20 → 8-25 (WARN 5-25 → 5-30), `whitespace_ratio` PASS 25-45% → 55-75% (realistic for tight single-page layouts), `single_column_layout` threshold 72pt → 150pt (accommodates intentional 2-column skills grid), `template_margins` PASS 0.5-1.0in → 0.3-1.0in (WARN 0.4-1.1 → 0.25-1.1), `template_line_spacing` PASS lower bound 0.55em → 0.5em, `template_section_spacing` PASS 8-12pt → 8-26pt (WARN 6-14 → 6-32) to accept the new heading spacing. Thresholds were widened rather than asserted against exact new template values so the eval metrics continue to represent "is this a reasonable resume template?" rather than "is this the current template?", keeping future template iterations from cascading into eval churn. Template overhaul: 0.5in → 0.3in margins, paragraph leading 0.55em → 0.5em, list spacing 2pt → 8pt, removed horizontal rule under section headings, bumped headings to 14pt bold tracked 0.5pt, contact info to 12pt bold with widened separators, enumerate() first-item-no-top-spacing pattern for work/projects/education/certificates. Supersedes the 2026-03-16 "ALL CAPS section headings with horizontal rules" entry: tracked caps without a rule now provide sufficient visual hierarchy at 14pt while freeing vertical space for denser content. Test matrix expanded from 8 to 10 JDs with S4 (DevOps/Platform AI-Forward) and M4 (AI Platform Engineer) for AI-crossover signal |
| 2026-04-11 | Removed `priority: int` from `HighlightSelection` / `WorkEntrySelection` | Renderer-side reverse-chronological sort replaces AI-emitted ranking. AI no longer needs to reason about ties, sequential integers, or ordering — it picks WHICH entries to include and in what strongest-first order within each entry; the renderer handles the rest. Eliminates the sequential-integer / no-ties contract the prompt used to enforce. Identified during S1 testing as a likely attention-shift trigger that contributed to the RDS keyword hallucination (see next entry) because removing the ranking step freed model attention toward JD-literal keyword matching. Kept the removal because its benefits (simpler schema, deterministic ordering) outweigh the drift risk, which is addressed by the verbatim-keyword rule in the 2026-04-11 prompt hardening entry |
| 2026-04-11 | Verbatim-keyword rule for `skills.keywords` (prompt-level, load-bearing) | S1 real-world testing (2x deterministic, `main` bisected clean) showed the model emitting `RDS` under `cloud-aws` because the JD listed it as a required AWS skill but the portfolio does not. Layer 3 `_validate_curation_ids` correctly rejected both attempts but there is no retry path, so two paid calls were wasted. Prompt hardening: added a verbatim-match rule for `skills.keywords` (renamed from `selected_keywords` in 2026-04-12 refactor) in three reinforcing locations (global `<constraints>`, skills output guidance, Keyword strategy), explicitly scoped the "Mirror the JD" rule to narrative fields only, and tightened the `SkillRanking.keywords` Pydantic field description to carry the constraint into the schema the model sees. Three regression asserts in `test_prompt.py` lock each placement. This is a best-effort prompt-level fix, NOT a grammar-level constraint (schema still types the field as `list[str]`; skill groups are dynamic so a static enum isn't possible). The retry-with-feedback architecture and the dynamic per-call enum schema are deferred to a follow-up PR; see `TODO.md` `## Curation Reliability` section. **Load-bearing**: the three-location reinforcement is the only thing standing between a known failure mode and a hard curate failure. Any future prompt refactor that collapses, moves, or weakens the rule can silently re-introduce the RDS class on a different JD against a different keyword. The regression asserts in `test_prompt.py` are the enforcement |
| 2026-04-10 | S1 sanity fixes: summary length, per-position highlights, trim floor, header spacing | Relaxed `bullet_word_count` PASS 8-25 -> 8-35 (WARN 5-30 -> 5-40) so only egregious long bullets fail; the portfolio intentionally contains detailed long bullets. Removed `verb_tense_consistency` metric and `VERB_TENSE_MAP` from rules.py (Claude selects highlight IDs, not text, so the metric could only surface portfolio-source problems the curator can't fix). Prompt: summary target 50-75 words hard max 80, must mention founder of Perts Foundry LLC, must not drop specific artifact names on the cert/project/publication side. Per-position highlight allocation now numeric: position 0 = 4-5, position 1 = 3-4, position 2 = 2-3, positions 3+ = 1-2. **Partially superseded 2026-04-12**: `max_highlights_per_entry` removed, per-position allocation table removed from prompt. Template: wrapped name/label/contact in a `#[...]` scope with `par(spacing: 3pt)` to tighten the top-left header gaps; no changes to section heading spacing, rule/summary padding, or any other template element |
| 2026-04-12 | AI scope refactor: 9 fields to 6, drop reasoning/education/certificates | Reduced AI output from 9 fields to 6: summary, suggested_label, company_slug, work_highlights, skills, projects. Dropped: reasoning (debuggability loss accepted), selected_education, selected_certificates (renderer-managed from portfolio order), selected_work entry selection (all entries always included). Schema renamed: WorkEntrySelection/HighlightSelection to WorkHighlightRanking, SkillSelection to SkillRanking, summary_suggestion to summary, selected_keywords to keywords, selected_projects to projects. Constants split: SELECTABLE_SECTIONS to RENDERER_SECTIONS (5-tuple for renderer/config) + AI_RANKED_SECTIONS (3-tuple for prompt). CONTEXT_SECTIONS deleted. format_version 1.1 to 2.0. EVAL_SCHEMA_VERSION 2 to 3. Prompt reduced from ~668 to ~285 lines. Context-only sections (languages, publications, services, volunteer) dropped from cached portfolio block; summaries no longer draw on these sections (accepted cost-reduction tradeoff). Renderer safety net appends AI-omitted highlight IDs in portfolio order. Optional `priority: int` field added to EducationEntry and CertificateEntry for explicit ordering. Module-load invariant `_validate_reserved_tags()` added for prompt injection defense self-policing |
| 2026-04-13 | Preserve-all-entries cascade philosophy + project weight signal + eval rubric alignment | **Renderer**: work entries and skill groups are never removed wholesale. Work entries render as header-only rows (position/company/dates without bullets) when their highlight list drains, preserving the full employment timeline. Skill groups are preserved by draining keywords one-by-one (tier 11) rather than removing whole groups; `_prune_empty_sections` drops only fully-drained groups. Projects render at most 3 lines (header + up to 2 content bullets, description-as-first-bullet); excess highlights are dropped at hydration in `_apply_selections` to avoid wasting cascade iterations. Cascade reordered: project content (highlights, descriptions, wholesale-keep-2) trims early (tiers 2-4) so the page budget preferentially goes to work and skills. `max_trim_iterations` default 25 -> 100 / ceiling 100 -> 200 to accommodate the gentler drain cadence (the 15-iteration WARNING threshold is now a soft signal — see `TODO.md` Curation Reliability for re-baselining). **Prompt + model**: added `weight: int | None` (ge=1) to `ProjectEntry` matching the portfolio schema's existing `weight` field (1 = highest). Prompt instructs Claude to rank projects by `(JD fit x weight)` strongest-first with strong preference for lower-weight entries (weight-1 / weight-2 MUST appear unless genuinely unrelated). Prompt also notes the renderer's 2-bullet cap so the model puts the strongest highlight first per project. **Eval rubric alignment**: `highlight_counts` positions 2+ accept 0 highlights up to `int(budget*0.5)` (header-only is intentional, not a gap); `total_highlight_count` PASS 6-25 (was 8-25); `skills_keyword_count` PASS 20-70 (was 15-40) — the wider band reflects breadth-preserved group diversity (8-10 groups at 4-7 keywords each lands 30-70). Tier 2 judge gains a `<conventions>` block telling it that header-only older roles are intentional and must not be penalized as gaps on `section_selection`, `highlight_quality`, `narrative_coherence`, or `overall_impression` (recent-role bullets are still held to full standards). `scripts/rerender.py` dev helper added so renderer/template iteration doesn't require paying for new curations. **Tests**: 989 passing including new regression cases for header-only-row preservation, full-skill-matrix passing, and the late-tier project removal floor |
| 2026-04-14 | Static resume mode (zero-API path) + curation_log v2.1 + max_pages 1..5 | **New `curator static` subcommand** generates a polished, general-purpose resume with no API call. `static_mode.synthesize_curation` builds a `ResumeCuration` deterministically from portfolio data: summary/label verbatim from `basics`, all work highlights in portfolio order (capped per-entry by `--max-highlights`), all skill keywords in portfolio order, projects sorted by `weight` ascending. `pipeline.run_static_pipeline` mirrors `run_pipeline` but skips the `CuratorClient`. **Schema bumps**: `CurationResult` gains a `source: Literal["api","static"]` field and `curation_log.json` `format_version` bumps to `"2.1"` to add the new `source` key. `model="n/a"` in static runs to avoid overloading the Anthropic-model-ID semantics. **Renderer**: `jd_text` becomes `str \| None`; static runs skip `job_description.txt` and write `mode.txt` (`source: static\ncompany_slug: ...\n`) as the per-source descriptor. `validate_curation_ids` promoted to public in `models.py` (raises `CurationValidationError`), with `client._validate_curation_ids` now a thin adapter that re-wraps as `APIResponseError` for the API path. New helpers `slugify` and `priority_sort_key` added to `io_utils.py` (the latter with a `field_name` parameter so renderer uses `priority` and static-mode projects use `weight`). **Config**: `max_pages` ceiling raised from `le=3` to `le=5` globally; 1-3 typical for `curate`, 4-5 supports `static` multi-page resumes. **CLI**: `--name`, `--pages` (1..5), `--max-highlights` (1..50), `--no-pdf`, `--json` flags; `--json` and `--no-pdf` mutually exclusive (mirrors `curate --dry-run --no-pdf` precedent). **Output dir hardening**: `_make_output_dir` switched from `exists()`-check + `mkdir(exist_ok=True)` to `mkdir(exist_ok=False)` + `FileExistsError` retry (closes TOCTOU CWE-367); added `is_relative_to` assertion as belt-and-suspenders CWE-22 guard. **Tests**: 1087 passing; new `test_static_mode.py` (22 cases), `test_io_utils.py` (24 cases), integration `test_static_render.py` (4 cases), e2e `test_static_command.py` (3 cases with real Typst). **Why two subcommands instead of `curate --static`**: `curate` requires a JD and carries API-spend semantics; grafting "no-JD, no-API" onto it complicates flag wiring and muddies the help text. **Why `max_pages` raised globally**: avoids a separate validator path and lets `curate` users opt into longer output if ever desired; the static path needs 4-5 for executive/academic profiles |
| 2026-04-14 | Trim cascade simplified to 12 tiers: project descriptions ride with their entry; certificates trim bottom-up early with a top-3 floor; skill groups removed atomically | `TrimKind.PROJECT_DESCRIPTION` removed and the standalone description-drain tier deleted. Once a project's highlights drain to 0, the Typst template naturally renders the description as the single remaining bullet (slot 0 is description-first, capped at 2 bullets); the description disappears only when the whole project is cut wholesale at tier 3. `CERTIFICATE` relocated from old tier 5 to new tier 4 (bottom-up, immediately after wholesale project removal) with `CERTIFICATE_FLOOR = 3` preserving the top 3 certificates as load-bearing credentials. No late-stage cert drain exists, so if page pressure persists after skill-keyword drain (tier 10), the below-floor work-highlight tiers (11-12) fire as the final escape hatch rather than cutting any of the top 3 certs. `TrimKind.KEYWORD` renamed to `TrimKind.SKILL_GROUP`; tier 10 now removes the lowest-priority skill group wholesale (all keywords + header) per iteration instead of draining keywords one at a time. Atomic removal frees a whole section of vertical space per step and converges the page-fit loop in dramatically fewer iterations on dense portfolios. Convergence is preserved: removable certs drain to floor, then skill groups drop atomically, then below-floor work highlights run as last resort. `trim_log` in historical `curation_log.json` files may still contain `Removed description from project:` strings (from the deleted `PROJECT_DESCRIPTION` tier) and `Removed keyword: <kw> from skill group: <sid>` strings (from the pre-rename `KEYWORD` tier). Neither phrasing is produced by the current code path; they should be treated as archive-only |
| 2026-04-24 | Eval framework scoped to curation quality; `informational` metrics + `PortfolioFitReport` sidecar; judge rubric versioned and hash-tripwired; rubric-drift short-circuit on golden comparisons | **Conceptual split** introduced between "curation quality" (what the curator did with the portfolio) and "portfolio-JD fit" (a property of the candidate's career). All aggregate-contributing metrics measure curation quality; portfolio-fit signals are retained as informational (weight=0) but do not drag scores. See the "Curation Quality vs Portfolio-JD Fit" section earlier in this doc. **Tier 2 rubric**: new top-level `<scope>` block (formerly a paragraph inside `<conventions>`) tells the judge to ignore JD requirements/keywords absent from the portfolio; score-5 anchors for `relevance`, `keyword_strategy`, `section_selection`, `overall_impression` rewritten from JD-absolute to portfolio-scoped ("best possible given the portfolio, not the JD"); `summary_quality` and `highlight_quality` anchors tightened in the same direction; reading-gloss removed in favor of explicit anchor rewrites. `JUDGE_VERSION: str = "2026-04-24"` added in `eval/judge.py` and emitted on `Tier2Report.to_dict()`; paired with `JUDGE_PROMPT_HASH` (first 12 chars of `sha256(_RUBRIC_SYSTEM_PROMPT)`) as a tripwire so an un-bumped rubric edit still drifts audibly. Bumping discipline: bump `JUDGE_VERSION` on any rubric prose change (test snapshot pin in `tests/unit/test_eval_judge.py::test_judge_version_pinned`). Judge path also now runs `validate_job_description()` (previously curate-path only) to reject JDs with reserved delimiters; reserved tag set extended with the judge-envelope tags (`curation_selections`, `rendered_sections`, `resume_data`, `scope`, `conventions`, `rubric`, `dimension`). **Tier 1 recalibration** (mirroring the Tier 2 split): `alignment.py`: `keyword_count` upper bound removed (PASS >=15, was 15-25 — more JD keywords on the resume is a net positive, not a penalty); `jd_match_rate` set to `weight=0.0` (portfolio-JD fit signal, not curation quality); `acronym_expansion_pairs` set to `weight=0.0` (same reason). `pdf.py`: `font_embedding_valid` promoted from chronic WARN stub to informational PASS at `weight=0.0` on both with-PDF and dry-run paths (Typst embeds by default; real detection deferred); `actual_min_font_size` threshold relaxed via new `MIN_FONT_SIZE_PASS_PT = 8.5` / `MIN_FONT_SIZE_WARN_PT = 7.5` constants in `rules.py` (was 9.5 / 8.0 — contact-line/footer at 8.5pt is standard design practice). **Score impact**: on the S1 devops profile, Tier 1 aggregate moved 86.7 -> 98.48; Tier 2 re-run deferred to after calibration debt is addressed. **Calibration debt**: `tests/eval/golden/poor-*.yaml` `human_scores` were calibrated against the old (portfolio-JD-fit-aware) rubric and may drift against the new rubric; see `TODO.md` for the short-circuit plan (recalibrate or skip on version mismatch). **Tests**: +7 (weight=0 assertions on alignment/pdf, judge_version pin, prompt hash auto-derivation, JD-reserved-delimiter rejection on judge path including all new envelope tags). **Module docstrings** for `alignment.py` and `pdf.py` updated to reflect the scored-vs-informational split. **Second pass (same day)**: replaced the `weight=0.0` convention with an explicit `EvalMetricResult.informational: bool = False` field; `score_category` now skips informational metrics; new `PortfolioFitReport` dataclass in `eval/report.py` aggregates the portfolio-fit subset (`jd_match_rate`, `acronym_expansion_pairs` — named in `PORTFOLIO_FIT_METRIC_NAMES`) into its own 0-100 score with its own `status`. `EvalReport.portfolio_fit` is always populated; `to_dict()` emits it as a top-level key alongside `metrics`. `EVAL_SCHEMA_VERSION` bumped `3 -> 4`. `DimensionScore` gains a soft `@field_validator` on `justification` that logs a WARNING (never rejects) when the text lacks curation-scope tokens (portfolio/jd/curation/resume/selection/selected). `GoldenMeta.judge_version` added (optional); `compare_judge_against_golden` now short-circuits with a single WARNING finding when the golden's `judge_version` does not match the current `JUDGE_VERSION` (defends against calibration drift across rubric rewrites). `GOLDEN_SKIP_METRICS` narrowed `{keyword_coverage, jd_match_rate} -> {keyword_coverage}`: `jd_match_rate` is already excluded by the informational filter, so the name-list entry was redundant. **Second-pass tests**: +7 more (PortfolioFitReport aggregation, informational-stub exclusion, to_dict serialization, rubric-drift short-circuit on golden, legacy-version handling, GOLDEN_SKIP_METRICS cardinality). Total suite: 1233 passing |
| 2026-04-27 | Phase-1 testing close-out recalibrations: highlight_counts portfolio clamp, jd_match_rate uniform PASS, prompt acronym/keyword-distribution guidance, EvalContext projection precedent | **Motivation**: 2026-04-26 Phase-1 testing across 10 JDs (S1-S4 strong, M1-M4 moderate, P1-P2 poor; see `testing/notes.md`) surfaced four recurring patterns: (1) `highlight_counts` WARN on every case from a single portfolio entry (`pf-senior-devsecops-consultant`) with 3 authored highlights vs. position-0 band of 4-5; (2) `dates_include_months` WARN on every case from year-only `umw-bs-cs` dates (portfolio-side fix; off-repo); (3) `acronym_expansion_pairs` FAIL/WARN on 8+ of 10 cases; (4) `keyword_distribution` FAIL/WARN on 6+ of 10 cases. **Eval changes**: `evaluate_selection` gains a `work_authored_highlight_counts: Mapping[str, int] | None` parameter and clamps the position-based `highlight_counts` band against the entry's authored count (lo and hi both clamped via `min(., authored)`); authored=0 falls back to the position band so header-only roles aren't force-FAILed. **Principle**: penalize curation defects (under-selection of available evidence), not portfolio gaps (entry has fewer highlights than the position target). **Empirics**: 10/10 Phase-1 cases triggered the WARN against `pf-senior-devsecops-consultant`'s 3 authored vs. 4-5 position band. **EvalContext projection**: rather than thread the full `PortfolioData` through `evaluate_selection`, a `work_authored_highlight_counts: dict[str, int]` projection is computed once at context-build time in `from_profile_dir` and `from_pipeline_result` and consumed by the metric. The projection precedent applies any time an eval module needs a single derived quantity from the portfolio. **`jd_match_rate` recalibration**: status moved from 15%/8%/below PASS/WARN/FAIL bands to uniform PASS on the rate path (the no-portfolio path retains WARN as a "broken audit input" signal, distinct from a portfolio-fit gap). **Principle**: an informational portfolio-fit metric should not emit FAIL noise on every realistic case; the numeric `value` carries the actionable signal. **Empirics**: Phase-1 runs produced 0-5% match rates across all 10 JDs (JDs are dense and portfolios are bounded by career history); the prior 15%/8% bands were aspirational and FAIL'd every single run. The metric is now structurally hollow (status carries no signal, only `value` does) and is queued for migration to a typed `PortfolioFitReport.coverage_rate: float` field on the next `EVAL_SCHEMA_VERSION` bump (TODO.md `[CALIBRATE-3]`); until then the consumer contract is documented at the metric site in `eval/alignment.py`. **Prompt changes** (`PROMPT_VERSION 2026-04-26 -> 2026-04-28` across two iterations): added inline acronym-expansion guidance (`Full Name (ACRONYM)` form on first mention, with 9 examples drawn from the Phase-1 misses subset of `rules.ACRONYM_EXPANSIONS`; subsequent mentions may use the bare acronym); explicit anti-fabrication framing for non-listed acronyms ("leave it as the bare acronym rather than guess; inventing expansions is a fabrication and is forbidden"); added top-5-keyword distribution preference (each top-5 JD keyword judged by frequency and prominence should appear in two or more of `summary` / `skills.keywords` / work highlights); explicit precedence ("the verbatim-keyword rule and the no-fabrication rule take precedence" over the distribution preference); explicit "skills.keywords ONLY counts when the term already exists verbatim in the portfolio". `_SYSTEM_PROMPT_TEXT` SHA pin and renderer audit-log assertion updated in lockstep; cache invalidated as expected. **Tests**: +12 (3 highlight_counts clamp scenarios + 3 boundary cases + key-miss + authored=0 + position-1 clamp on selection.py; 1 empty-JD PASS branch on alignment.py; 4 prompt regression pins for acronym list, anti-fabrication guard, distribution precedence clause, and rules.ACRONYM_EXPANSIONS drift detection). Total suite: 1258 passing |
| 2026-04-13 | TODO cleanup pass: audit/eval hardening, packaging, CI signal | **Audit trail**: `PROMPT_VERSION` constant in `prompt.py` (currently `"2026-04-13"`), written into `curation_log.json` as `prompt_version` alongside `format_version`. Bumping discipline is convention-only; pin the test snapshot in `test_renderer.py` when bumping. **Eval typing**: `EvalMetricResult.value` narrowed from `Any` to the `EvalMetricValue` union (`int | float | str | bool | None | list[Any] | dict[str, Any] | tuple[Any, ...]`); `find_metric` returns `EvalMetricResult`. Tests scope `disable_error_code = ["operator", "index"]` only — `call-overload` and `arg-type` stay strict. **Eval comparison**: `BaselineRange.status` (opt-in `Literal["PASS","WARN","FAIL"]`) drives STATUS_FLIP detection in `compare_against_golden`; max-baseline overrun now warns rather than passing silently. `_JUDGE_DIMENSION_TOLERANCES` overrides per-dimension warn/error tolerances (`section_selection (0,1)`, `overall_impression (1,3)`); other dimensions default to `(1, 2)`. `GoldenCase.calibration_source` records human_scores provenance (audit-only). New `from_golden_case()` builds an `EvalContext` directly without the materialize-to-disk roundtrip. Whitespace-ratio formula no longer hardcodes 36pt: `evaluate_pdf` accepts `page_margin_pt`, threaded from `get_uniform_page_margin_pt(template_path)` in `evaluate_tier1`; falls back to 36pt when the template is unavailable (recalibration of the 0.55-0.75 PASS band against the de-biased formula deferred). **Golden schema**: `GoldenMeta.tier` is required (`Literal["strong","good","moderate","poor"]`); `GoldenExpected` switched to `extra="forbid"`; CLI per-tier breakdown reads the field instead of parsing the case-id prefix. **Packaging**: template moved from repo-root `templates/` into `src/curator/templates/`; new `curator.default_template_path()` resolves it via `importlib.resources` so editable installs and built wheels both work. Replaces `__file__` traversal in `config.py`, `eval/__init__.py`, `eval/golden.py`. **Model constants**: new `RENDERER_MANAGED_SECTIONS = ("interests",)` and `EMPTY_INTERESTS` consolidate the renderer-managed section append and the empty-interests default; renderer + golden materializer + content metric all import from one place. **Content metric**: word_count walker handles the interests dict shape (hobbies/fun_facts) instead of skipping it. **Template parsers**: `_extract_text_size` tolerates nested font tuples; `_extract_name_size` collects all bold `#text` candidates and picks the largest, removing source-order dependence. **Hobby**: gained `max_length` on `name` (100) and `description` (500). **Profile loader**: `from_profile_dir` legacy-marker list expanded with `selected_volunteer/skills/projects` and the error message names the offending fields. **CI**: dedicated `Golden Eval` step in `.github/workflows/ci.yml` runs `pytest -m golden` separately and reports its own pass/fail in the PR comment; main pytest excludes `golden` to avoid double-running. **Tests**: 1016 passing; PR #43 boundary-test coverage gaps (jd_match_rate, whitespace_ratio, single_column_layout, bullet_word_count, em_dash_prohibition_present) backfilled |
| 2026-04-30 | Disable Typst auto-hyphenation in resume + cover-letter templates; extend invisible-character validator | **Templates**: `hyphenate: false` in both `cover_letter.typ` and `curated.typ` `#set text(...)` blocks. Typst's auto-hyphenation wraps each line-break hyphen in a `/ActualText <FEFF00AD>` marked-content section, so PDF copy operations emit U+00AD (SOFT HYPHEN) instead of U+002D. Web-form fonts that lack a U+00AD glyph render the codepoint as `.notdef` boxes. Reproduced in `profiles/2026-04-30-<redacted-vendor>/cover_letter.pdf`: 8 line breaks, 8 matching markers, all visible as boxes when pasted into a job-application form. Disabling hyphenation keeps the rendered text, accessibility tags, and clipboard text consistent (no soft hyphens at any layer). The resume template carried the same flag latently; flipped symmetrically. **Trade-off**: `curated.typ` retains `justify: true`, so disabling hyphenation may produce slightly larger inter-word gaps on lines with long unbreakable tokens. Documented inline; do NOT "fix" by re-enabling. **Validator hardening**: `_CONTROL_CHAR_RE` extended from C0+DEL to also reject U+00AD, U+200B-U+200F (zero-width spaces, ZWNJ, ZWJ, LRM, RLM), and U+FEFF (BOM); applies to all three `_CONTROL_CHAR_RE` call sites in `models.py` (`ResumeCuration._no_control_chars` for `summary`/`suggested_label`, `CoverLetterCuration._no_control_chars` for `salutation`/`opening`/`closing`/`sign_off`, `CoverLetterCuration._paragraphs_no_control_chars` for `body_paragraphs`). Closes the bypass on AI-curated output and on the cover-letter portfolio file (which reuses `CoverLetterCuration` as both API-output and portfolio-input model). The broader portfolio-side text fields (`Basics`, `WorkEntry.highlights[].text`, `SkillEntry.keywords`, `ProjectEntry`, etc.) are **not** covered by this regex; portfolio-wide validator extension is tracked as `[AR-1]` in `TODO.md`. **Tests**: structural unit test (`TestTemplateTypography`, parametrized over both templates, font-independent, strips Typst `//` and `/* ... */` comments before the assertion) plus integration negative+positive control (`TestCoverLetterSoftHyphenRegression`, gated on `TYPST_AVAILABLE`, uses pypdf for filter-aware stream walking; positive control patches `hyphenate: true` back into a copied template and asserts the literal `FEFF00AD` byte sequence is present, proving the assertion harness fires on the bad input). Validator hardening is exercised by `test_invisible_chars_in_summary_rejected` (7 codepoints x ResumeCuration.summary), `test_invisible_chars_in_scalar_field_rejected` (9 codepoints x 4 cover-letter scalar fields), and `test_invisible_chars_in_body_paragraph_rejected` (9 codepoints x body_paragraphs). +56 new collected tests; 1314 total passing |
| 2026-04-28 | Resume layout tweaks: tighter summary, "Fully Remote" header, single-line education; rules.py-driven discipline; prompt_hash audit-log wiring | **Layout**: summary word band tightened (`SUMMARY_WORD_TARGET_MAX 75 -> 65`, `SUMMARY_WORD_HARD_MAX 80 -> 70`; output regularly landed at 70-80 and pushed work content below the fold). Header gained an explicit "Fully Remote" marker on the contact line (cheap insurance against ATS/recruiter filters that look for an explicit remote signal). Education compressed from a three-line render (degree+area / institution+date-range / 9pt honors line) to a single-line layout `B.S. Computer Science, minor in Cyber Security · University of Mary Washington · 2018 · Summa Cum Laude` via two new Typst helpers `abbreviate-degree` (exact-match degree-title abbreviation, falls through verbatim) and `year-of` (year-only date extractor with 4-digit-prefix validation). GPA still intentionally omitted per §4.8. **Rules-driven discipline**: `eval/selection.py:summary_word_count_in_range` migrated from hardcoded `50 <= n <= 80` literals to the `SUMMARY_WORD_*` constants (the threshold change had broken the parity with `eval/content.py:summary_word_count`, producing contradictory FAIL/PASS for 71-75 word summaries within a single eval report). `models.py` `ResumeCuration.summary` description string now interpolates the constants instead of hardcoding "50-65"/"70". New module-load `assert SUMMARY_WORD_WARN_MIN <= ... <= SUMMARY_WORD_WARN_MAX` monotonicity guard mirroring the `COVER_LETTER_WORD_TARGET` guard at rules.py:596. **Audit log**: `prompt_hash` (already-defined `sha256[:12]` of `_SYSTEM_PROMPT_TEXT + _COVER_LETTER_PROMPT_BLOCK` constant in `prompt.py`) now actually emitted into `curation_log.json` via `renderer.py` log_data dict. Mirrors the existing judge-side pattern (`judge_version` + `judge_prompt_hash` at `eval/judge.py:212`); resolves the same-day-prompt-edit forensic-collision concern raised in pre-PR review (today's `PROMPT_VERSION="2026-04-28"` was already bumped earlier in the day from a separate edit, so version alone could not distinguish the two prompt snapshots). **Prompt clarity**: `render_summary_length_guidance_for_prompt` now renders "50-65 words soft target, 70 word hard maximum" so the soft/hard distinction is explicit; `_SYSTEM_PROMPT_TEXT` SHA pin updated in lockstep at `tests/unit/test_prompt.py:655`. **Defensive guards**: header literal "Fully Remote" gated behind a `_has_contact_prefix` check so a sparse-basics fixture doesn't render `· Fully Remote` with a stray leading dot; education `institution` segment guarded against schema-relaxation. **Trust-model docs**: `render-education` gained a comment block matching the header trust-model note at curated.typ:107-114, naming the education-field interpolation surface. **PROMPT_VERSION**: stays `"2026-04-28"` — date is still accurate; the new `prompt_hash` field disambiguates the two same-day snapshots in the audit log. **Tests**: 1258+ passing across the run; new `prompt_hash` presence/shape assertion in `test_renderer.py::test_curation_log_has_metadata` |
