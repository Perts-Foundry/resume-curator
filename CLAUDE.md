# CLAUDE.md

## Project Overview

resume-curator is an AI-powered CLI tool that tailors resume content to specific job
descriptions using structured portfolio data (YAML) and the Claude API. It selects and
prioritizes existing experience entries — it does not fabricate content.

## Key Documents

- `docs/architecture.md` — Current architecture and design decisions.
  **Update this document** whenever the architecture changes: new modules, changed patterns,
  updated dependencies, or shifted design decisions.
- `docs/portfolio-schema.md` — Directory layout and per-section schema for the
  portfolio source directory. The Pydantic models in `src/curator/models.py` are
  the canonical schema; this doc is the human-readable summary.
- `docs/testing-protocol.md` — Real-world validation protocol for the curation
  pipeline. The `testing/` directory it references is gitignored; users source
  their own JD content locally.
- `TODO.md` — Single source of truth for all planned work, grouped by phase.
  Add new TODOs here, not as inline comments in source files.

## Coding Standards

Coding standards are encoded in `pyproject.toml` (ruff, mypy, pytest) and
enforced by CI on every PR. The rules below are project conventions on top of
those tool configs:

- **Python 3.12+** minimum. Use modern syntax (`X | None`, `list[str]`, match/case).
- **src layout** with package under `src/curator/`.
- **uv** for dependency management. Always commit `uv.lock`.
- **Ruff** for linting and formatting. Configuration is in `pyproject.toml`.
- **mypy strict** for type checking.
- **Google-style docstrings** for public APIs.
- **Pydantic v2** for all external data boundaries (API responses, YAML, user input).
  Dataclasses for internal transfer objects.
- **`yaml.safe_load()` only** — never `yaml.load()`.
- **Never `shell=True`** in subprocess calls.
- **Never log API keys or PII.** Use `SecretStr` for secrets.
- **Custom exception hierarchy** rooted in `CuratorError` in `exceptions.py`.
- **Anthropic SDK built-in retries** for API calls (not Tenacity).
- **pytest** for testing. Target 80% branch coverage.

## Section Taxonomy

Resume sections are classified into three categories (see `docs/architecture.md`
Section Categories for full details):

- **AI-ranked** (`AI_RANKED_SECTIONS`): work, skills, projects.
  Claude ranks highlights within each work entry, filters/orders skill keywords,
  and ranks projects. All portfolio work entries are always included.
  In **static mode** (`curator static`), the same sections are deterministically
  ranked from portfolio data: highlights and keywords in portfolio order,
  projects sorted by `weight` ascending. No API call is made.
- **Renderer-written** (`RENDERER_SECTIONS`): work, skills, projects, certificates,
  education. These sections are written as YAML files by the renderer. Certificates
  and education are loaded from the portfolio in file order (optionally sorted by
  `priority` field) without AI input.
- **Renderer-managed** (`RENDERER_MANAGED_SECTIONS`): interests. Not sent to AI,
  appended after `RENDERER_SECTIONS`, and trimmed first when the page overflows.
  Empty payloads default to `EMPTY_INTERESTS`.

`RENDERABLE_SECTIONS = (*RENDERER_SECTIONS, *RENDERER_MANAGED_SECTIONS)` is the
full ordered list that lands on the rendered PDF.

When adding sections, update constants in `models.py` and the prompt in
`prompt.py`. The AI produces exactly 6 fields; education, certificates, and
interests are handled entirely by the renderer.

## Architecture Maintenance

When making changes that affect the architecture:

1. Implement the change.
2. Update `docs/architecture.md` to reflect the new state:
   - Add/remove/rename modules in the project structure section.
   - Update dependency descriptions if relationships change.
   - Note new design decisions or pattern changes.
3. Keep `docs/architecture.md` as the source of truth for "how the project works now."
   It should always reflect the current state, not aspirational plans.

### Branch and commit discipline

