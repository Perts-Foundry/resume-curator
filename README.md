# resume-curator

Tailor your resume to any job description -- AI selects from your portfolio, you keep full control of the content.

![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

Feed in a YAML portfolio and a plain-text job description. The Claude API ranks, prioritizes, and reorders your existing entries -- it never fabricates content. Typst compiles the result into a tailored PDF (default: 2 pages; pass `--pages 1` for short-form).

## Quick Start

> [!IMPORTANT]
> **Prerequisites:**
> - Python 3.12+ ([install via uv](https://docs.astral.sh/uv/getting-started/installation/))
> - [uv](https://docs.astral.sh/uv/) package manager
> - [Typst](https://github.com/typst/typst#installation) CLI for PDF compilation
> - An [Anthropic API key](https://console.anthropic.com/)
> - A portfolio source directory (see "Bring your own portfolio" below)

> [!NOTE]
> **Bring your own portfolio.** This tool reads YAML career data from
> a directory you control. The default config points at
> `../professional-portfolio-source/` (the author's private portfolio
> repo); override with `CURATOR_PORTFOLIO_PATH` to use your own.
> The portfolio schema (directory layout, per-section files, required
> vs optional fields) is documented in `docs/portfolio-schema.md`. A
> fully synthetic example portfolio is a planned follow-up
> (`examples/portfolio-minimal/`); until then the schema doc plus the
> Pydantic models in `src/curator/models.py` are the canonical
> reference. The `--portfolio` flag is currently `eval`-only; for
> `curate` and `static`, set `CURATOR_PORTFOLIO_PATH`.

1. **Clone the repo and install dependencies:**

   ```bash
   git clone git@github.com:Perts-Foundry/resume-curator.git
   cd resume-curator
   uv sync --locked
   ```

2. **Configure your environment:**

   ```bash
   cp .env.example .env
   ```

   Edit `.env` to add your API key, authorize spending, and point the tool
   at your portfolio:

   ```
   CURATOR_ANTHROPIC_API_KEY=sk-ant-your-key-here
   CURATOR_ALLOW_API_SPEND=true
   CURATOR_PORTFOLIO_PATH=/path/to/your/portfolio
   ```

   `CURATOR_PORTFOLIO_PATH` is required for `curate` and `static`. See
   the "Bring your own portfolio" callout above for the schema.

3. **Curate a resume:**

   ```bash
   uv run curator curate path/to/job-description.txt
   ```

4. **Find your output** in `profiles/<date>-<company-slug>/resume.pdf` (e.g., `profiles/2026-03-20-acme-corp/resume.pdf`).

## Usage

### Curating a Resume

```bash
uv run curator curate job-description.txt
```

The pipeline reads your portfolio data, sends it with the job description to the Claude API, and compiles a PDF via Typst. If the result exceeds the target page count (default: 2; override with `--pages 1..5`), the renderer trims lowest-value content and re-compiles until it fits.

#### Providing the job description

| Method | Command | Notes |
|--------|---------|-------|
| **File** | `uv run curator curate job-description.txt` | Default -- pass a text file path |
| **Stdin (pipe)** | `cat jd.txt \| uv run curator curate -` | Pipe from any command |
| **Stdin (paste)** | `uv run curator curate -` | Type or paste, then Ctrl+D to finish |
| **Clipboard** | `uv run curator curate --clipboard` | Reads system clipboard (requires `pyperclip`: `uv sync --extra clipboard`) |

All input methods apply the same validation: non-empty, max 50,000 characters. File and clipboard are mutually exclusive.

#### Mode flags

```bash
# Zero-cost preview: show what would be sent without calling the API
uv run curator curate job-description.txt --dry-run

# Call the API but skip PDF compilation (writes all other artifacts)
uv run curator curate job-description.txt --no-pdf

# Enable debug logging (timestamps, source locations, SDK messages)
uv run curator curate job-description.txt --verbose
```

`--dry-run` and `--no-pdf` are mutually exclusive. Both work with any input method.

#### Output

Output lands in `profiles/<date>-<company-slug>/`:

| File | Purpose |
|------|---------|
| `resume.pdf` | Final compiled resume |
| `curated.yaml` | Curation decisions (summary, label, rankings) |
| `job_description.txt` | Original JD for reference |
| `curation_log.json` | API metadata: model, tokens, cache stats, timestamp |
| `layout.yaml` | Section ordering for Typst |
| `data/*.yaml` | Per-section YAML files consumed by Typst |

> [!NOTE]
> The `profiles/` directory is gitignored. Outputs are ephemeral and per-machine.

### Generating a Static Resume

For a polished, general-purpose resume that does not require a job description and makes zero API calls, use the `static` subcommand. It synthesizes a curation deterministically from your portfolio and renders the same Typst template as `curate`.

```bash
# 2-page general resume (default; --name "general")
uv run curator static

# Short-form 1-page resume
uv run curator static --pages 1 --name short

# Other page budgets (1..5)
uv run curator static --pages 3 --name long-form

# Cap each work entry to N highlights for predictable output
uv run curator static --pages 1 --max-highlights 3

# JSON preview (no PDF, no profile dir written)
uv run curator static --json | jq .
```

**`--json` output schema** (`static-1.0`):

```json
{
  "source": "static",
  "schema_version": "static-1.0",
  "curation": { /* matches curator.models.ResumeCuration */ }
}
```

The envelope makes provenance explicit so downstream consumers can branch on `source` without inferring from absence of token fields. The inner `curation` object is the same `ResumeCuration` shape produced by `curate`.

Selection rules (no AI):

- **Summary / label**: from `basics.summary` and `basics.label` verbatim (truncated to schema limits when oversized).
- **Work highlights**: every portfolio highlight per entry, in portfolio order. `--max-highlights N` caps each entry.
- **Skills**: every skill group with all keywords, in portfolio order.
- **Projects**: sorted by `weight` ascending (stable, ties fall back to portfolio order).

Output mirrors `curate` with two differences: `curation_log.json` carries `source: "static"` and `model: "n/a"` with zero token counts, and `mode.txt` replaces `job_description.txt` as the per-source descriptor.

`static` and `curate --dry-run` are not interchangeable: `--dry-run` previews without producing a PDF, while `static` produces a real PDF (also at zero API cost).

### Evaluating Quality

Tier 1 runs 60 deterministic metrics locally (zero API cost):

| Category | Metrics | Weight | Examples |
|----------|:-------:|:------:|----------|
| Content Density | 3 | 10% | Highlights per entry, total content volume |
| Selection Quality | 11 | 15% | Entry count, priority ordering, skill coverage |
| Writing Quality | 16 | 25% | Action verbs, tense consistency, red-flag words |
| JD Alignment | 6 (4 scored + 2 informational) | 25% | Keyword coverage, summary relevance; `jd_match_rate` and `acronym_expansion_pairs` marked `informational=True` (roll into `PortfolioFitReport` sidecar) |
| Date & Format | 3 | 5% | Date formatting, chronological ordering |
| PDF Output | 11 (10 scored + 1 informational) | 15% | Page count, margins, font consistency; `font_embedding_valid` marked `informational=True` (deferred-detection stub) |
| Template Correctness | 9 | 5% | Section headers, link formatting |

```bash
# Evaluate a specific profile
uv run curator eval profiles/2026-03-20-acme-corp/

# Include keyword coverage metrics (requires portfolio path)
uv run curator eval profiles/2026-03-20-acme-corp/ --portfolio /path/to/your/portfolio/data

# JSON output for scripting
uv run curator eval profiles/2026-03-20-acme-corp/ --json
```

**Tier 2** adds 8-dimension LLM judge scoring via Claude Sonnet (~$0.05/eval):

```bash
# Judge a single profile
uv run curator eval profiles/2026-03-20-acme-corp/ --judge

# Judge all golden cases with calibration stats
uv run curator eval --golden --judge --calibrate
```

> [!NOTE]
> `--judge` requires `CURATOR_ALLOW_API_SPEND=true` and an API key.

**Golden dataset** -- 24 synthetic cases across 4 fit tiers for regression testing:

```bash
uv run curator eval --golden
uv run curator eval --golden --calibrate --apply
```

See the [Architecture Guide](docs/architecture.md) for system design and the [Testing Protocol](docs/testing-protocol.md) for validation methodology.

<details>
<summary>Alternative invocation methods</summary>

```bash
# Recommended: uv run (auto-syncs dependencies from uv.lock)
uv run curator curate job.txt

# With an activated virtual environment
source .venv/bin/activate
curator curate job.txt

# Explicit interpreter
python -m curator curate job.txt
```

</details>

## How It Works

Two pipelines share the same renderer; they differ in how a `ResumeCuration` is produced:

```
                      ┌─► Claude API ──► Curated YAML ──┐
Portfolio YAML + JD ──┤                                 ├──► Typst ──► PDF
                      └─► Static synthesis (zero API) ──┘
```

1. **Load** -- reads career data from your portfolio source directory (YAML files covering work, skills, projects, certificates, education, and more; see [`docs/portfolio-schema.md`](docs/portfolio-schema.md) for the schema)
2. **Curate** -- either:
   - `curate` sends portfolio + JD to the Claude API and gets back structured rankings (summary, label, work highlight order, skill filtering, project ranking) via constrained decoding against a portfolio-derived JSON schema, or
   - `static` synthesizes the same `ResumeCuration` deterministically from portfolio data (no API call)
3. **Render** -- writes per-section YAML and compiles a PDF using the Typst template at `templates/curated.typ`
4. **Enforce** -- if the PDF exceeds the target page count, the renderer trims lowest-value content and re-compiles (up to `max_trim_iterations` passes; a WARNING logs at 15 as a convergence signal)

For the full data flow, structured output schema, prompt architecture, cost model, and design decisions, see the [Architecture Guide](docs/architecture.md).

## Configuration

Settings are resolved in priority order: **CLI arguments > environment variables > `.env` file > defaults**. All environment variables use the `CURATOR_` prefix.

| Variable | Default | Description |
|----------|---------|-------------|
| `CURATOR_ANTHROPIC_API_KEY` | *(required)* | Anthropic API key |
| `CURATOR_ALLOW_API_SPEND` | `false` | Must be `true` to authorize API calls |
| `CURATOR_MODEL` | `claude-sonnet-4-6` | Claude model for curation (alias by default; override with a snapshot ID for reproducibility) |
| `CURATOR_MAX_TOKENS` | `4096` | Maximum output tokens (256-8192) |
| `CURATOR_EFFORT` | *(none)* | Response quality: low / medium / high / max |
| `CURATOR_MAX_PAGES` | `2` | Target page count; renderer trims if exceeded (1-5). 2 is the typical submission shape for both `curate` and `static`; pass `--pages 1` for short-form, 3-5 for executive/academic CVs |
| `CURATOR_MAX_TRIM_ITERATIONS` | `150` | Maximum renderer trim iterations (1-200); WARNING logged at 15 as convergence signal |
| `CURATOR_JUDGE_MODEL` | `claude-sonnet-4-6` | Model for Tier 2 judge evaluation |
| `CURATOR_SUMMARY_MANDATORY_MENTION` | *(author's identity by default)* | Phrase the AI must include verbatim in every generated resume summary. Forks should set this to their own mandatory mention (e.g. `"founder of YourCo"`) or to an empty string to disable. |

> [!WARNING]
> The Anthropic API is billed separately from Claude Pro/Max/Team subscriptions.
> You need an API key from [console.anthropic.com](https://console.anthropic.com/).
> `CURATOR_ALLOW_API_SPEND` must be set to `true` explicitly -- this safety guard
> prevents surprise charges from automated workflows.

See the [Configuration section](docs/architecture.md#configuration) in the architecture guide for the complete settings list and priority hierarchy.

## Development

```bash
# Install all dependencies (including dev)
uv sync --locked

# Lint and format
uv run ruff check .
uv run ruff format --check .

# Type check (strict mode)
uv run mypy src/

# Run tests (excludes @pytest.mark.llm by default)
uv run pytest

# Run with coverage
uv run pytest --cov --cov-report=term-missing

```

Coding standards are encoded in `pyproject.toml` (ruff, mypy, pytest configs); CI runs `ruff check`, `ruff format --check`, `mypy src/`, `pytest`, golden eval, `pip-audit`, gitleaks, and trufflehog. See the [CI/CD section](docs/architecture.md#cicd) in the architecture guide for the full pipeline.

## Project Status

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 1 -- MVP | Complete | Full pipeline: JD in, PDF out |
| Static Resume Mode | Complete | Zero-API path: `curator static` produces a polished general-purpose PDF |
| Eval Phases 0--C | Complete | 60 metrics, 24 golden cases, LLM judge |
| Eval Phase D -- CI integration | Planned | Automated eval gating in CI pipeline |
| Phase 2 -- Job Discovery | Planning | Source, score, and surface job postings |
| Phase 3 -- Application Submission | Planning | Browser automation with human-in-the-loop |

See [TODO.md](TODO.md) for the full backlog grouped by phase.

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture Guide](docs/architecture.md) | System design, data flow, API integration, cost model, and design decisions |
| [Portfolio Schema](docs/portfolio-schema.md) | Directory layout and per-section reference for authoring your own portfolio source |
| [Testing Protocol](docs/testing-protocol.md) | Real-world validation protocol (requires you to source your own JD content into a local `testing/jds/` directory; not committed to this repo) |
| [TODO](TODO.md) | Planned work and backlog (internal working notes; references to historical PRs may not resolve) |

## Costs and API Spending

This tool calls the Anthropic API, which bills per token. The `CURATOR_ALLOW_API_SPEND` environment variable must be set to `true` explicitly before any API call will execute. This safety guard prevents surprise charges from automated workflows or accidental invocations. Running `--dry-run` is always free.

## Security

See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## License

[MIT](LICENSE)
