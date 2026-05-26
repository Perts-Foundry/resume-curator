// Curated resume template — best-practices compliant
// Renders AI-curated portfolio data. Selection and ordering handled upstream
// by Python — this template renders whatever data files it receives.
//
// Design: Sans-serif, left-aligned header, ALL CAPS headings, navy hyperlinks,
// grouped skills without proficiency levels, no GPA for experienced candidates,
// auto-hyphenation off (clipboard soft-hyphen guard).
// See docs/architecture.md Design Decisions Log for rationale.
//
// Usage: typst compile --root <profile-dir> templates/curated.typ <output.pdf>

#let basics = yaml("/data/basics.yaml")
#let layout = yaml("/layout.yaml")

// Section data loaders — each returns an empty array if the file is absent.
// Typst's yaml() errors on missing files, so the renderer must write empty
// lists for sections with no selections.
#let work = yaml("/data/work.yaml")
#let education = yaml("/data/education.yaml")
#let skills = yaml("/data/skills.yaml")
#let certificates = yaml("/data/certificates.yaml")
#let projects = yaml("/data/projects.yaml")
#let interests = yaml("/data/interests.yaml")

// ---------------------------------------------------------------------------
// Page setup — best practices §2.2, §2.3, §2.6
// ---------------------------------------------------------------------------

// Asymmetric margins: 0.3in top/sides, 0.15in bottom. The trim cascade
// converges as soon as content fits the page budget and cannot backfill,
// so the last page often ends with unfilled real estate. Tightening the
// bottom margin specifically absorbs ~1 line of visible dead space
// without affecting the top header/contact block layout. 0.15in is below
// standard resume practice (0.5-0.75in is typical) but the trade-off
// favors visual completeness over conventional spacing for a tool whose
// output is read on screen as often as printed.
#set page(
  paper: "us-letter",
  margin: (top: 0.3in, bottom: 0.15in, left: 0.3in, right: 0.3in),
)
// hyphenate: false  Typst's auto-hyphenation wraps line-break hyphens in
// /ActualText <FEFF00AD>, so clipboard copy emits U+00AD (SOFT HYPHEN).
// Web-form fonts that lack a U+00AD glyph render those as .notdef boxes.
// Keeping it off means the rendered text, accessibility tags, and clipboard
// text all match. Trade-off: with justify: true below, long unbreakable
// tokens may produce slightly larger inter-word gaps; do not "fix" by
// re-enabling hyphenation. Keep aligned with cover_letter.typ. Guarded by
// TestTemplateTypography (unit) and the integration positive control.
#set text(font: ("Inter", "Ubuntu Sans", "DejaVu Sans"), size: 10pt,
          hyphenate: false, lang: "en")
#set par(justify: true, leading: 0.5em)
#set list(marker: [•], indent: 0.3in, body-indent: 0.2em, spacing: 8pt)

// Navy blue hyperlinks — §2.6: "#003366 safest accent", WCAG AAA (13.4:1)
#show link: set text(fill: rgb("#003366"))

// ALL CAPS tracked section headings, no horizontal rule — §2.2: 14-16pt, §2.4, §2.7
#show heading.where(level: 2): it => {
  v(16pt)
  block(spacing: 0pt)[#text(size: 14pt, weight: "bold", tracking: 0.5pt)[#upper(it.body)]]
  v(8pt)
}

// ---------------------------------------------------------------------------
// Date formatting helper
// ---------------------------------------------------------------------------
// Converts ISO partial dates to human-readable:
//   "2026-02"    → "Feb 2026"
//   "2023-06-15" → "Jun 2023"
//   "2014"       → "2014"
//   ""           → ""

#let month-names = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

#let format-date(raw) = {
  if raw == none or raw == "" { return "" }
  // str() handles year-only dates that arrive as integers
  // (e.g., if YAML serialization emits 2014 without quotes)
  let s = str(raw)
  let parts = s.split("-")
  if parts.len() >= 2 {
    let month-idx = int(parts.at(1)) - 1
    if month-idx >= 0 and month-idx < 12 {
      month-names.at(month-idx) + " " + parts.at(0)
    } else {
      s
    }
  } else {
    s
  }
}

// Year-only extractor for the compact education line. Handles "2018",
// "2018-05", and "2018-05-31" — the renderer normalizes to ISO partial
// dates but the portfolio source is allowed to be year-only. Shares the
// none/empty/cast/split input-handling shape with `format-date` above;
// if a third date helper joins them, factor out a `parse-date-parts`
// helper. Returns "" for malformed input where the leading chunk is not
// a 4-digit numeric prefix; upstream Pydantic OptionalDate validation
// should already reject such inputs at portfolio load time, but
// defending in depth keeps a stray non-year value from bleeding into
// the rendered PDF.
#let year-of(raw) = {
  if raw == none or raw == "" { return "" }
  let head = str(raw).split("-").at(0)
  if head.len() == 4 and regex("^\d{4}$") in head { head } else { "" }
}

