# 0031: Stop labelling the README's only trace example "Chrome Trace Output"

- **Status:** Not started
- **Kind:** bug (cosmetic)
- **Effort:** XS
- **Origin:** the last outstanding item of old spec 19 (README update for v0.2.0)
- **Respects:** [ADR-0012](../docs/adr/0012-trace-output-formats.md) (`chrome+perfetto` is deliberately undocumented in the README)

## 1. Problem

Someone choosing between `--format chrome` and `--format perfetto` finds one screenshot in the
README, headed **`### Example: Chrome Trace Output`**, captioned *"GC monitoring data visualized
in Perfetto UI"*, and listing a `Processes` lifetime track underneath. Three different answers
to "what am I looking at". The Quick Start block a few lines above offers
`--format perfetto -o trace.pftrace --rss` with nothing showing what that buys over the default,
so the reader's actual question, which format should I pick, goes unanswered by the one part
of the README that could answer it visually.

## 2. Evidence

In `README.md`, the section heading is `### Example: Chrome Trace Output`; the image it embeds
is `docs/images/chrome-trace-example.png`; the caption directly below reads *"GC monitoring data
visualized in Perfetto UI:"*. Both statements can be true at once (the Perfetto UI opens Chrome
JSON traces), which is exactly why the heading needs to say which format produced the file.

The bullets under it list features that are not uniformly available across formats.
[docs/formats.md](../docs/formats.md) is the authority on which: it marks command lines
**Perfetto-only** and the `Start Process` marker **Perfetto-only**, and it qualifies what the
`Processes` track's spans mean per producer. The README repeats none of those qualifications.

## 3. Scope

**Affected:** `README.md`, the `### Example: Chrome Trace Output` section, and the images
under `docs/images/`.

**Not affected:** [docs/formats.md](../docs/formats.md), which already documents the per-format
differences correctly and is where the detail belongs. No code, no trace content.

**Why the suite didn't catch it:** prose. Nothing tests it, and nothing should.

## 4. Proposed change

1. Retitle the section to name the format that produced the file. Establish which that is by
   checking the screenshot against `docs/formats.md`'s per-format table: if it shows a
   Perfetto-only feature, the file was a `.pftrace` and the heading is wrong; if not, the
   heading is right and the caption needs the qualifier "(both formats open in the Perfetto
   UI)".
2. Trim the bullets to the features the pictured format actually produces, and link
   [docs/formats.md](../docs/formats.md) for the rest; the link is already there and just needs
   to carry the difference.
3. If a second screenshot is worth adding, add the *other* format's, named for it, so the reader
   can compare. One correct example beats two mislabelled ones; do not add a second image just
   to have a pair.

Explicitly **not** part of this: a `## Optional Dependencies` container heading, the other item
old spec 19 was still open for. The `## Installation` section now covers `[stats]` and
`[cmdline]` in prose, links `docs/statistics.md` and `docs/rss.md`, and states the
graceful-degradation property. The content arrived; the heading it was once planned under did
not, and does not need to.

## 5. Seams and testing decisions

- **Seam:** none. This is documentation prose, and the only check that matters is reading it
  next to `docs/formats.md`.
- **New seam needed:** none. Do not add a link checker or a doc test for this; the cost outlives
  the defect.
- **What makes a good test here:** n/a.
- **Prior art:** n/a.
- **Cases:** n/a.

## 6. Out of scope

- Documenting `chrome+perfetto` in the README. ADR-0012 deliberately leaves it out.
- Rewriting the Alternatives Comparison table or the Limitations section, both of which were
  revised well after old spec 19 was written.
- Moving format detail out of `docs/formats.md` and into the README. The split is right; the
  README should link, not duplicate.
