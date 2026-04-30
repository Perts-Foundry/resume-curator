# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it
responsibly by emailing contact@pertsfoundry.com.

Please do not open a public GitHub issue for security vulnerabilities.

## Scope

This policy covers the resume-curator source code in this repository.

## Response

We will acknowledge receipt within 5 business days and provide a timeline
for resolution. We follow a 90-day coordinated disclosure policy.

## Supported Versions

Only the `main` branch is actively maintained.

## Contributor Guidance

Do not commit any of the following to this repository:

- Real job descriptions copied from job postings
- Real target-company names tied to specific applications, dates, or
  profile paths
- Anthropic request IDs or other API account-correlated metadata
- Real curated profile output (resume YAML, cover letter YAML, PDF,
  `curation_log.json` audit log)
- Personal contact details (real name, email, phone, GitHub or
  LinkedIn handle, street or city) of any specific individual in
  worked-example headers or sign-offs. Use placeholders like
  `Your Name` / `you@example.com` / `City, ST` / `github.com/yourhandle`
- Verbatim cover-letter or resume prose tied to a specific real
  application. Paraphrase or omit; quoting an exact sentence reveals
  what was sent to a named employer

Use the synthetic golden datasets under `tests/eval/golden/*.yaml`
for any test fixtures that need to look like real JDs. Real
applications belong in the local-only `profiles/` directory, which
is gitignored. CI runs gitleaks and trufflehog on every PR; any
detected secret material blocks the merge.