// Compact-form degree abbreviations for the single-line education block.
// Exact-match, case-sensitive lookup keyed on the canonical full study
// type ("Bachelor of Science"). Anything unmapped (lowercase variants,
// "BSc", non-US/non-English degree titles, mid-string variations like
// "Bachelor of Engineering") falls through to the verbatim study_type
// string — the safe default and a deliberate, documented contract:
// portfolio data is canonical and the abbreviation is a layout choice,
// not a data choice. Extend this table when a new portfolio entry
// surfaces an unmapped degree.
#let abbreviate-degree(study_type) = {
  if study_type == "Bachelor of Science" { "B.S." }
  else if study_type == "Master of Science" { "M.S." }
  else if study_type == "Bachelor of Arts" { "B.A." }
  else if study_type == "Master of Arts" { "M.A." }
  else if study_type == "Doctor of Philosophy" { "Ph.D." }
  else { study_type }
}

// ---------------------------------------------------------------------------
// Header — left-aligned split layout per §2.8, §1.4 F-pattern
// ---------------------------------------------------------------------------

// Name (20pt bold, left) with website/GitHub (right, navy blue)
#let profiles = basics.at("profiles", default: ())
#let github = profiles.filter(p => lower(p.at("network", default: "")) == "github")
#let has_url = basics.at("url", default: "") != ""

// Top-left header block: name/URL line, label, contact. Scoped `par`
// spacing tightens the gaps between these three rows without affecting
// the rest of the document.
//
// NOTE: basics.name and basics.label are inlined into the 20pt heading
// via Typst content-mode interpolation. Both fields must be plain text
// only — no Typst-active characters (`#`, `[`, `]`, `$`, `\`) — because
// they are not escaped. Upstream validation is enforced by the
// SelectableBasics / ResumeCuration Pydantic models (ID regex +
// max_length on label) and by the AI's suggested_label field which the
// renderer injects; portfolio basics.name is trusted local data.
#[
  #set par(spacing: 3pt)

  #text(size: 20pt, weight: "bold")[
    #basics.name#if basics.at("label", default: "") != "" [#text(size: 12pt, weight: "regular", style: "italic")[#h(0.5em)#sym.dot.c#h(0.5em)#basics.label]]
  ]
  #h(1fr)
  #text(size: 12pt, weight: "bold")[
    #if has_url [#link(basics.url)[#basics.url.replace("https://www.", "").replace("https://", "")]]
    #if has_url and github.len() > 0 [#h(0.1em) | #h(0.1em)]
    #if github.len() > 0 [#link(github.at(0).url)[#github.at(0).url.replace("https://", "")]]
  ]

  // Contact line: country-only location (state/region intentionally omitted
  // since the role is fully remote — the country still matters for work
  // authorization but the state does not). Only emit a leading bullet
  // separator on "Fully Remote" when at least one preceding contact field
  // is present, so a sparse-basics fixture doesn't render a stray
  // " · Fully Remote" line.
  #let _has_email = basics.at("email", default: "") != ""
  #let _has_phone = basics.at("phone", default: "") != ""
  #let _has_country = (basics.at("location", default: none) != none
    and basics.location.at("country_code", default: "") != "")
  #let _has_contact_prefix = _has_email or _has_phone or _has_country

  #v(5pt)
  #text(size: 12pt, weight: "bold")[
    #if _has_email [#link("mailto:" + basics.email)[#basics.email]]
    #if _has_phone [ #sym.dot.c #basics.phone]
    #if _has_country [ #sym.dot.c #basics.location.country_code]
    #if _has_contact_prefix [ #sym.dot.c Fully Remote] else [Fully Remote]
  ]
]

#v(-4pt)
#line(length: 100%, stroke: 0.5pt)
#v(-2pt)

// Summary — §4.2: "340% more callbacks", "highest ATS weight"
#if basics.at("summary", default: "") != "" [
  #basics.summary
  #v(2pt)
]

// ---------------------------------------------------------------------------
// Section renderers
// ---------------------------------------------------------------------------

#let render-skills() = {
  if skills.len() > 0 [
    == Skills
    // Compact grid format per §5.2 DevOps example — no proficiency levels (§4.7)
    #grid(
      columns: (auto, 1fr),
      column-gutter: 0.6em,
      row-gutter: 5pt,
      ..skills.map(s => (
        [*#s.name:*],
        [#s.at("keywords", default: ()).join(", ")]
      )).flatten()
    )
  ]
}

// Number of leading highlights glued to the role header so it never
// stands alone at a page break. ATS resume parsers (Workday in
// particular) drop an entry when the header lands at the bottom of a
// page with all body content overflowing to the next. Keeping the
// header plus the first two bullets in an unbreakable block guarantees
// the parser sees enough of the entry on whichever page the header
// lands on. 2 is the textbook widow/orphan threshold; lowering it
// re-opens the parser-drop case, raising it makes tall entries skip
// pages and waste real estate.
#let WORK_HEADER_GLUE_BULLETS = 2