- **One concern per commit**: do not bundle a prompt fix with an eval
  recalibration with a doc sync. Each independent concern gets its own
  commit even when they ship in the same PR. Bisection over the eval
  framework's prompt evolution depends on this; bundled commits force
  the team to revert unrelated work to back out a single regression.
  AR-2 (2026-04-26) entrenched this convention after the second cycle
  in a row violated `[CR-1-apr24]`.
- **Mid-PR scope additions** (e.g., a finding raised during pre-PR
  review that needs an extra fix) MUST land as a new commit on the
  same branch, not as an amend or a squash into the previous commit.
- The minimum split for a typical eval-touching PR is roughly:
  (1) source/test fixes for the headline change, (2) follow-up
  recalibration items, (3) doc sync. Same PR is fine; separate
  commits make code review and bisection meaningful.

## Cover Letters

Optional cover-letter generation is gated by `--cover-letter` on both
`curator curate` and `curator static`. Defaults to off everywhere.
Neither path ever emits a template letter; both produce fully-filled,
submittable prose with no placeholders and no TEMPLATE banner.

- **Single-call invariant (API path)**: `curator curate --cover-letter`
  bundles the cover letter into the same structured-output call as the
  resume curation via the `ResumeCurationWithCoverLetter` composition
  wrapper. There is never a second paid call. A unit test in
  `tests/unit/test_client.py` (`TestCurateSingleCallInvariant`) asserts
  no billable Anthropic message method is invoked more than once per
  `curate()`.
- **Static path**: `curator static --cover-letter` reads
  `<portfolio>/data/cover-letter.yaml` verbatim into
  `CoverLetterCuration` at portfolio-load time. The loader handles
  the file as an optional object section (missing file is fine when the
  flag is off). `synthesize_cover_letter` is a pass-through; no
  assembly, no tailoring, no placeholders. Missing content raises
  `StaticModeError` pointing at the `COVER_LETTER_*` constants in
  `src/curator/rules.py`. `--name` affects the output directory only,
  not the letter body.
- **Outputs**: `cover_letter.pdf` and `data/cover_letter.yaml` land in
  the same profile directory as the resume. Neither payload includes
  `is_template` (the field was retired when the TEMPLATE banner was
  deleted).
- **Validator (one for both paths)**: `validate_cover_letter` enforces
  word counts (total **soft cap** 250-300: under-min is a hard reject;
  over-max is a `logger.warning` and ships anyway), 40-90 per body
  paragraph (hard), exactly 2 body paragraphs (hard; was 2-3 before
  2026-04-24 to bound the total via section arithmetic), sign-off
  enum, em-dash rejection, forbidden-word/phrase matching, and a
  strict reject for any `[UPPERCASE]` bracketed placeholder. On the
  static path, a validator failure is wrapped as `StaticModeError` with
  a pointer to `cover-letter.yaml` and the authoring guide. All lists
  and bands live in `rules.py` (`COVER_LETTER_*`).
- **Cache partitioning**: on-path and off-path `curate` runs do NOT
  share prompt cache hits. Toggling the flag drops the cache. Verifying
  cache reuse must be done within a single flag state.
- **Failure recovery (API path only)**: when the cover-letter validator
  raises a HARD failure (under-min total, per-paragraph band violation,
  forbidden content, placeholder token), the client persists the
  otherwise-valid resume curation to
  `<output_dir>/curation_partial-*.yaml`. Recover via
  `uv run python scripts/rerender.py --partial <path>` to rebuild the
  resume PDF without re-paying for the API call. Over-max total word
  count is a soft warning, not a hard reject: the letter ships and
  `cover_letter.over_cap=true` in the audit log flags it.
- **Audit log**: `curation_log.json` carries a nested `cover_letter`
  sub-object (`enabled`, `word_count`, `over_cap`) when present, else
  `{"enabled": false}`. `over_cap` is `true` when
  `word_count > COVER_LETTER_WORD_MAX`.

