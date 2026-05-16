# TODO

Single source of truth for planned work. Inline TODO comments in source
files point here; `docs/architecture.md` describes current state only.

---

## Curation Reliability

Cross-parent highlight attribution and unknown work/project IDs are
**grammar-impossible** on the API path as of 2026-05-15: the
structured-output schema built per-call from `PortfolioData` encodes
`work_highlights_by_id` and `projects` with `items.enum` scoped to
each parent's children, plus `required` + `additionalProperties: false`
on every nested object. Unknown `work_id`, unknown `highlight_id`
inside a known `work_id`, unknown `project_id`, duplicate `work_id`,
and missing work rankings all became decode-time-unreachable.

Skill keywords and skill-group identity are NOT decode-time-enforced.
The original design's `skills_by_id: object{group_id → array[items.enum]}`
exceeded Anthropic's compiled-grammar budget (HTTP 400 "compiled
grammar is too large" on 2026-05-13). Dropping the keyword enum
(Option A) was insufficient: the 22-property required-strict object
hit the same 400 on 2026-05-14, and a 6-probe Haiku bisect localized
the binding axis to *inner-property count under `required` +
`additionalProperties: false`* (not enum count, not description
bytes). The wire shape collapsed to a flat top-level
`skills: array[string]` (Option E, shipped 2026-05-15); the adapter
at `client._adapt_curation_dict` walks each emitted keyword back to
its parent portfolio group (first-match by portfolio order) and
drops unknown keywords with a WARN log line.

`validate_curation_ids` stays as defense-in-depth on the API path
(the adapter catches non-verbatim keywords first) and as primary
defense on the static path (which constructs `ResumeCuration`
directly without going through the adapter). The soft-drop behavior
for hallucinated keywords/highlights (since 2026-04-11 / 2026-05-12)
remains load-bearing on the static path and as adapter-regression
safety net on the API path.

Cover-letter word-count overshoots ship via the existing soft-warn
on the API path. Items below address validation cases that grammar
cannot reach (cover-letter validator policy, prose-level constraints
on `summary` length, etc.) plus observability gaps.

### Retry-with-feedback loop

- [ ] Implement retry-with-feedback in `client.curate`. On
  `APIResponseError` from `_validate_curation_ids`, append the
  assistant's invalid response and a corrective user turn quoting the
  exact invalid IDs, then re-call `messages.stream`. Bound by a new
  `curation_max_semantic_retries` setting (default 0; opt-in via
  `CURATOR_CURATION_MAX_SEMANTIC_RETRIES=1`). Must preserve prompt
  cache hit on retry (system prompt byte-identical across attempts).
- [ ] Add `CurationSemanticError(APIResponseError)` and a
  `ValidationFeedback` frozen dataclass carrying `invalid_work_ids`,
  `invalid_highlight_ids`, `invalid_skill_ids`,
  `invalid_skill_keywords: dict[str, list[str]]`, `invalid_projects`.
  Refactor `_validate_curation_ids` to build the feedback object and
  raise the new exception.
- [ ] Add `build_correction_message(feedback, portfolio) -> MessageParam`
  to `prompt.py` so repair wording lives alongside the primary system
  prompt. Re-list valid keywords for offending skill groups ONLY,
  never the full portfolio (cache preservation).
- [ ] Generalize the loop to also carry cover-letter validator context
  so word-count overshoots and forbidden-word hits are recoverable
  through the same mechanism. **2026-05-09 update**: this is the
  identified unblocker for switching `model` default to
  `claude-haiku-4-5`. The cross-model A/B at
  `testing/results/haiku-eval/findings.md` showed Haiku failing
  ~67% of cover-letter calls across 6 paid samples (lexicon trips,
  paragraph drift, wrong-company artifacts), making it net more
  expensive than Sonnet despite the 3x sticker-price advantage.
  With retry-with-feedback the failures self-correct on the next
  attempt, amortizing across 1-3 calls instead of 1-3 wasted-paid
  recovery cycles. Re-test Haiku once this lands and revisit the
  default.

### Observability

- [ ] Extend `CurationResult` with `attempts: int` and
  `validation_retries: list[ValidationFeedback]`. Token counts become
  totals across all attempts.
- [ ] Extend `curation_log.json` writer in `renderer.py` with a
  `validation_retries` key parallel to existing `trim_log`. Each
  entry: `{attempt, invalid_ids, tokens_in, tokens_out}`.
- [ ] Move usage logging in `client.curate` BEFORE the validation call
  so failed attempts log token counts (today's `client.py` runs only
  on success and wasted spend is invisible).
- [ ] Structured INFO log on every semantic retry (attempt number,
  invalid-ID count, request_id), WARNING on final failure.