#let render-work() = {
  if work.len() > 0 [
    == Experience
    #for (i, job) in work.enumerate() [
      #if i > 0 [#v(4pt)]
      // 2-column grid keeps dates from wrapping mid-range when long
      // company+location overflows the left column.
      #let end_raw = job.at("end_date", default: "")
      #let end_display = if end_raw == "" or end_raw == none { [Present] } else { format-date(end_raw) }
      #let highlights = job.at("highlights", default: ())
      #let glue_count = calc.min(WORK_HEADER_GLUE_BULLETS, highlights.len())
      #let glued = highlights.slice(0, glue_count)
      #let rest = highlights.slice(glue_count)
      #block(breakable: false)[
        #grid(
          columns: (1fr, auto),
          align: (left + top, right + top),
          column-gutter: 0.5em,
          [*#job.position* #h(0.5em) | #h(0.5em) #job.name #if job.at("location", default: "") != "" [#text(size: 8.5pt)[ · #job.location]]],
          [#format-date(job.at("start_date", default: "")) -- #end_display],
        )
        #if glued.len() > 0 [
          #v(1pt)
          #for h in glued [
            - #h.text
          ]
        ]
      ]
      #for h in rest [
        - #h.text
      ]
    ]
  ]
}

// Each project renders as at most 3 lines:
//   Line 1: project name and (optional) link
//   Lines 2-3: up to 2 content bullets (description first when present,
//              then highlights filling any remaining slot)
// The 3-line cap keeps the Projects section compact; trim tier 2 can
// further drain bullets bottom-up when the page budget is tight.
#let render-projects() = {
  if projects.len() > 0 [
    == Projects
    #for (i, project) in projects.enumerate() [
      #if i > 0 [#v(3pt)]
      *#project.name* #if project.at("url", default: "") != "" [-- #text(size: 9pt)[#link(project.url)[#project.url]]]
      #let bullets = ()
      #let desc = project.at("description", default: "")
      #if desc != "" { bullets.push(desc) }
      #for h in project.at("highlights", default: ()) {
        if bullets.len() < 2 { bullets.push(h.text) }
      }
      #if bullets.len() > 0 [
        #for b in bullets [
          - #b
        ]
      ]
    ]
  ]
}

// Single-line education block:
//   <Degree> <Area>, minor in <Minor> · <Institution> · <Year> · <Honors>
// Year prefers end_date (graduation year) and falls back to start_date.
// GPA (`score`) is intentionally omitted for experienced candidates §4.8.
//
// NOTE: Like the header block at lines 107-114, the education fields
// (`area`, `minor`, `institution`, `honors`) are inlined into content
// mode without escaping. Same trust model: portfolio YAML is local
// and trusted, and EducationEntry validates id format upstream. Plain
// text only; do not introduce data with Typst-active characters
// (`#`, `[`, `]`, `$`, `\`) into education entries.
#let render-education() = {
  if education.len() > 0 [
    == Education
    #for (i, edu) in education.enumerate() [
      #if i > 0 [#v(3pt)]
      #{
        let degree = abbreviate-degree(edu.at("study_type", default: ""))
        let area = edu.at("area", default: "")
        let minor = edu.at("minor", default: "")
        let yr = if edu.at("end_date", default: "") != "" {
          year-of(edu.end_date)
        } else if edu.at("start_date", default: "") != "" {
          year-of(edu.start_date)
        } else { "" }
        let honors = edu.at("honors", default: "")

        if degree != "" [#degree]
        if degree != "" and area != "" [ ]
        if area != "" [#area]
        if minor != "" [, minor in #minor]
        // institution is schema-required, but guard defensively so a
        // future relaxation of the data contract doesn't render a
        // bare " · " separator.
        if edu.at("institution", default: "") != "" [ #sym.dot.c #edu.institution]
        if honors != "" [ #sym.dot.c #honors]
        // Year is right-aligned via #h(1fr), mirroring the
        // certificates layout (line 302) so degree dates and credential
        // dates anchor to the same visual column.
        if yr != "" [#h(1fr)#yr]
      }
    ]
  ]
}

#let render-certificates() = {
  if certificates.len() > 0 [
    == Certifications
    #for (i, cert) in certificates.enumerate() [
      #if i > 0 [#v(-2pt)]
      *#cert.name* | #cert.at("issuer", default: "") #h(1fr) #format-date(cert.at("date", default: ""))
    ]
  ]
}

// Renders hobby names only (description/keywords omitted for space).
// This section is renderer-managed and trimmed first when page overflows.
#let render-interests() = {
  let hobbies = interests.at("hobbies", default: ())
  let facts = interests.at("fun_facts", default: ())
  if hobbies.len() > 0 or facts.len() > 0 [
    == Interests
    #if hobbies.len() > 0 [
      #hobbies.map(h => h.name).join(", ")
    ]
    #if facts.len() > 0 [
      #if hobbies.len() > 0 [\ ]
      #facts.join([ #sym.dot.c ])
    ]
  ]
}

// ---------------------------------------------------------------------------
// Section dispatch — render in the order specified by layout.yaml
// ---------------------------------------------------------------------------

#let section-renderers = (
  "skills": render-skills,
  "work": render-work,
  "projects": render-projects,
  "education": render-education,
  "certificates": render-certificates,
  "interests": render-interests,
)

#for section-name in layout.section_order {
  let renderer = section-renderers.at(section-name, default: none)
  if renderer != none {
    renderer()
  }
}
