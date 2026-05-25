# Real-World Testing Protocol

A structured protocol for validating resume-curator against real job descriptions
before relying on it for actual applications.

> [!IMPORTANT]
> The `testing/` directory is gitignored (see `.gitignore`). Real job
> postings are not committed to this repo. To run this protocol, source
> your own JD files into a local `testing/jds/` directory under the
> filenames referenced below (e.g. `testing/jds/s1-devops.txt`). Outputs
> go to `testing/results/` (also gitignored). The directory layout
> documented here is a convention, not a shipped fixture.

> [!NOTE]
> This protocol and the Tier 2 LLM judge rubric were calibrated against
> AI-curated output (`curator curate`). Static-mode profiles
> (`source: "static"` in `curation_log.json`) are outside this protocol's
> scope; treat any judge scores on them as exploratory rather than
> calibrated quality signals.

---

## Pre-flight Check (5 min, $0.00)

Before spending any API credits:

```bash
uv run curator --version                                     # Tool installs
typst --version                                              # Typst available
uv run curator curate testing/jds/s1-devops.txt --dry-run    # Portfolio loads, JD parses
uv run curator static --cover-letter --name preflight --no-pdf  # Static path + cover-letter validator exercised
```

Also confirm `.env` has `CURATOR_ALLOW_API_SPEND=true` and a valid API key.

---

## Test Matrix: 10 Job Descriptions

Source real postings from Indeed, Greenhouse, or Lever. Copy full posting text
including responsibilities, requirements, and nice-to-haves.

| ID | Role | Fit Tier | Purpose |
|----|------|----------|---------|
| S1 | Senior DevOps Engineer | Strong | Primary smoke test; closest portfolio match |
| S2 | Senior SRE / Platform Engineer | Strong | Tests reliability/monitoring emphasis |
| S3 | Senior DevSecOps Engineer | Strong | Tests security-shifted-left selection |
| S4 | DevOps/Platform Engineer (AI-Forward) | Strong | AI content selection alongside strong infra match |
| M1 | Cloud Solutions Architect | Moderate | Cloud overlap but less hands-on ops |
| M2 | Security Engineer / AppSec | Moderate | Security focus, not infra-centric |
| M3 | Engineering Manager (DevOps) | Moderate | Tests seniority/management adaptation |
| M4 | AI Platform Engineer | Moderate | AI-primary role; tests honest representation vs fabrication |
| P1 | Frontend / Full-Stack Developer | Poor | Minimal overlap, graceful degradation |
| P2 | Data Scientist / ML Engineer | Poor | Near-zero overlap, no fabrication check |

**JD quality variants** (no extra JDs needed):
- Well-structured: S1, M1 (typical job board formatting)
- Messy/unformatted: Source S2 or M2 from a plaintext or low-format posting
- Very short: P1 (find a terse 5-10 line JD)
- Very long: S3 or M3 (pick one with extensive boilerplate/EEO/benefits)

---

## File Organization

Test materials live in a local `testing/` directory (gitignored):

```
testing/
  jds/
    s1-devops.txt
    s2-sre.txt
    s3-devsecops.txt
    s4-devops-ai.txt
    m1-cloud-architect.txt
    m2-security.txt
    m3-eng-manager.txt
    m4-ai-platform.txt
    p1-frontend.txt
    p2-data-scientist.txt
  results/
    s1-devops-eval.json       # --json output for each case
    ...
  notes.md                    # Human observations per case
```

The entire `testing/` tree is gitignored: real JDs are likely
copyrighted or NDA-sensitive, and per-case results carry candidate
PII. Bootstrap your own copy locally before running the protocol.
Profile output goes to `profiles/` (also gitignored).

---

## Testing Phases

### Phase 1: Strong-Fit Smoke Test (25 min, ~$0.55)

Run S1 first as the primary validation. It is the closest match to the portfolio and
should produce the best result. All Strong-tier runs include `--cover-letter` so the
cover-letter path is exercised on every on-path case.

