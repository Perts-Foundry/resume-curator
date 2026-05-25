// Test fixture: a copy of src/curator/templates/cover_letter.typ with the
// body-scoped `#show "-": "\u{2011}"` rule REMOVED. Used as the positive
// control for `TestCoverLetterNonBreakingHyphens` to prove the negative
// tests aren't passing vacuously: a template without the show rule must
// emit ASCII hyphens in body text.
//
// Why a checked-in variant rather than a runtime `.replace()` patch of
// the packaged template: the show-rule line contains string quotes and
// a unicode escape that are easy to drift on across Typst versions or
// editor smart-quote autocorrects. A separate file is grep-stable and
// the drift-check in the test (`variant != packaged`) catches anyone who
// accidentally syncs them.
//
// DO NOT use this template in production. DO NOT add the show rule here
// to "fix" anything; this file's whole purpose is to lack it.

#let basics = yaml("/data/basics.yaml")
#let letter = yaml("/data/cover_letter.yaml")

// ---------------------------------------------------------------------------
// Page setup (identical to packaged cover_letter.typ).
// ---------------------------------------------------------------------------

#set page(paper: "us-letter", margin: 0.75in)
#set text(font: ("Inter", "Ubuntu Sans", "DejaVu Sans"), size: 11pt,
          hyphenate: false, lang: "en")
#set par(justify: false, leading: 0.65em, first-line-indent: 0pt)

#show link: set text(fill: rgb("#003366"))

// ---------------------------------------------------------------------------
// Letterhead (identical to packaged cover_letter.typ).
// ---------------------------------------------------------------------------

#let profiles = basics.at("profiles", default: ())
#let github = profiles.filter(p => lower(p.at("network", default: "")) == "github")
#let has_url = basics.at("url", default: "") != ""

#[
  #set par(spacing: 3pt)

  #text(size: 18pt, weight: "bold")[#basics.name]

  #v(6pt)

  #text(size: 10pt)[
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

#letter.at("rendered_date", default: "")
#v(14pt)

// ---------------------------------------------------------------------------
// Body section. NO `#show "-": "\u{2011}"` rule here. This is the whole
// point of the variant: ASCII hyphens must survive into the rendered PDF.
// ---------------------------------------------------------------------------

#[
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
