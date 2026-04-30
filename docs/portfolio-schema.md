# Portfolio Schema

This document describes the directory layout and per-file schema that
`curator curate` and `curator static` expect for a portfolio source.

The portfolio source is a directory you control (set
`CURATOR_PORTFOLIO_PATH` to point at it). The loader reads each section
from its own YAML file under a `data/` subdirectory and validates it
against a Pydantic model. The Pydantic models in `src/curator/models.py`
are the canonical schema; this doc summarizes the shape with a minimal
example for each section.

## Directory layout

```
your-portfolio/
  data/
    basics.yaml          # required
    work.yaml            # optional list (recommended)
    skills.yaml          # optional list (recommended)
    projects.yaml        # optional list
    education.yaml       # optional list
    certificates.yaml    # optional list
    volunteer.yaml       # optional list
    publications.yaml    # optional list
    languages.yaml       # optional list
    services.yaml        # optional list
    interests.yaml       # optional single object
    cover-letter.yaml    # optional single object (only consumed by
                         # `curator curate --cover-letter` static path
                         # and by `curator static --cover-letter`)
```

Only `basics.yaml` is required. Missing optional files are treated as
empty (lists default to `[]`, single-object sections default to `None`).

The exact field set, validation constraints, and required-vs-optional
defaults for each section live in the Pydantic models in
`src/curator/models.py`. Treat the models as the source of truth: a
field's type, regex constraint, length bound, or `Field(default=...)`
in code is what the loader will accept, regardless of what this doc
says. When in doubt, run `uv run python -c "import curator.models;
help(curator.models.WorkEntry)"` for the canonical contract.

## Section reference

The loader registers each file in `src/curator/loader.py:_SECTION_REGISTRY`
and routes it to the matching Pydantic model:

| File              | Model                  | Shape           |
|-------------------|------------------------|-----------------|
| `basics.yaml`     | `Basics`               | single object   |
| `work.yaml`       | `WorkEntry`            | list            |
| `skills.yaml`     | `SkillEntry`           | list            |
| `projects.yaml`   | `ProjectEntry`         | list            |
| `education.yaml`  | `EducationEntry`       | list            |
| `certificates.yaml`| `CertificateEntry`    | list            |
| `volunteer.yaml`  | `VolunteerEntry`       | list            |
| `publications.yaml`| `PublicationEntry`    | list            |
| `languages.yaml`  | `LanguageEntry`        | list            |
| `services.yaml`   | `ServiceEntry`         | list            |
| `interests.yaml`  | `InterestData`         | single object   |
| `cover-letter.yaml`| `CoverLetterCuration` | single object   |

### Minimal `basics.yaml`

```yaml
name: Your Name
label: "Senior Software Engineer"
email: you@example.com
phone: ""
location:
  city: "City"
  region: "ST"
url: https://example.com/yourhandle
profiles:
  - network: GitHub
    username: yourhandle
    url: https://github.com/yourhandle
summary: >-
  One- to three-sentence professional summary describing role,
  experience level, and signature areas of strength.
```

### Minimal `work.yaml`

```yaml
- id: example-co-senior-engineer
  name: Example Co.
  position: Senior Software Engineer
  start_date: "2023-01"
  end_date: present
  url: ""
  highlights:
    - id: example-co-launch
      content: >-
        Led the launch of feature X, owning design through rollout for
        N customers. Reduced p99 latency by Y% through architectural
        change Z.
    - id: example-co-mentor
      content: >-
        Mentored Q new engineers, established the team's RFC review
        process, and reduced onboarding time from W weeks to V weeks.
```

`id` fields must be unique within their list and stable across runs;
the AI references them by ID when producing curation output.

### Minimal `skills.yaml`

```yaml
- id: cloud-aws
  name: AWS
  level: Expert
  keywords:
    - EC2
    - S3
    - IAM
    - VPC
- id: languages
  name: Programming Languages
  level: Expert
  keywords:
    - Python
    - Go
    - TypeScript
```

The AI selects which `keywords` to surface and in what order on the
resume; `keywords` it does not select are not rendered, but every
`SkillEntry` (group header) is preserved.

### Minimal `projects.yaml`

```yaml
- id: example-project
  name: Example Project
  description: One-sentence description of the project's purpose.
  url: https://github.com/yourhandle/example
  weight: 10  # lower = more important; ties broken by file order
  highlights:
    - id: example-project-perf
      content: >-
        Quantified outcome with a number, technology used, and a
        business or user impact statement.
```

`weight` is the AI's signal for project ranking. Smaller numbers rank
higher; entries without `weight` sort to the end. Two projects with the
same `weight` are ordered by file position.

### Minimal `education.yaml` and `certificates.yaml`

```yaml
# education.yaml
- id: example-school-bs-cs
  institution: Example University
  area: Computer Science
  study_type: Bachelor of Science
  start_date: "2018-09"
  end_date: "2022-05"
  url: https://example.edu

# certificates.yaml
- id: aws-saa
  name: AWS Certified Solutions Architect — Associate
  issuer: Amazon Web Services
  date: "2024-03"
  url: https://aws.amazon.com/certification/
  priority: 1
```

The renderer reads these in file order (or by `priority` ascending if
present). The AI does not rank or filter education and certificates.

### Other sections

`volunteer.yaml`, `publications.yaml`, `languages.yaml`, `services.yaml`,
and `interests.yaml` follow the same per-file YAML pattern. See their
Pydantic models in `src/curator/models.py` for fields and constraints.

### Optional `cover-letter.yaml`

Only consumed when running `curator static --cover-letter` (renders the
file verbatim) or when an `--cover-letter` API path needs a
candidate-authored override. See the `COVER_LETTER_*` constants in
`src/curator/rules.py` for the machine-enforced bands (word counts,
sign-off enum, forbidden words and phrases). The matching rules in the
system prompt mirror those constants in
`src/curator/prompt.py:_COVER_LETTER_PROMPT_BLOCK`.

## Verifying your portfolio loads

```bash
# Set the path to your portfolio (the directory containing data/)
export CURATOR_PORTFOLIO_PATH=/path/to/your-portfolio

# Dry-run (zero API cost) — exercises the loader, prompt builder, and
# renderer without contacting the Anthropic API.
uv run curator static --no-pdf
```

If the loader rejects a file you'll get a `PortfolioValidationError`
with the offending section and field name. The Pydantic error chain
contains the exact constraint that failed (length, type, regex). Fix
the YAML and re-run.

## Notes on the legacy `professional-portfolio-source` reference

Earlier versions of this codebase assumed a sibling directory named
`professional-portfolio-source/` and the default `CURATOR_PORTFOLIO_PATH`
still points there. That sibling repo is the author's private portfolio
and not shipped publicly; you can leave the default in place if you
clone your own portfolio to that path, or override the env var to point
anywhere.