## TODO Tracking

`TODO.md` is the single source of truth for all planned work. Follow these rules:

- **New work items**: Add to `TODO.md` under the appropriate phase/section.
  Never add inline `TODO` comments in source files or future work in architecture.md.
- **Completed work**: Mark items as `[x]` in `TODO.md` when the work lands on `main`.
- **Discovered work**: If you find something that needs fixing or improving while
  working on another task, add it to `TODO.md` rather than leaving a `TODO` comment.

## Repository Structure

```
resume-curator/
  CLAUDE.md                         # This file
  TODO.md                           # Single source of truth for planned work
  SECURITY.md                       # Security policy and disclosure process
  LICENSE                           # MIT license
  README.md                         # Project overview and usage
  pyproject.toml                    # Project config, dependencies, tool settings
  uv.lock                          # Locked dependencies (committed)
  .python-version                  # Python version pin for uv
  .gitignore
  .pre-commit-config.yaml
  .github/
    workflows/ci.yml                # CI: ruff, mypy, pytest, pip-audit, PR comments
    dependabot.yml                  # Weekly grouped dependency updates
  src/
    curator/
      __init__.py
      __main__.py                   # python -m curator
      cli.py                        # Typer CLI entry point, JD input handling
      pipeline.py                   # Pipeline orchestration: run_pipeline (API path),
                                    #   run_static_pipeline (zero-API path), shared
                                    #   _summarize_pipeline_result helper
      models.py                     # Pydantic models for structured output;
                                    #   validate_curation_ids (public),
                                    #   CoverLetterCuration (also the portfolio
                                    #   cover-letter boundary model),
                                    #   PortfolioData.cover_letter,
                                    #   ResumeCurationWithCoverLetter,
                                    #   validate_cover_letter (public)
      prompt.py                     # System prompt + message construction
      loader.py                     # YAML loading from portfolio-source
                                    #   (incl. optional data/cover-letter.yaml)
      client.py                     # Anthropic API wrapper; CurationResult.source
                                    #   ("api" | "static")
      renderer.py                   # Curated YAML writer, Typst compilation, page-fitting trimmer;
                                    #   writes mode.txt for static runs
      static_mode.py                # Zero-API curation synthesis (synthesize_curation,
                                    #   build_static_result, synthesize_cover_letter)
      config.py                     # pydantic-settings configuration; max_pages 1..5,
                                    #   cover_letter_template_path
      exceptions.py                 # Custom exception hierarchy
      rules.py                      # Shared resume quality constants (word lists, thresholds)
      io_utils.py                   # Shared I/O: atomic writes, YAML loading, PDF page counting,
                                    #   slugify, priority_sort_key
      eval/
        __init__.py                 # Public API: evaluate_tier1(), evaluate_tier2(),
                                    #   from_profile_dir(), from_pipeline_result(), EvalContext
        _text_helpers.py            # Shared text extraction (highlight collection)
        report.py                   # EvalMetricResult, EvalMetricStatus, EvalReport,
                                    #   score aggregation, EVAL_SCHEMA_VERSION
        golden.py                   # Golden dataset: GoldenCase, comparison, materializer,
                                    #   PDF renderer, GOLDEN_SKIP_METRICS
        judge.py                    # Tier 2 LLM judge: JudgeResponse, Tier2Report,
                                    #   evaluate_tier2(), 8-dimension rubric scoring
        content.py                  # Content Density metrics (3)
        selection.py                # Selection Quality metrics (11)
        writing.py                  # Writing Quality metrics (17)
        alignment.py                # JD Alignment metrics (6)
        dates.py                    # Date & Format Consistency metrics (3)
        pdf.py                      # PDF Output Quality metrics (11)
        template.py                 # Template Correctness metrics (9)
      templates/
        curated.typ                 # Typst resume template (packaged as resource;
                                    #   located via curator.default_template_path())
        cover_letter.typ            # Typst cover letter template (single-page;
                                    #   curator.default_cover_letter_template_path())
  tests/
    conftest.py
    helpers.py                      # Shared test utilities (find_metric)
    unit/                           # Unit tests for all core modules and eval framework
    integration/                    # Render pipeline tests with mocked Typst
    e2e/                            # Full CLI tests with real Typst compilation
    eval/
      conftest.py                   # Golden regression test fixtures
      test_golden_regression.py     # Parametrized regression tests
      test_judge_calibration.py     # Real-API judge calibration (@pytest.mark.llm)
      golden/                       # Golden YAML cases (synthetic data)
  testing/
    jds/                            # Real-world JD files for manual validation (10 fit-tiered cases)
    results/                        # Eval JSON output from test runs (gitignored)
    notes.md                        # Per-case human observations from JD testing
  scripts/
    rerender.py                     # Dev helper: re-render an existing curated.yaml
                                    #   without an API call (template/cascade iteration)
  profiles/                         # Per-job output (curated YAML + PDFs)
  docs/
    architecture.md                 # Current architecture (kept up to date)
    portfolio-schema.md             # Portfolio source directory schema reference
    testing-protocol.md             # Real-world validation protocol
```

