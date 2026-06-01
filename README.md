# resume-curator

**Tailor your resume to any job posting in one command, without letting AI make things up.**

![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

## What is this?

Applying to jobs means re-tailoring your resume for every posting: surfacing the
right experience, trimming the rest, and re-formatting it to fit a page or two.
It is tedious, and most "AI resume" tools fix the tedium by inventing
accomplishments you never had.

resume-curator does the tailoring without the invention. You keep all of your
career history in one place (a "portfolio"). For a given job posting, the tool
asks Claude to **select, prioritize, and reorder your existing entries** so the
most relevant experience rises to the top, then compiles a finished PDF. It never
writes new accomplishments, never fabricates metrics, and never adds skills you
did not list. Every word on the page came from you.

Concretely: you give it a job description and it gives you back
`resume.pdf`, already trimmed to fit and tailored to that role. The same
portfolio can produce a different, well-targeted resume for every job you apply
to. There is also a zero-cost mode that builds a polished general-purpose resume
with no AI call at all.

**Who it's for:** anyone who applies to enough jobs that hand-tailoring each
resume is a chore, and who is comfortable running a command-line tool.

### Key terms

| Term | What it means here |
|------|--------------------|
| **Portfolio source** | A directory of YAML files holding *all* your career data (every job, skill, project, certificate). You author it once; the tool reads it. |
| **Job description (JD)** | The plain-text posting you are tailoring toward. Passed as a file, pipe, or clipboard. |
| **Curation** | The selection and ranking decisions (which highlights, in what order) that turn your full portfolio into one targeted resume. |
| **Typst** | The typesetting engine that compiles the curated content into the actual PDF. You install it once. |
| **`curate` vs `static`** | `curate` calls the Claude API to tailor to a specific JD (costs a few cents). `static` builds a general resume from your portfolio with **no API call** (free). |

## What you need

- **Python 3.12+** and the [**uv**](https://docs.astral.sh/uv/) package manager
  ([install uv](https://docs.astral.sh/uv/getting-started/installation/)).
- The [**Typst**](https://github.com/typst/typst#installation) CLI, for PDF
  compilation.
- An [**Anthropic API key**](https://console.anthropic.com/) (only for `curate`
  and the optional eval judge; `static` needs no key).
- A **portfolio source directory** that matches the schema in
  [`docs/portfolio-schema.md`](docs/portfolio-schema.md).

> [!NOTE]
> **Bring your own portfolio.** The tool reads career data from a directory you
> control. The default config points at `../professional-portfolio-source/` (the
> author's private repo); set `CURATOR_PORTFOLIO_PATH` to point at yours. A fully
> synthetic `examples/portfolio-minimal/` is a planned follow-up; until then the
> schema doc plus the Pydantic models in `src/curator/models.py` are the canonical
> reference.

## Quick Start

1. **Clone and install:**

   ```bash
   git clone git@github.com:Perts-Foundry/resume-curator.git
   cd resume-curator
   uv sync --locked
   ```

2. **Configure your environment:**

   ```bash
   cp .env.example .env
   ```

   Edit `.env` to add your API key, authorize spending, and point at your
   portfolio:

   ```
   CURATOR_ANTHROPIC_API_KEY=sk-ant-your-key-here
   CURATOR_ALLOW_API_SPEND=true
   CURATOR_PORTFOLIO_PATH=/path/to/your/portfolio
   ```

   `CURATOR_PORTFOLIO_PATH` is required for `curate` and `static`.
   `CURATOR_ALLOW_API_SPEND` must be `true` before any paid call runs (a safety
   guard against surprise charges).

3. **Curate a resume:**

   ```bash
   uv run curator curate path/to/job-description.txt
   ```

4. **Find your output** at
   `profiles/<date>-<company-slug>/resume.pdf`
   (for example, `profiles/2026-03-20-acme-corp/resume.pdf`).

> [!TIP]
> Want a PDF without spending anything? Run `uv run curator static` for a
> general-purpose resume, or add `--dry-run` to `curate` to preview what would be
> sent. Both are free.

## How It Works

Two pipelines share the same renderer; they differ only in how the curation
decisions are produced:

```
                      ┌─► Claude API ──► Curated YAML ──┐
Portfolio YAML + JD ──┤                                 ├──► Typst ──► PDF
                      └─► Static synthesis (zero API) ──┘
```

1. **Load** the career data from your portfolio source directory.
2. **Curate**, one of two ways:
   - `curate` sends portfolio + JD to the Claude API and gets back structured
     rankings (summary, label, work-highlight order, skill filtering, project
     ranking).
   - `static` synthesizes the same shape deterministically from portfolio data,
     with no API call.
3. **Render** the per-section YAML and compile a PDF via the Typst template.
4. **Enforce** the page budget: if the PDF runs long, the renderer trims the
   lowest-value content and re-compiles until it fits.

For the full data flow, structured-output schema, prompt architecture, and cost
model, see the [Architecture Guide](docs/architecture.md).

## Commands at a glance

| Command | What it does | Common invocation |
|---------|--------------|-------------------|
| [`curate`](#curator-curate) | Tailor a resume to a job description via the Claude API (paid). | `uv run curator curate jd.txt` |
| [`static`](#curator-static) | Build a polished general-purpose resume with zero API cost. | `uv run curator static` |
| [`eval`](#curator-eval) | Score a curated profile on quality metrics (Tier 1 free, Tier 2 paid). | `uv run curator eval profiles/<dir>/` |
| [`publish`](#curator-publish) | Copy a profile's upload-ready files to another directory. | `uv run curator publish profiles/<dir> DEST` |

Two global options apply to every command:

| Option | Description |
|--------|-------------|
| `--version`, `-v` | Print the installed version and exit. |
| `--verbose` | Enable DEBUG console logging (timestamps, source locations, SDK messages). The on-disk log always captures all levels regardless. |

## `curator curate`

Tailor a resume to a specific job description. This is the paid path: one Claude
API call per run.

```bash
uv run curator curate job-description.txt
```

### Providing the job description

| Method | Command | Notes |
|--------|---------|-------|
| **File** | `uv run curator curate job-description.txt` | Default: pass a text file path. |
| **Stdin (pipe)** | `cat jd.txt \| uv run curator curate -` | Pipe from any command. |
| **Stdin (paste)** | `uv run curator curate -` | Type or paste, then Ctrl+D to finish. |
| **Clipboard** | `uv run curator curate --clipboard` | Reads the system clipboard (requires `pyperclip`: `uv sync --extra clipboard`). |

All methods apply the same validation: non-empty, max 50,000 characters. A file
argument and `--clipboard` are mutually exclusive.

### Options

| Option | Description |
|--------|-------------|
| `--dry-run` | Preview what would be sent to the API (portfolio counts, model, estimated cost). No call, no charge. |
| `--no-pdf` | Make the API call and write all artifacts, but skip PDF compilation. |
| `--clipboard` | Read the job description from the system clipboard instead of a file/stdin. |
| `--pages N` | Target page count, 1 to 5 (default 2). Pass `--pages 1` for short-form. |
| `--cover-letter` / `--no-cover-letter` | Also generate a tailored cover letter **in the same API call** (a few extra output tokens, no second call). Off by default. Produces `cover_letter.pdf`, `cover_letter.txt`, and `data/cover_letter.yaml`. |
| `--cache-ttl {5m,1h}` | Prompt-cache TTL on the portfolio block (overrides `CURATOR_CACHE_TTL`). `1h` (default) favors multi-run sessions; pass `5m` for one-off runs to avoid the 2x write penalty. |
| `--publish DIR` | After rendering, copy the upload-ready files into `DIR/<profile>/`. See [`publish`](#curator-publish). Canonical form puts the JD first: `curator curate jd.txt --publish DIR`. |

`--dry-run` and `--no-pdf` are mutually exclusive. Every option works with any
input method.

```bash
# Zero-cost preview: show what would be sent, make no call
uv run curator curate job-description.txt --dry-run

# Call the API but skip PDF compilation
uv run curator curate job-description.txt --no-pdf

# One-page resume plus a tailored cover letter, in a single paid call
uv run curator curate job-description.txt --pages 1 --cover-letter
```

### Output

Output lands in `profiles/<date>-<company-slug>/`:

| File | Purpose |
|------|---------|
| `resume.pdf` | Final compiled resume. |
| `curated.yaml` | Curation decisions (summary, label, rankings). |
| `job_description.txt` | The original JD, for reference (API path). |
| `curation_log.json` | API metadata: model, tokens, cache stats, timestamp. |
| `layout.yaml` | Section ordering for Typst. |
| `data/*.yaml` | Per-section YAML files consumed by Typst. |
| `cover_letter.pdf` | Compiled cover letter (only with `--cover-letter`). |
| `cover_letter.txt` | Paste-ready plain-text sidecar (only with `--cover-letter`; lands even under `--no-pdf`). |
| `data/cover_letter.yaml` | Cover letter content (only with `--cover-letter`). |

> [!NOTE]
> The `profiles/` directory is gitignored. Outputs are ephemeral and per-machine.

## `curator static`

Build a polished, general-purpose resume that needs no job description and makes
**zero API calls**. It selects portfolio content deterministically and renders
the same Typst template as `curate`.

```bash
uv run curator static
```

### Options

| Option | Description |
|--------|-------------|
| `--name TEXT` | Output slug and audit descriptor (default `general`). Non-alphanumerics collapse to `-`. |
| `--pages N` | Target page count, 1 to 5 (default 2). Pass `--pages 1` for short-form. |
| `--max-highlights N` | Cap each work entry to at most N bullets (1 to 50). Off by default (show all). |
| `--no-pdf` | Write audit artifacts but skip PDF compilation. |
| `--json` | Print a JSON envelope to stdout and exit without writing a profile directory (see below). |
| `--cover-letter` / `--no-cover-letter` | Render the candidate-authored cover letter from `<portfolio>/data/cover-letter.yaml` verbatim. `--name` does not affect letter content. No API call. |
| `--publish DIR` | Copy upload-ready files into `DIR/<profile>/` after rendering. See [`publish`](#curator-publish). |

`--json` and `--no-pdf` are mutually exclusive.

```bash
# 2-page general resume (default; --name "general")
uv run curator static

# Short-form 1-page resume
uv run curator static --pages 1 --name short

# Cap each work entry to 3 highlights for predictable output
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

The envelope makes provenance explicit so downstream consumers can branch on
`source` without inferring it from the absence of token fields.

**Selection rules (no AI):**

- **Summary / label**: from `basics.summary` and `basics.label` verbatim
  (truncated to schema limits when oversized).
- **Work highlights**: every portfolio highlight per entry, in portfolio order.
  `--max-highlights N` caps each entry.
- **Skills**: every skill group with all keywords, in portfolio order.
- **Projects**: sorted by `weight` ascending (stable; ties fall back to portfolio
  order).

Output mirrors `curate` with two differences: `curation_log.json` carries
`source: "static"` and `model: "n/a"` with zero token counts, and `mode.txt`
replaces `job_description.txt` as the per-source descriptor.

> [!NOTE]
> `static` is not `curate --dry-run`. `--dry-run` previews without producing a
> PDF; `static` produces a real PDF, also at zero API cost.

## `curator eval`

Score a curated profile on quality metrics. **Tier 1** runs 60 deterministic
checks locally at zero API cost; **Tier 2** adds an LLM judge (paid).

| Category | Metrics | Weight | Examples |
|----------|:-------:|:------:|----------|
| Content Density | 3 | 10% | Highlights per entry, total content volume |
| Selection Quality | 11 | 15% | Entry count, priority ordering, skill coverage |
| Writing Quality | 16 | 25% | Action verbs, tense consistency, red-flag words |
| JD Alignment | 6 (4 scored + 2 informational) | 25% | Keyword coverage, summary relevance |
| Date & Format | 3 | 5% | Date formatting, chronological ordering |
| PDF Output | 11 (10 scored + 1 informational) | 15% | Page count, margins, font consistency |
| Template Correctness | 9 | 5% | Section headers, link formatting |

### Options

| Option | Description |
|--------|-------------|
| `--portfolio PATH` | Portfolio data directory; enables keyword-coverage metrics. |
| `--skip TEXT` | Metric name to skip (repeatable). |
| `--golden` | Run against the golden dataset for regression testing (instead of a profile). |
| `--golden-dir PATH` | Golden dataset directory (default `tests/eval/golden/`). |
| `--calibrate` | Show proposed baselines for golden cases (use with `--golden`). |
| `--apply` | Write baselines into golden YAML files (use with `--calibrate`). |
| `--judge` | Run Tier 2 LLM judge evaluation (requires an API key; ~$0.05 per eval). |
| `--json` | Output results as JSON to stdout (machine-readable). |
| `--pages N` | Override the inferred `max_pages` used for band selection (1 to 5). Rejected with `--golden` (each golden case owns its own page budget). |

```bash
# Evaluate a specific profile (Tier 1, free)
uv run curator eval profiles/2026-03-20-acme-corp/

# Include keyword-coverage metrics (requires portfolio path)
uv run curator eval profiles/2026-03-20-acme-corp/ --portfolio /path/to/your/portfolio/data

# JSON output for scripting
uv run curator eval profiles/2026-03-20-acme-corp/ --json
```

**Tier 2** adds 8-dimension LLM judge scoring via Claude Haiku 4.5
(approximately $0.05 per eval):

```bash
# Judge a single profile
uv run curator eval profiles/2026-03-20-acme-corp/ --judge

# Judge all golden cases with calibration stats
uv run curator eval --golden --judge --calibrate
```

> [!NOTE]
> `--judge` requires `CURATOR_ALLOW_API_SPEND=true` and an API key.

**Golden dataset:** 24 synthetic cases across 4 fit tiers, for regression
testing.

```bash
uv run curator eval --golden
uv run curator eval --golden --calibrate --apply
```

See the [Testing Protocol](docs/testing-protocol.md) for the full validation
methodology.

## `curator publish`

Copy a profile's upload-ready artifacts (`resume.pdf`, `cover_letter.pdf`,
`cover_letter.txt`) into another directory. This does **not** publish to any
registry or remote; "publish" here means "make these files reachable for upload",
which is the common need when running under WSL (see
[Troubleshooting](#troubleshooting)).

There are two ways to publish:

- **Inline, on a fresh run** with `--publish DIR` on `curate` or `static`.
- **After the fact**, re-publishing a past profile with the `publish` subcommand.

```bash
# Inline on a fresh run (job description first, then --publish DIR):
uv run curator curate jd.txt --publish /mnt/c/Users/<you>/Downloads/resume-curator --cover-letter
uv run curator static --name general --publish /mnt/c/Users/<you>/Downloads/resume-curator

# Re-publish a past profile (overwrites the previous publish):
uv run curator publish profiles/2026-05-27-acme /mnt/c/Users/<you>/Downloads/resume-curator
```

The `publish` subcommand takes two required arguments: the profile directory and
the destination. Files land at `DESTINATION/<profile>/`.

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

## Configuration

Settings resolve in priority order: **CLI arguments > environment variables >
`.env` file > defaults**. Every environment variable uses the `CURATOR_` prefix.

| Variable | Default | Description |
|----------|---------|-------------|
| `CURATOR_ANTHROPIC_API_KEY` | *(required for paid calls)* | Anthropic API key. |
| `CURATOR_ALLOW_API_SPEND` | `false` | Must be `true` to authorize any API call. |
| `CURATOR_PORTFOLIO_PATH` | `../professional-portfolio-source` | Portfolio source repo root. Required for `curate` and `static`. |
| `CURATOR_OUTPUT_DIR` | `profiles` | Directory for per-job output. |
| `CURATOR_MODEL` | `claude-sonnet-4-6` | Claude model for curation (alias by default; override with a snapshot ID for reproducibility). |
| `CURATOR_MAX_TOKENS` | `4096` | Maximum output tokens (256 to 8192). |
| `CURATOR_EFFORT` | *(none)* | Response quality: low / medium / high / max. |
| `CURATOR_CACHE_TTL` | `1h` | Prompt-cache TTL on the portfolio block: `5m` or `1h`. See [Architecture: Prompt Caching](docs/architecture.md). |
| `CURATOR_MAX_PAGES` | `2` | Target page count; renderer trims if exceeded (1 to 5). Pass `--pages 1` for short-form, 3 to 5 for executive/academic CVs. |
| `CURATOR_MAX_TRIM_ITERATIONS` | `150` | Maximum renderer trim iterations (1 to 200); a WARNING logs at 15 as a convergence signal. |
| `CURATOR_API_MAX_RETRIES` | `5` | Max retry attempts for Anthropic API calls (1 to 10; SDK built-in retries). |
| `CURATOR_JUDGE_MODEL` | `claude-haiku-4-5` | Model for Tier 2 judge evaluation. |
| `CURATOR_JUDGE_EFFORT` | *(none)* | Effort level for judge quality tuning. Leave unset for Haiku 4.5. |
| `CURATOR_SUMMARY_MANDATORY_MENTION` | *(author's identity by default)* | Phrase the AI must include verbatim in every generated summary. Forks should set their own, or an empty string to disable. |

> [!WARNING]
> The Anthropic API is billed separately from Claude Pro/Max/Team subscriptions.
> You need an API key from [console.anthropic.com](https://console.anthropic.com/).
> `CURATOR_ALLOW_API_SPEND` must be set to `true` explicitly: this safety guard
> prevents surprise charges from automated workflows.

See the [Configuration section](docs/architecture.md#configuration) in the
architecture guide for the complete settings list and priority hierarchy.

## Costs and API spending

This tool calls the Anthropic API, which bills per token. `curate` and
`eval --judge` cost real money (a curate run is roughly $0.07 first / $0.02
cached on Sonnet; a judge eval is approximately $0.05 on Haiku 4.5).
`CURATOR_ALLOW_API_SPEND` must be `true` before any API call runs.

**Free at all times:** `curator static`, `curator curate --dry-run`, and
`curator eval` without `--judge`. Reach for `static` whenever you want a PDF and
do not need JD-specific tailoring.

## Troubleshooting

### Uploading from WSL: "this folder contains system files"

Windows browser file pickers (Edge/Chrome) refuse to upload files from
`\\wsl.localhost\...` (or `\\wsl$\...`) paths under Chromium's blocked-paths
policy. The error reads "this folder contains system files", but the trigger is
the UNC path itself, not any file attribute; PowerShell confirms every file in
your profile directory reports `Attributes=Normal`.

**Workarounds:**

1. **One-off**: copy the PDF onto the Windows drive before uploading.

   ```bash
   cp profiles/<profile>/resume.pdf /mnt/c/Users/<you>/Downloads/
   ```

2. **Recurring**: pass `--publish DIR` on each run, or re-publish a past profile
   with `curator publish`. See [`curator publish`](#curator-publish) for the full
   workflow. The destination is always given inline (there is no configured
   default).

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

Coding standards are encoded in `pyproject.toml` (ruff, mypy, pytest configs);
CI runs `ruff check`, `ruff format --check`, `mypy src/`, `pytest`, golden eval,
`pip-audit`, gitleaks, and trufflehog. See the
[CI/CD section](docs/architecture.md#cicd) in the architecture guide.

## Project Status

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 1, MVP | Complete | Full pipeline: JD in, PDF out. |
| Static Resume Mode | Complete | Zero-API path: `curator static`. |
| Eval Phases 0 to C | Complete | 60 metrics, 24 golden cases, LLM judge. |
| Eval Phase D, CI integration | Planned | Automated eval gating in CI. |
| Phase 2, Job Discovery | Planning | Source, score, and surface job postings. |
| Phase 3, Application Submission | Planning | Browser automation with human-in-the-loop. |

See [TODO.md](TODO.md) for the full backlog grouped by phase.

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture Guide](docs/architecture.md) | System design, data flow, API integration, cost model, design decisions. |
| [Portfolio Schema](docs/portfolio-schema.md) | Directory layout and per-section reference for authoring your portfolio source. |
| [Testing Protocol](docs/testing-protocol.md) | Real-world validation protocol (you source your own JD content into a local `testing/jds/`; not committed). |
| [TODO](TODO.md) | Planned work and backlog (internal notes; historical PR references may not resolve). |

## Security

See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## License

[MIT](LICENSE)