### Schema-level elimination (done 2026-05-13 / refined 2026-05-15)

- [x] Build per-call JSON schema dynamically in `client.py` from
  `PortfolioData`. Landed as `src/curator/output_schema.py` with
  `build_curation_schema()`. `oneOf`-discriminator design was
  abandoned after research established Anthropic's grammar likely
  union-flattens `anyOf` branches with no decode-time narrowing
  (`oneOf` isn't supported at all). Initial design used an
  object-with-fixed-keys form for both `work_highlights_by_id`
  AND `skills_by_id`, each value carrying its own `items.enum`.
  Empirically verified against `claude-haiku-4-5` on 2026-05-13
  (9 probe calls; all ENFORCED at small scale). The full-portfolio
  shape, however, exceeded Anthropic's compiled-grammar budget
  (HTTP 400 "compiled grammar is too large"); a 6-probe Haiku bisect
  on 2026-05-14 localized the binding axis to inner-property count
  under `required` + `additionalProperties: false`. The shipped
  shape (Option E, 2026-05-15) keeps `work_highlights_by_id` as
  object-with-fixed-keys (the load-bearing cross-parent attribution
  surface, 5 inner properties) but collapses skills into a flat
  top-level `skills: array[string]` with no `items.enum`. The
  adapter at `client._adapt_curation_dict` walks each emitted keyword
  back to its parent portfolio group (first-match by portfolio order;
  ambiguous attributions logged at INFO; unknown keywords dropped
  with WARN). Full failure history and the 6-probe bisect live in
  the investigation plan referenced from `docs/architecture.md`.
- [ ] Add a `highlight_ids` dedup pass to `validate_curation_ids`.
  Grammar enforces membership of `items.enum`, not uniqueness or
  count, so the model could in principle emit `[pf-a-h1, pf-a-h1,
  pf-a-h1]` even with the new schema. Validator should dedup
  in-place before returning. Independent of the schema fix; small
  and self-contained.

### Defense-in-depth

- [ ] Per-session circuit breaker: process-scoped counter on
  `CuratorClient`. If >50% of session calls hit validation retries,
  log CRITICAL and disable retries for the remainder of the session.
  Prevents silent cost doubling if a future model snapshot regresses.
- [ ] Portfolio gap reporter: `curator eval` subcommand (or standalone
  script) that aggregates `validation_retries` across all
  `curation_log.json` files under `profiles/` and `testing/results/`,
  flags top-N missing IDs by parent section. Repeated "missing X"
  errors mean the portfolio, not the model, needs updating.

### Tests

- [ ] `test_curate_hallucinated_keyword_raises_semantic_error`
  parametrized with `{"cloud-aws": ["RDS"]}` (the S1 failure shape).
- [ ] `test_curate_retries_on_semantic_error` with mocked SDK
  returning invalid then valid; assert `stream()` called twice.
- [ ] `test_curate_gives_up_after_max_retries` with N consecutive
  invalids; assert `stream()` called `N+1` times.
- [ ] `test_curate_logs_request_id_on_validation_failure`.
- [ ] `test_curate_logs_token_usage_on_validation_failure` after the
  usage-log move.
- [ ] `test_build_system_prompt_is_byte_stable_across_retries` cache-
  preservation invariant.

---

## Renderer & Trim Cascade

Surfaced by S1 / S2 testing reruns. Some are pre-existing; some
followed from the deliberate "preserve full employment timeline"
design choice.

- [ ] Trimmer drops the certificates section entirely when it ends up
  empty after the cascade, instead of rendering an empty heading.
- [ ] Decide and document the work-entry zero-highlight policy.
  Current behavior (preserve every portfolio work entry as a
  header-only row) is intentional product design; pin it explicitly
  in `docs/architecture.md` so future contributors don't "fix" it.
- [ ] Per-group keyword filter is too permissive. Prompt should
  pressure the model harder to pick fewer, more targeted keywords per
  group; or tighten via schema once the per-call dynamic schema work
  lands.
- [ ] Atomic skill-group removal can over-trim on boundary cases
  (removing 2 groups when removing 1 would have fit). Once only one
  skill group remains, fall back to keyword-level drain to avoid
  wiping the Skills section on a tiny-portfolio edge.
- [ ] Cascade hard vs soft project weight: prompt says weight-1 /
  weight-2 MUST appear, but the renderer can drop them once the list
  trims toward the keep-2 floor and the AI rank order can disagree
  with weight order. Pick one authority. Options: (a) split projects
  schema into `must_include` + `nice_to_have`, (b) weaken prompt to
  "strongly prefer", (c) sort by weight ascending in renderer before
  the trim step. Schema-enforce 3-5 range with min/max_length on
  `ResumeCuration.projects` so violations are visible.
- [ ] `max_trim_iterations` default (100) and ceiling (200) were
  reactive bumps. Compute worst-case from portfolio size
  (`len(projects)*3 + len(certs) + len(work)*avg_highlights +
  len(skills)*avg_keywords + recent_role_soft_floor*2`), set default
  to ~1.5x that, raise the WARNING threshold to ~0.5x the new default
  so it still catches pathological convergence.
- [ ] Negative vertical spacing in `templates/curated.typ`
  (`render-certificates` uses `#v(-2pt)` between items). Brittle if
  `#set par(leading: ...)` is ever raised. Wrap in `block(spacing:
  0pt)[...]` or use a positive `#v(1pt)` with a different typographic
  strategy.
- [ ] `scripts/rerender.py --partial` is positioned as a product
  recovery path in `docs/testing-protocol.md` but is architecturally
  a dev script (no test coverage on the `--partial` branch). Pick:
  (1) promote to a Typer subcommand `curator rerender --partial
  <path>`, (2) make the pipeline auto-recover by re-rendering resume-
  only on `CurationValidationError`, or (3) keep internal-only and
  drop the `testing-protocol.md` pointer.

### Refactor candidates

- [ ] Refactor cascade to a declarative `TrimRule` list (predicate +
  action + metadata) iterated in order, so new rules slot in by list
  position rather than editing a long `_generate_next_trim` function.
- [ ] Property test: for any portfolio with N work entries, `render()`
  output YAML contains N work entries regardless of `max_pages`. The
  "every entry renders" invariant is now load-bearing and unprotected
  against future cascade refactors.
- [ ] Machine-readable contract between prompt and renderer for
  "protected" items (weight-1/2 projects, positions 0/1 work). Add
  `protected: bool` or `protection_tier: int` to `ResumeCuration`
  sub-models so the renderer consults the field rather than positional
  heuristics.

---

## Two-Page-Mode Follow-ups (from 2026-05-09 work)

These were deliberately deferred so the wave 1-3 PR could land without
expanding scope. Each is independently shippable.

- [ ] **Long-form safety-net + soft-floor interaction**: on long-form
  rerenders, `_reorder_with_safety_net` (renderer.py:124) appends every
  AI-unranked portfolio highlight to the current role, then the trim
  cascade's `recent_role_soft_floor=4` (max_pages=2) protects positions
  0-1 while draining positions 2-4 to zero. Net effect: position 0
  monopolizes page 1 (e.g. 21 bullets) while positions 2-4 render as
  header-only rows. This is exactly the "curation gap" pattern the new
  long-form judge `<conventions>` will FAIL on. Reproduced on
  `profiles/2026-05-09-boeing/` (rerender of a 1-page-targeted curation
  at `--pages 2`): AI ranked 5 highlights for the current role; renderer
  shipped 21 (5 ranked + 16 safety-net-appended); 3 of 5 work entries
  rendered with 0 bullets. Pure rendering / cascade concern -- the AI
  prompt is page-agnostic by design and will not see `max_pages`.
  Possible fixes: (a) per-role hard cap that scales with `max_pages`,
  (b) cap the safety net per entry (e.g. `max_safety_net_per_entry =
  max_pages * 2`), (c) cascade reorder so safety-net-appended highlights
  drain before AI-ranked highlights at non-current positions, (d) drain
  current-role excess before draining older roles to zero. Best
  diagnosed alongside the AR-1 ProjectRanking work since both touch the
  AI/renderer split. (Real-world finding 2026-05-09.)

- [ ] **AR-1 / ProjectRanking schema**: extend `ResumeCuration.projects`
  from `list[str]` to `list[ProjectRanking]` so the AI ranks highlights
  *within* each project, not just project IDs. Today's renderer caps
  every project at 2 bullets across all `max_pages` modes precisely
  because the AI cannot rank per-project highlights — raising the cap
  to 3+ on long-form would surface portfolio-position-2 content rather
  than JD-relevance content. Schema extension would unblock a long-form
  per-project cap of 3-4 and add genuine project depth on 2-page output.
  Bumps `PROMPT_VERSION` (one cache invalidation cycle); update
  `_caps_for_pages` to return `project_bullet_cap` once the AI ranking
  exists. (architecture-reviewer 2026-05-09 review.)

- [ ] **AR-7 / EXEC_FORM_BANDS**: third tier in `bands_for_pages` for
  executive 4-5 page CVs. Today's selector plateaus at `LONG_FORM_BANDS`
  for `max_pages >= 2` so a 4-page exec CV scores against 2-page bands
  (likely too dense). Add `EXEC_FORM_BANDS` (or rename to a generic
  per-page profile) when 3+-page output becomes a real workflow target.
  Track in `_caps_for_pages` too — it also plateaus at 3+.

- [ ] **AR-15 / Auto-downsize on under-fill**: renderer post-check that
  emits an INFO log + `auto_downsize_eligible: true` in
  `curation_log.json` when the final page is < 30% filled. Future
  enhancement: optional flag that auto-recompiles at `max_pages-1` and
  ships the smaller PDF. User explicitly chose "always target the
  requested page count" for the wave-1-3 scope; this lands as opt-in
  later if needed. (architecture-reviewer 2026-05-09 review.)

- [ ] **AR-3 / Per-mode `_JUDGE_DIMENSION_TOLERANCES`**: watch item.
  Existing tolerances (`section_selection: (0,1)`, `overall_impression:
  (1,3)`, default `(1,2)`) were calibrated against 1-page goldens. If
  the long-form judge calibration shows wider dispersion on
  `narrative_coherence` / `highlight_quality`, add a per-page-budget
  tolerance map. Land only if observed empirically, not preemptively.

- [ ] **TE-15 / Long-form golden human_scores**: the 4 long-form
  goldens shipped with `baselines: {}` and no `human_scores` block.
  After running the judge against them once (paid; ~$0.20), populate
  `human_scores` from the calibration output and set `meta.judge_version`
  so the regression contract is restored on the long-form arm.

- [ ] **CR-15 / 1-page golden re-baseline**: existing 24 goldens'
  `human_scores` are stamped with `judge_version="2026-04-26"`. Until
  re-baselined, `compare_judge_against_golden` short-circuits with a
  WARNING for each (no ERROR). Run the judge once against all 24 cases
  (paid; ~$1.20), lift `meta.judge_version` to `"2026-05-09"` where
  diffs ≤ tolerance, mark out-of-tolerance cases for fresh human review.

- [ ] **CR-21 / Annotate stale band claims**: the design-log entries
  dated 2026-04-10, 2026-04-13, and 2026-04-14 still describe band
  values (e.g. `total_highlight_count PASS 6-25`, `whitespace_ratio
  PASS 55-75%`) as universal facts. Annotate inline with "now
  SHORT_FORM_BANDS-specific" supersedes-style notes so the log reads
  correctly to a future contributor. (doc-sync-checker 2026-05-09
  review.)

## Trim Cascade Rebalance Follow-ups (from 2026-05-10 work)

- [ ] **Calibrate `work_position_floors` from real runs**: the
  `(8, 6, 6, 2, 2)` 2-page profile is a starting point. After N>=10
  2-page renders ship under the new profile, review the `trim_log`
  pattern + on-page bullet distribution and tighten or relax the
  per-position floors. Particular concerns: 6-bullet position-1 floor
  may be too generous for short-content roles; 2-bullet position 3-4
  floor may be too low for substantive older roles when the page
  budget allows.

- [ ] **EXEC_FORM_BANDS for `max_pages >= 4`**: the renderer now
  scales 2-vs-3+ asymmetrically (`(8,6,6,2,2)` vs `(10,8,8,4,4)`)
  while `bands_for_pages` still treats `max_pages >= 2` as one
  profile (`LONG_FORM_BANDS = caps(2)`). On 3+-page renders the eval
  scores against 2-page bands. Add a third `EvalBands` profile keyed
  on `max_pages >= 3` once 3+-page output becomes a real workflow
  target (touches `bands_for_pages`, `EvalBands` field defaults, and
  `test_eval_bands_share_caps_floors` parametrize range). Subsumes
  the AR-7 follow-up above on the eval side.

- [ ] **Validator hard-vs-soft on per-paragraph cover-letter
  over-max**: today's API path soft-warns on per-paragraph word
  count over `COVER_LETTER_PARAGRAPH_WORD_MAX=90`; the static path
  hard-rejects. Original rationale ("paid calls are expensive") was
  sound but the cost asymmetry has shifted now that the
  partial-recovery flow is built out (per CLAUDE.md "Failure
  recovery (API path only)"). Reconsider promoting per-paragraph
  over-max to a hard reject on the API path. Would have caught the
  311-word output observed on the 2026-05-10 reference run before
  it shipped. (prompt-reviewer CRIT-1 follow-up.)

- [ ] **Cover-letter prompt block hash pin**: today's
  `EXPECTED_SHA256` test pin in `tests/unit/test_prompt.py:655`
  covers `_SYSTEM_PROMPT_TEXT` only. The cover-letter block
  (`_COVER_LETTER_PROMPT_BLOCK`) is interpolated and only audited
  via `PROMPT_HASH`. Adding a parallel byte-identity pin for the
  cover-letter block would catch silent edits at test time
  (parallel to the system-prompt pin). Defer until an unrelated
  cover-letter prompt edit warrants the cache-rotation cost.

- [ ] **Aggressive cover-letter band tightening (escalation
  trigger)**: if the conservative target+body-band change shipped
  2026-05-10 still produces >20% drift over the 300-word soft cap
  on the next 5+ paid `--cover-letter` runs, tighten body band
  prose further from `80-87` to `80-85` (achievable upper bound
  becomes `65 + 2*85 + 45 = 280`). Validator constants stay at 90.

---

## Cover Letter

### Real bugs

- [ ] Forbidden-word validator collides with target company names that
  happen to be one of the AI-tell metaphor words. Validator
  lowercases the cover-letter body before regex-matching, so a
  capitalized proper-noun company name and the lowercase metaphor are
  indistinguishable. Recommended fix: switch `_FORBIDDEN_WORDS_RE` to
  case-sensitive matching against original-case text with lowercase-
  only patterns; capitalized occurrences (proper nouns, including
  target company names) are then exempt while metaphor uses still
  trip the rule. Same change applies to forbidden-phrase matching.

### Grounding

- [ ] Basic grounding heuristic in `validate_cover_letter`: every
  multi-digit number in the letter must appear somewhere in portfolio
  text (basics.summary union all highlight texts union project
  descriptions). Cheap belt against fabricated metrics.
- [ ] Fuller claim-trace check that maps named entities (company
  names, project names, technologies) back to portfolio entries.
  Larger lift; revisit after the numeric-grounding heuristic lands
  and there is telemetry on real fabrication rates.

### Cover letter eval

- [ ] Decide whether cover letters should be evaluated in the Tier 1
  / Tier 2 framework. Currently out of scope; eval ignores the
  `cover_letter` field on `CurationResult`. The Tier 2 LLM judge is a
  natural fit for writing-quality dimensions.

### Tuning & variants

- [ ] Evaluate whether cover letters benefit from `effort=high`
  independently from the resume. Currently both share the same
  effort knob.
- [ ] Email-body variant (150-250 words) as a distinct artifact for
  hiring-manager outreach.
- [ ] Inject one worked example into `_COVER_LETTER_PROMPT_BLOCK` to
  anchor output style. Watch for over-anchoring before merging.

### Schema & API

- [ ] If Anthropic's structured-output grammar adds support for
  `minLength` / `maxLength` / `minItems > 1`, move
  `CoverLetterCuration` length and count constraints out of
  validators back into `Field(...)` so generation-time enforcement
  kicks in.

---

## Eval Framework

### Calibration

- [ ] Run 3 judge passes per golden case and use mean scores for more
  robust baselines.
- [ ] `test_judge_stability` smoke test: 2 runs same case, assert
  `diff <= 1`.
- [ ] Add 2-3 "defect injection" golden cases with intentional writing
  defects.

### Metrics

- [ ] `jd_match_rate` denominator is structurally asymmetric.
  `extract_keywords(jd_text)` produces unigrams AND bigrams, but
  `_build_portfolio_keywords` only accumulates single-token strings
  from `skill.keywords`, `entry.technologies`, etc. Every JD bigram
  is an automatic miss in `jd_keywords & portfolio_keywords` yet
  still counts in the denominator. Principled fixes: (a) build
  `portfolio_keywords` by also running `extract_keywords()` over a
  blob of portfolio text (work highlight text, project descriptions,
  summary), or (b) for `jd_match_rate` specifically, strip bigrams
  from the denominator.
- [ ] Migrate `jd_match_rate` from "always-PASS informational metric"
  to a typed `PortfolioFitReport.coverage_rate: float` field. Status
  carries no signal today; only `value` does. Pair with the
  `EVAL_SCHEMA_VERSION` bump.
- [ ] Replace `EvalMetricResult.informational: bool` with a `kind:
  Literal["scored", "portfolio_fit", "deferred_stub"]` enum so the
  partition is self-documenting. ~30-line change + schema bump.
  Worth doing when a third informational metric arrives.
- [ ] `whitespace_ratio` PASS band 0.55-0.75 was calibrated against
  the prior 36pt-hardcode bias. Recalibrate against re-rendered
  goldens or document the coupling in `docs/architecture.md`. When a
  `template_path` is supplied but unreadable, emit WARN rather than
  silently computing against the 36pt fallback.

### Test alignment

- [ ] `TestSingleColumnLayoutThreshold` and `TestWhitespaceRatioThresholds`
  reimplement metric logic locally instead of exercising production
  code. Either extract `_is_single_column(chars)` /
  `_compute_whitespace_ratio()` for direct import, or drive
  `_evaluate_with_pdf` with a synthetic page and assert on the
  resulting `EvalMetricResult.status`.

### Schema & invariants

- [ ] `default_template_path()` does not validate file existence.
  Add a `@field_validator("template_path")` on `CuratorSettings` to
  fail fast at startup.
- [ ] Module-level invariant `assert set(BaselineStatus.__args__) ==
  {s.name for s in EvalMetricStatus}` in `eval/golden.py` so adding
  a new `EvalMetricStatus` value (e.g., SKIP) without updating
  `BaselineStatus` is caught at import.
- [ ] Single source-of-truth mapping from template declarations to
  eval PASS bands. `template_body_font_size`, `actual_body_font_size`,
  `whitespace_ratio` bands, and the template's actual values are
  independently maintained. Derive PASS bands as offsets from
  canonical template values.

### Naming

- [ ] Rename `validate_curation_ids` to `sanitize_curation_ids`. The
  function returns a sanitized `ResumeCuration` (skill keywords with
  unknown values dropped); the current name does not signal the
  return-value contract, and a future caller that discards the return
  value compiles cleanly and silently leaks bogus keywords through
  the renderer.

---

## Curation Pipeline

### API & SDK

- [ ] Migrate `output_format` to `output_config.format` in
  `client.py` and `judge.py` when Anthropic's transition period ends.
- [ ] Batch API integration (50% cost discount for async 24-hour
  processing).
- [ ] Token pre-counting via `client.messages.count_tokens()` to
  reject oversized inputs before batch requests.

### Prompt iteration

- [ ] Few-shot curation examples (1-2 high-quality decisions) in the
  system prompt. Measure token cost vs. quality lift.
- [ ] Evaluate extended thinking for complex multi-step pipelines
  where schema-based reasoning proves insufficient.
- [ ] Replace "(JD fit x weight signal)" in the prompt with a
  concrete formula like "(JD fit, tiebroken by lower weight value)".
  Cartesian product notation suggests multiplication the model can't
  actually perform.
- [ ] Worked example for project guidance: "weight-1 loose fit beats
  weight-5 tight fit" vs "weight-5 dramatically better wins" -
  resolves subjective thresholds more effectively than more prose.

### Token efficiency (measure first)

- [ ] YAML flow-style serialization (`default_flow_style=False` →
  flow on inner lists). Could reduce cold-cache cost materially at
  bulk-processing scale. Measure token delta on a representative
  portfolio before switching.

### Security hardening

- [ ] Portfolio tag-injection escaping. Portfolio values can contain
  literal `</basics>` sequences today; PyYAML escapes into quoted
  form but the literal tag-close appears in the serialized text. Not
  exploitable while the portfolio is trusted (single-author, read-
  only). Fix before relaxing the portfolio trust model (merged
  portfolios, shared teammates, external feeds): either escape `<` in
  dumped values before wrapping in section tags, or scan serialized
  section output for reserved delimiters and raise
  `PortfolioValidationError`.

### Static-mode

- [ ] Re-introduce `--variant` flag to filter by `resume_variants`
  (general / devops / security) when needed.
- [ ] Have `render()` accept `ResumeCuration` directly instead of
  `CurationResult`. Large call-site refactor; deferred until a
  second non-API producer of curations exists.
- [x] ~~Soft validation: warn when `curator curate` runs with
  `--max-pages > 3`~~ — superseded 2026-05-09 by the explicit `--pages`
  flag on `curate` and the aligned default of 2 (`--pages 1` for
  short-form, `--pages 4..5` for executive CVs is now an explicit
  user choice, not a "watch this carefully" warning surface).

### Suggestions worth prompt drift

- [ ] `suggested_label` JD phrasing guidance: tell Claude to prefer
  exact JD title phrasing when experience matches, paraphrase when
  the JD title is unusual or overly specific. Add if eval shows
  drift.
- [ ] `company_slug` edge case fallbacks: guidance for ambiguous or
  missing company names ("a leading tech company" → fallback like
  `unknown-role`). Add if bulk runs produce bad slugs.

---

## Public Republish

These are the polish items for a clean first impression after
republishing.

### Examples & onboarding

- [ ] Build `examples/portfolio-minimal/data/*.yaml` covering all 12
  sections (basics, work, education, skills, certificates, projects,
  volunteer, publications, languages, services, interests, optional
  cover-letter) with all-fictional content. Wire one e2e test that
  runs `curator static --portfolio examples/portfolio-minimal` end-
  to-end so the example stays valid as the schema evolves. Cleanest
  fix for the schema-reference gap; README "Bring your own
  portfolio" callout already notes this is a planned follow-up.
- [ ] `docs/release-checklist.md` capturing pre-publish guardrails:
  secret scan green on `--all` history, `testing/` gitignored, no
  `request_id` strings in tracked files, no real company names
  outside `rules.py` forbidden-word lexicon, working defaults in
  `config.py`. Optional follow-up: CI step that greps for known
  patterns (real company names, `msg_[A-Z0-9]{20,}` IDs) on every
  PR as defense-in-depth alongside gitleaks/trufflehog.
- [ ] Ship a `.secrets.baseline` so the `detect-secrets` pre-commit
  hook works out of the box for first-time external contributors.
  Run `detect-secrets scan > .secrets.baseline`, annotate test-
  fixture matches as `is_secret: false`, commit, and add a brief
  README note.
- [ ] Add `gitleaks` to `.pre-commit-config.yaml` alongside the
  existing `detect-secrets` hook. Catches a few patterns
  (`sk-ant-*`, generic high-entropy YAML strings) that
  `detect-secrets` misses.

### CI & tooling

- [ ] Remove the `--ignore-vuln CVE-2026-3219` and
  `--ignore-vuln CVE-2026-6357` flags from the `pip-audit` step in
  `.github/workflows/ci.yml` once pip 26.1 ships to PyPI and the
  lockfile picks it up. Both CVEs are scoped to pip 26.0.1 and both
  fixes ship in pip 26.1. Tracking issue: pypa/pip#13870.
- [ ] Pre-commit `mypy` hook only sees changed files; CI mypy only
  covers `src/`. Either extend CI mypy to cover `tests/` or note the
  IDE-only scope in `CLAUDE.md`.
- [ ] `pyproject.toml` default `addopts = -m "not llm"` runs goldens
  locally but CI excludes them (`-m "not golden and not llm"`).
  Either align local to CI or add a Makefile target that mirrors CI
  exactly. Verify `fail_under = 80` still holds on the filtered set.

---

## Phase 2: Job Discovery

- [ ] `--url` flag for direct JD fetching from job board URLs (HTML-
  to-text extraction).
- [ ] Job board API integrations (Greenhouse, Lever, HN "Who is
  Hiring?", RSS feeds, JobSpy).
- [ ] Job scoring pipeline (Haiku or keyword matching: pull, score,
  surface, curate).
- [ ] Rate-limit header access via `with_raw_response.create()` for
  proactive throttling.
- [ ] Gradual traffic ramp for batch pipelines (acceleration limit
  mitigation).

---

## Phase 3: Application Submission

- [ ] Browser automation via Playwright.
- [ ] Human-in-the-loop approval workflow.
- [ ] Application tracking.

---

# Review TODO

> Deferred findings from pre-PR reviews. Check off items as resolved.

## Important

- [ ] **[AR-1]** Extend `_CONTROL_CHAR_RE` (or an equivalent loader-pass
  scrub) to portfolio-side text fields. The current PR only validates
  AI-curated output and the cover-letter portfolio file (which reuses
  `CoverLetterCuration`). A SHY pasted into `Basics`, `WorkEntry.highlights[].text`,
  `SkillEntry.keywords`, `ProjectEntry`, `EducationEntry`, etc. flows
  through the loader unflagged and renders as `.notdef` in the PDF the
  same way the original bug did, just from a different source.
  Cheapest implementation is a one-pass scrub in `loader.py` after
  `yaml.safe_load` that points at the offending file:line; alternative
  is a shared `@field_validator` mixin across 10+ portfolio models.
  (architecture-reviewer, 2026-05-01)
- [ ] **[AR-2]** Structural cover-letter page-fit handling. The cover letter
  has no trim cascade (`renderer.py:_render_cover_letter` is single-pass
  with a logger.warning on overflow); first page-overflow incident on a
  real submission ships an unusable two-page artifact. Disabling
  hyphenation narrows the safety margin slightly. Either (a) add a
  minimal cover-letter trimmer mirroring the resume cascade on a smaller
  surface area (drop closing pleasantries, then tighten body paragraph
  toward its 40-word floor, then fail loud) or (b) tighten
  `COVER_LETTER_*_WORD_MAX` so the geometry is provably fitting at all
  input shapes. (architecture-reviewer, 2026-04-30)
- [ ] **[AR-7]** Surface cover-letter `pages` in the `cover_letter`
  sub-object of `curation_log.json` so bulk-automation runs are
  auditable (currently logger-only). (architecture-reviewer, 2026-04-30)

## Suggestions

- [ ] **[CR-7]** Make the verification snippet's `<date>-verify-no-shy/`
  path resolve via glob in docs / runbooks so operators don't have to
  substitute the date manually. (code-reviewer, 2026-04-30)
- [ ] **[CR-8]** Document a known-hyphenation-triggering verification
  fixture (or a one-off `verify-shy.py`) so static-render smoke checks
  don't pass vacuously when `cover-letter.yaml` happens to contain no
  long compound words. (code-reviewer, 2026-04-30)
- [ ] **[DS-2]** Mirror the curated.typ file-header design comment update
  on cover_letter.typ if/when that file grows a similar enumerated
  design list. (doc-sync-checker, 2026-04-30)
- [ ] **[AR-4]** Revisit the per-break ActualText control alternative if
  a future Typst release exposes a knob to "draw hyphen but skip
  ActualText" (not available in 0.13.x/0.14.x). (architecture-reviewer,
  2026-04-30)
- [ ] **[AR-6]** Class-level grouping for any further regression tests
  added to `test_renderer.py` (file is 2850+ lines; free-floating
  module-level tests get lost). (architecture-reviewer, 2026-04-30)
- [ ] **[TE-8]** Ensure the soft-hyphen regression-test docstrings link
  back to the originating issue / commit so future contributors
  scanning a "weird" assertion can find the rationale.
  (test-engineer, 2026-04-30)
- [ ] **[SA-2]** Rewrite `_CONTROL_CHAR_RE` in `models.py` with explicit
  `­` / `​-‏` / `﻿` escape sequences instead of
  literal codepoints, so the class is greppable and reviewable without
  a hex inspector. Blocked on tooling: the agent's edit pipeline
  decodes `\u` sequences at the JSON layer, so direct literal
  insertion is currently the only working form. Revisit when editing
  by hand or via a build script. (security-auditor, 2026-05-01)
- [ ] **[CR-2]** Tighten the positive control's "patched != original"
  assertion to verify the active-code occurrence count went from 1 to
  0, not just that any text changed (e.g., comment-only rewrite would
  pass today). Strip comments before counting. (code-reviewer, 2026-05-01)
- [ ] **[CR-4]** Add an integration regression test for the resume PDF
  that compiles via `render()` and runs the same FEFF00AD content-stream
  assertion. The structural unit test guards both templates at the
  source-text layer; the integration test currently covers only the
  cover letter. The resume is the higher-traffic artifact.
  (code-reviewer, 2026-05-01)
- [ ] **[DS-3]** Tighten the cover_letter.typ:3 "Typography is aligned
  with curated.typ" comment to enumerate what is and isn't shared
  (font/lang/hyphenate shared; size/justify/leading differ by design).
  (doc-sync-checker, 2026-05-01)
- [ ] **[DS-5]** Decide whether `# Review TODO` should carry an empty
  `## Blockers` heading for symmetry with the global CLAUDE.md
  template, or treat the heading as illustrative.
  (doc-sync-checker, 2026-05-01)
- [ ] **[DS-6]** Add a parenthetical `(vendor name redacted per
  CLAUDE.md Sensitive Content)` to the 2026-04-30 architecture log
  entry so the `<redacted-vendor>` placeholder is self-explanatory in
  isolation. (doc-sync-checker, 2026-05-01)
- [ ] **[AR-2-test]** Test docstring miscounted the fixture word count
  as ~293; actual is ~286. Update the docstring (verbose form: compute
  word count in the test setup and assert an exact range).
  (architecture-reviewer, 2026-05-01)
- [ ] **[AR-3]** Widen the positive-control assertion to also accept
  `­` / `\xad` byte forms in case Typst changes how it emits
  ActualText hex strings in a future release. The current check is
  tied to the `FEFF00AD` BOM-prefixed form. (architecture-reviewer,
  2026-05-01)
- [ ] **[AR-4-test]** Add a one-line content-length-differs assertion
  in the positive control to prove the patched template was actually
  used by Typst (defends against a future renderer refactor that
  resolves templates differently). (architecture-reviewer, 2026-05-01)
- [ ] **[TE-4]** Decide positive-control failure mode on Typst version
  drift: hard-fail (current) vs self-skip with WARNING. Hard-fail
  surfaces unknown changes; skip avoids CI red on what is informational
  about Typst, not about resume-curator. (test-engineer, 2026-05-01)
- [ ] **[TE-5]** Normalize `import pypdf` to the project's
  `from pypdf import PdfReader` style in
  `tests/integration/test_render_pipeline.py` (other call sites use
  the latter). Cosmetic. (test-engineer, 2026-05-01)
- [ ] **[TE-7]** Use `\u`-escape parametrize IDs in
  `tests/unit/test_models.py` for the invisible-char tests. Same
  Edit-tool blocker as SA-2; revisit alongside it.
  (test-engineer, 2026-05-01)
  (test-engineer, 2026-04-30)