## Claude API & AI Best Practices

These rules apply to all code interacting with the Claude API. Consult
`docs/architecture.md` (Claude API Design Decisions) for full context.

- **Always check `stop_reason` for `"refusal"`** after API calls. Raise
  `APIRefusalError` — never assume every response contains valid content.
- **Log `request_id` on all API failures.** Anthropic support requires it
  for escalations. Use `getattr(e, "request_id", "unknown")`.
- **Use SDK built-in retries** for Anthropic calls (`max_retries` on client
  init). Do NOT layer Tenacity on top — it causes double-retry issues. Use
  Tenacity only for non-Anthropic operations.
- **Default `CURATOR_MODEL` to an alias** (e.g. `claude-sonnet-4-6`) so
  out-of-the-box invocations always work against a current model release.
  Forks that need reproducibility against a frozen release should override
  via env var with a published snapshot ID. Document the trade-off in the
  design log when changing this convention.
- **Use streaming** (`stream()` + `get_final_message()`) as the default API
  call pattern. Prevents timeouts and future-proofs for higher `max_tokens`.
- **Post-response validation** is three layers: grammar (structure) →
  Pydantic (constraints) → application-level (ID existence checks).
- **Schema-based reasoning** (field ordering) over extended thinking blocks
  for audit visibility. Use the `effort` parameter for quality tuning.
- **Prompt caching** on stable data (portfolio) only. Never cache variable
  data (job descriptions).
- **Never log API keys, full request/response bodies, or PII.** Use
  `SecretStr` for secrets.
- **Never re-run a paid API call just to change output format.** A single
  `curator curate` or `curator eval --judge` invocation is a real Sonnet 4.6
  request and costs real money. If you need both JSON and a human-readable
  view of the same result, run the command once with `--json` saved to a
  file, then render the human view from that saved artifact. Do not run
  `curator eval --judge --json > file.json` followed by a second
  `curator eval --judge` for display purposes. Before any paid invocation,
  confirm you are not duplicating work already done in this session. This
  rule applies to all paid calls: curate, judge eval, golden regeneration,
  and judge calibration.
- **Use `curator static` for any zero-cost resume need** (PDF previews,
  general-purpose resumes, format experiments). It synthesizes a curation
  deterministically from portfolio data and runs the same renderer; no API
  call is made. Prefer `static` over hand-rolled scripts that wrap the
  renderer.

## External Dependencies

- **Portfolio source directory** — a directory matching the schema in
  `docs/portfolio-schema.md`, containing YAML portfolio data per section.
  Read-only from this tool's perspective. Default path:
  `../professional-portfolio-source` (the author's private portfolio repo);
  forks should override `CURATOR_PORTFOLIO_PATH` to point at their own.