```bash
# Step 1: Dry run (free)
uv run curator curate testing/jds/s1-devops.txt --dry-run

# Step 2: No-PDF run to validate API response structure (cover letter included)
uv run curator curate testing/jds/s1-devops.txt --cover-letter --no-pdf

# Step 3: Full run with PDF + cover letter
uv run curator curate testing/jds/s1-devops.txt --cover-letter

# Step 4: Save machine-readable eval (Tier 1 + Tier 2). Single paid judge call;
# do not re-run without --json to get a human-readable view, inspect the saved
# JSON directly (for example with `jq` or `python -m json.tool`).
uv run curator eval profiles/<slug>/ --portfolio "$CURATOR_PORTFOLIO_PATH/data" --judge --json \
  > testing/results/s1-devops-eval.json

# Step 5: Open resume.pdf AND cover_letter.pdf and review visually.
# Also `cat cover_letter.txt | head -40` to spot-check the paste-ready
# sidecar (paragraphs separated by blank lines, no internal wrapping,
# ASCII hyphens preserved). See `docs/architecture.md` "Clipboard
# defenses" for the rationale.
```

**Gate:** If any of the following trip on S1, stop and investigate before running S2-S4:
- Tier 1 aggregate < 70, or
- PDF layout defects, or
- `CurationValidationError` raised on the cover letter (structural failure), or
- Human review spots any fabricated metric, company, tech, or year in the cover letter.

Then run S2, S3, and S4 with the same `--cover-letter` pattern. All four should
differentiate between DevOps/SRE/DevSecOps/AI emphasis in their selections. S4 should
visibly integrate AI content (ai-tooling skill group, AI-tagged work highlights, AI
projects) alongside standard infrastructure entries.

### Phase 2: Moderate and Poor Fit (35 min, ~$0.85)

Run M1-M4 (with `--cover-letter`) and P1-P2 (without) in sequence:

For each JD, run `curate` then `eval` against the generated profile directory.
The profile slug is auto-generated as `profiles/YYYY-MM-DD-<company>/` (printed in
the curate output), so substitute accordingly:

```bash
# Moderate tier: cover letter ON (M1-M4)
uv run curator curate testing/jds/m1-cloud-architect.txt --cover-letter
# Note the profile path from the output, then:
uv run curator eval profiles/<date>-<slug>/ \
  --portfolio "$CURATOR_PORTFOLIO_PATH/data" --judge --json \
  > testing/results/m1-cloud-architect-eval.json

# Repeat M-tier pattern for: m2-security, m3-eng-manager, m4-ai-platform

# Poor tier: cover letter OFF, Tier 1 only (P1-P2)
# Rationale: Poor-fit JDs stress the cover-letter path toward fabrication by design;
# the bar here is "doesn't crash", not "quality output". Tier 2 judge is also skipped.
uv run curator curate testing/jds/p1-frontend.txt
uv run curator eval profiles/<date>-<slug>/ \
  --portfolio "$CURATOR_PORTFOLIO_PATH/data" --json \
  > testing/results/p1-frontend-eval.json

# Repeat P-tier pattern for: p2-data-scientist
```

### Phase 3: Input Method Validation (10 min, ~$0.05)

Re-use S1 JD to test stdin and clipboard; additionally exercise the zero-API static
path end-to-end:

```bash
cat testing/jds/s1-devops.txt | uv run curator curate -
uv run curator curate --clipboard   # after copying JD text; skip if pyperclip unavailable
uv run curator static --cover-letter --name sanity-static   # zero-API; validates static path
```

Verify structurally similar output to the file-path run (not identical due to API
non-determinism, but same work entries selected). The `static --cover-letter` smoke
should produce `resume.pdf`, `cover_letter.pdf`, `cover_letter.txt`, and
`data/cover_letter.yaml` with no API call (confirm `source: "static"` and
`cover_letter.enabled=true` in `curation_log.json`).

### Phase 4: Edge Cases (15 min, ~$0.14)

Only if Phases 1-2 pass:

- **Variance:** Re-run S1 and compare eval scores to first run. Curation uses default
  temperature (non-deterministic); how much do selections vary?
- **Very short JD:** Run the short P1 JD. Should produce a reasonable generic resume.
- **Very long JD:** Run the boilerplate-heavy S3/M3 JD. Should ignore noise.

---

## Per-Case Evaluation Protocol

For each of the 10 JDs, observe and record:

### Pipeline Behavior
- Did it complete without errors?
- How many trim steps? Did it converge to the target page count
  (`max_pages`, default 2)?
- Any warnings in the output?

### Tier 1 Eval Scores
- Aggregate score and status
- Per-category scores (jd_alignment, writing_quality, selection_quality, etc.)
- Any FAIL metrics (note which ones)

### Tier 2 Judge Scores
- Aggregate and per-dimension scores (1-5)
- Key dimensions: relevance, keyword_strategy, overall_impression

### Visual PDF Review
- Layout: single column, no overflow, balanced margins
- Header: name, contact, website/GitHub links present
- Professional label: appropriate to the target role
- Summary: natural language, references JD terms, no fabricated claims
- Work entries: ordered by relevance, action verbs, metrics where portfolio has them
- Skills grid: relevant groups, no proficiency levels, sensible keywords
- Dates: human-readable, consistent format

### Human Judgment (record 1-5 scores)
1. Would I send this resume for this role?
2. Does the summary accurately represent my fit?
3. Are the right entries and highlights selected?
4. Is there anything fabricated or inaccurate? (binary yes/no)

---

## Cover Letter Evaluation (Strong + Moderate only)

For each `--cover-letter` run (S1-S4, M1-M4, plus the static `sanity-static` smoke),
evaluate both the structural contract and the human-judgment axes:

### Structural (automated; PASS if `curate` succeeds without `CurationValidationError`)

`validate_cover_letter` in `src/curator/models.py` enforces all of these; a successful
run implies PASS. Confirm by checking the artifacts exist and the log records the flag.

- Word count 250-360 total (hard min, soft max — over-cap warns and ships;
  see `COVER_LETTER_WORD_MIN` / `COVER_LETTER_WORD_MAX` in `rules.py`)
- 40-90 per body paragraph (hard band, see `COVER_LETTER_PARAGRAPH_WORD_MIN` /
  `COVER_LETTER_PARAGRAPH_WORD_MAX`; per-paragraph over-max is a soft warn
  like the total-word cap)
- Exactly 2 body paragraphs (`COVER_LETTER_BODY_MIN_COUNT == MAX_COUNT == 2`
  since 2026-04-24)
- Sign-off in `COVER_LETTER_VALID_SIGN_OFFS` enum
- No em-dashes (`—`)
- No words or phrases from `COVER_LETTER_FORBIDDEN_*` lists
- No literal `[UPPERCASE]` bracketed placeholders (a successful-validator run that
  still contains one means the validator has a bug; treat as BLOCKER)
- `cover_letter.pdf`, `cover_letter.txt`, and `data/cover_letter.yaml` present in
  the profile dir (the `.txt` is the paste-ready sidecar; see
  `docs/architecture.md` "Clipboard defenses")
- `curation_log.json` has `cover_letter.enabled=true` and a positive `word_count`

### Grounding (human)

Every concrete claim (metric, company name, technology, year, project) must trace to
portfolio data. Flag fabrications. The grounding heuristic ([GROUND-1] in TODO.md)
is not yet implemented; this pass is manual.

### Tailoring (human)

- Letter addresses the JD's company / role name explicitly
- First body paragraph references at least one JD-specific requirement
- Keyword-mirroring present but not overwrought (no stuffing)

### Tone: "would send" score (human, 1-5)

Same 1-5 scale as the resume. Target: >=4/5 on at least 3 of 4 Strong cases.

### Single-call invariant (API path only)

`curator curate --cover-letter` must never bill more than one Anthropic message call
per run. Confirm by checking `curation_log.json` tokens (a single input/output pair,
not two). The unit test `tests/unit/test_client.py::TestCurateSingleCallInvariant`
enforces this in CI; a violation in testing is a BLOCKER.

