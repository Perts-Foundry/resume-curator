# Golden Dataset — Sensitive Data Policy

Golden YAML files in this directory contain **synthetic data only**:

- All job descriptions use **fictional company names** (e.g., "Nexus Systems", "Vantage Cloud")
- All work entries use **abstracted IDs** and fictional employer names
- The candidate name is "Alex Morgan" (fictional)
- Contact info uses `example.com` (IANA-reserved) and `555-` phone numbers

**Never commit:**
- Real company names or job posting text
- Real person names or employment history
- Actual portfolio entry IDs that reveal employment details

If you need to test with real data, use the `profiles/` directory (gitignored).

## Calibration Workflow

Golden baselines define the minimum acceptable aggregate score for each case.
When metrics change (added, removed, or thresholds adjusted), re-calibrate:

```bash
# Preview proposed baselines (no writes):
curator eval --golden --calibrate

# Auto-update all golden YAML files with new baselines:
curator eval --golden --calibrate --apply

# Verify everything passes:
pytest -m golden
```

**How baselines are computed:** `floor(actual_score - BASELINE_MARGIN)` where
`BASELINE_MARGIN` is 5 points (defined in `rules.py`). This gives each case a
5-point buffer to absorb minor metric drift without failing regression tests.

## Prerequisites

- **Typst** must be installed for golden regression tests (renders PDFs for the
  11 PDF output metrics). Tests skip automatically if Typst is unavailable.
- Golden tests use `$HOME/.cache/curator-golden-tests/` for temp files because
  snap-confined Typst cannot access `/tmp`.

## Tier 2 Judge Calibration

Golden cases include an optional `human_scores` field containing per-dimension
scores (1-5 scale) for the 8 Tier 2 judge dimensions. These are used to verify
that the LLM judge produces scores within acceptable tolerance of human
expectations.

**Prerequisites:**
- `CURATOR_ALLOW_API_SPEND=true` must be set (the spend guard blocks judge
  API calls by default to prevent surprise charges)
- A valid `CURATOR_ANTHROPIC_API_KEY` must be available

**Running calibration:**

```bash
# Run judge calibration (preview only, no writes):
curator eval --golden --judge --calibrate

# Apply judge scores as human_scores to golden YAML files:
curator eval --golden --judge --calibrate --apply

# Run the real-API calibration test suite:
CURATOR_ALLOW_API_SPEND=true uv run pytest tests/eval/test_judge_calibration.py -m llm
```

**Two-tier tolerance:** When comparing judge scores against `human_scores`:
- `abs(judge - human) > 1` produces a WARNING (flag for investigation)
- `abs(judge - human) > 2` produces an ERROR (test-failing regression)

## Skipped Metrics

`keyword_coverage` is always skipped in golden eval via the explicit
`GOLDEN_SKIP_METRICS = frozenset({"keyword_coverage"})` allowlist in
`golden.py` because it requires full portfolio data to compute meaningful
scores, and golden cases are self-contained without the portfolio.

`jd_match_rate` is also effectively skipped, but via a different mechanism:
it is marked `informational=True` and is excluded from the scored aggregate
by the informational filter (not by name). The `GOLDEN_SKIP_METRICS`
name-list entry was narrowed `{keyword_coverage, jd_match_rate} -> {keyword_coverage}`
on 2026-04-24 because it was redundant. Adding a future portfolio-dependent
informational metric does NOT require touching `GOLDEN_SKIP_METRICS`; setting
`informational=True` is sufficient.
