// Cover letter template, best-practices compliant.
// Renders /data/basics.yaml for the letterhead and /data/cover_letter.yaml
// for the letter body. Typography is aligned with curated.typ so both PDFs
// feel like part of the same application packet.
//
// Usage: typst compile --root <profile-dir> cover_letter.typ <output.pdf>

#let basics = yaml("/data/basics.yaml")
#let letter = yaml("/data/cover_letter.yaml")

// ---------------------------------------------------------------------------
// Page setup
// ---------------------------------------------------------------------------

#set page(paper: "us-letter", margin: 0.75in)
// hyphenate: false  Typst's auto-hyphenation wraps line-break hyphens in
// /ActualText <FEFF00AD>, so clipboard copy emits U+00AD (SOFT HYPHEN).
// Web-form fonts that lack a U+00AD glyph render those as .notdef boxes.
// Keeping it off means the rendered text, accessibility tags, and clipboard
// text all match. Keep aligned with curated.typ. Guarded by
// TestTemplateTypography (unit) and the integration positive control.
#set text(font: ("Inter", "Ubuntu Sans", "DejaVu Sans"), size: 11pt,
          hyphenate: false, lang: "en")
#set par(justify: false, leading: 0.65em, first-line-indent: 0pt)

#show link: set text(fill: rgb("#003366"))

// ---------------------------------------------------------------------------
// Letterhead: name and contact line, matches resume header typography.
// ---------------------------------------------------------------------------

#let profiles = basics.at("profiles", default: ())
#let github = profiles.filter(p => lower(p.at("network", default: "")) == "github")
#let has_url = basics.at("url", default: "") != ""

#[
  #set par(spacing: 3pt)

  #text(size: 18pt, weight: "bold")[#basics.name]

  #v(6pt)

  #text(size: 10pt)[
    // Country-only location (state/region intentionally omitted to match
    // the resume header; the role is fully remote, country still matters
    // for work authorization but state does not).
    #if basics.at("email", default: "") != "" [#link("mailto:" + basics.email)[#basics.email]]
    #if basics.at("phone", default: "") != "" [ #sym.dot.c #basics.phone]
    #if basics.at("location", default: none) != none [
      #if basics.location.at("country_code", default: "") != "" [ #sym.dot.c #basics.location.country_code]
    ]
    #if has_url [ #sym.dot.c #link(basics.url)[#basics.url.replace("https://www.", "").replace("https://", "")]]
    #if github.len() > 0 [ #sym.dot.c #link(github.at(0).url)[#github.at(0).url.replace("https://", "")]]
  ]
]

#v(2pt)
#line(length: 100%, stroke: 0.5pt)
#v(14pt)

// ---------------------------------------------------------------------------
// Date line (rendered verbatim from the YAML for reproducibility).
// ---------------------------------------------------------------------------

#letter.at("rendered_date", default: "")
#v(14pt)

// ---------------------------------------------------------------------------
// Salutation, body paragraphs, sign-off, and name. Wrapped in a scoped
// content block so the U+2011 substitution applies to the letter body
// only, not the letterhead.
//
// Why U+2011: Chrome/Acrobat insert U+00AD (SOFT HYPHEN) into the
// clipboard when copying across a hyphenated line break, rendering as
// a .notdef tofu box in fonts lacking a U+00AD glyph. Replacing ASCII
// hyphens with U+2011 (NON-BREAKING HYPHEN) prevents Typst from breaking
// at the hyphen, so the reader heuristic never fires. Pair with
// `hyphenate: false` above (different mechanism, same family).
//
// Why body-only: the letterhead URL, email, and phone (cover_letter.typ
// lines 47-53) contain hyphens that must paste as ASCII so destination
// applications resolve them as URLs, mailto: targets, and tel: numbers.
// Typst's `link()` target stays intact under a global show rule but the
// displayed text (what lands in the clipboard) would still be rewritten.
// The escape form \u{2011} is used over a literal U+2011 for greppability
// and to defend against editor smart-dash autocorrects.
// ---------------------------------------------------------------------------

#[
  #show "-": "\u{2011}"

  #letter.salutation
  #v(10pt)

  #par[#letter.opening]
  #v(8pt)

  #for paragraph in letter.body_paragraphs [
    #par[#paragraph]
    #v(8pt)
  ]

  #par[#letter.closing]
  #v(22pt)

  #letter.sign_off,
  #v(14pt)
  #basics.name
]