### Failure recovery (API path only)

If `validate_cover_letter` rejects the cover letter on an API run, the client writes
`curation_partial-*.yaml` to the output directory and exits non-zero. Recover without
re-paying for the API call:

```bash
uv run python scripts/rerender.py --partial profiles/curation_partial-*.yaml
```

Log the `request_id` from the error and any tokens burned as a `[TEST-*]` finding.

---

## Cost Summary

| Phase | Curate | Judge | Total |
|-------|--------|-------|-------|
| Phase 1 (4 strong-fit, `--cover-letter`) | ~$0.32 | ~$0.20 | ~$0.52 |
| Phase 2 (4 moderate w/ cover letter + 2 poor w/o) | ~$0.45 | ~$0.20 | ~$0.65 |
| Phase 3 (stdin + clipboard S1 replay, static smoke) | ~$0.04 | $0 | ~$0.04 |
| Phase 4 (3 edge cases + 1 variance re-run) | ~$0.10 | ~$0.05 | ~$0.15 |
| **Total** | | | **~$1.36** |

Costs assume Sonnet 4.6 pricing ($3/MTok input, $15/MTok output). Prompt caching makes
subsequent calls cheaper (~$0.03 vs ~$0.08 with `--cover-letter`). Running all JDs in
one session maximizes cache hits. **Cover-letter cache partitioning**: on-path and
off-path `curate` runs do NOT share prompt cache hits; toggling the flag drops the
cache. Keep flag state constant across back-to-back runs to verify cache reuse.

---

## Success Criteria

### Hard Requirements (all must pass)
1. All 4 strong-fit JDs produce a PDF at the requested page budget
   (`--pages`, default 2) with no layout defects
2. **No fabricated content** in any of the 10 test cases (resume or cover letter)
3. No pipeline crashes or unhandled errors on any well-formed JD
4. Human "would send" resume score >= 4/5 for at least 3 of 4 strong-fit JDs
5. Tier 1 aggregate >= 75 for all strong-fit JDs
6. **Zero** `CurationValidationError` on cover-letter across the 8 on-path runs
7. **Zero** fabricated metrics / companies / technologies / years spotted in cover letters

### Soft Requirements (most should pass)
8. Tier 2 overall_impression >= 4/5 for strong-fit JDs
9. Moderate-fit JDs produce coherent resumes (Tier 1 aggregate >= 60)
10. Poor-fit JDs complete without crashing and do not fabricate
11. Convergence to the requested `max_pages` with minimal trim steps for most JDs
12. `keyword_coverage` passes for strong-fit JDs (requires `--portfolio` flag)
13. Human "would send" cover-letter score >= 4/5 on at least 3 of 4 Strong cases

### Acceptable Failure Modes
- Poor-fit JDs scoring low across the board (expected, not a bug)
- Very short JDs producing lower keyword_strategy scores
- Moderate-fit JDs scoring 3/5 on relevance (honest signal)

### Blockers (stop real use until fixed)
- Any fabrication in any test case (resume or cover letter)
- Pipeline crash on well-formed input
- PDF exceeds the requested `max_pages` after max trim iterations on a strong-fit JD
- Tier 1 aggregate below 60 on a strong-fit JD
- Summary contains claims not in portfolio data
- Any cover letter containing a literal `[UPPERCASE]` bracketed placeholder
  (validator should reject; if it slips through, the validator has a bug)
- Any cover letter containing an em-dash `—` (validator should reject; same)
- Any `curator curate --cover-letter` run that bills more than one Anthropic
  message call (single-call invariant violation)

---

## After Testing

Based on results, the likely next actions are:
- **If issues found:** File GitHub issues, fix, and re-run the affected JDs
- **If passing:** The tool is ready for real-world use; consider Phase D (CI integration)
  for the eval framework to prevent regressions going forward
- **Keep the JDs:** They become an informal regression set for future code changes
  (re-run and compare `--json` output after modifications)
