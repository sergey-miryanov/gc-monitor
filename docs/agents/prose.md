# Prose conventions

Which file owns which kind of statement, and what to cut from the rest.
[`docs/adr/README.md`](../adr/README.md) is the authority on ADRs and
[`specs/CONVENTIONS.md`](../../specs/CONVENTIONS.md) on specs. This page covers
the CHANGELOG, docstrings, comments and the user-facing pages, plus the routing
that spans all of them.

Write the trimmed version first. A draft that carries its own justification gets
cut on review, and asking for more costs one line.

## Who owns what

| Statement | Home | Keep it out of |
|---|---|---|
| A change an operator can observe | `CHANGELOG.md` | any mechanism behind it |
| Internal work: refactors, performance, tests | the standing `### Internal` line | an entry of its own |
| A new user-facing documentation file | `### Documentation` | edits to a page that exists |
| Why the design has this shape | `docs/adr/` | user docs, docstrings, comments |
| Work specified but not built | `specs/` | ADRs |
| How to drive gcmon, how to read its output | `docs/*.md` | CPython internals |
| A CPython or OS internal the design rests on | `docs/internals/` | `docs/*.md` and the ADRs |
| What the code below cannot say itself | the docstring summary line | a body narrating the code |
| A constraint that must not regress | a test name and its assertion | a comment asserting it |
| A reading from one run: a rate, a byte count | nowhere | all of the above |

## The CHANGELOG

`.github/scripts/extract_changelog.py` lifts a version's whole `##` section into
the GitHub release notes verbatim, so every line reaches users and a new `###`
heading is safe.

- **`Features`, `Bugfixes` and `Breaking changes` describe what an operator
  sees.** A change that alters a printed number belongs there; the reason it
  changed does not.
- **Internal work gets one standing line** under `### Internal`, phrased at the
  level of "Stability, correctness and performance improvements". Later internal
  work joins that line.
- **`### Documentation` is for new user-facing pages.** Correcting an existing
  one is internal work and falls under the standing line.
- **Entries take the one-line shape of their neighbours.**

## Docstrings and comments

Keep the summary line. Every sentence after it has to say something neither the
code below nor another file already says: a rejected alternative, a measured
cost, an ordering constraint that is not visible locally.

A comment restating an invariant that a test enforces goes stale, so delete it
and let the failing test carry the rule. Where the reason is architectural, cite
`ADR-NNNN` from the docstring rather than restating the argument; the citation
survives the refactor that would have stranded the copy.

## User-facing pages

`docs/*.md` says what gcmon does and how to read its output. CPython internals
stay out, including anything that a future release could change under us. Links
run from an ADR to a page, never back: a page names no ADR and no spec.

## What lands nowhere

- Numbers measured on one machine: a collection rate, a bar's width, an error
  bound. They date the text to the machine that produced them.
- The journey. What was searched, what broke, what was tried. The decision is
  the part worth keeping, and the record already holds it.
- A clause after the claim opening with *so*, *since*, *which is what* or
  *rather than*.
- A literary phrase where a standard term exists. "Case and surrounding space
  are forgiven" is "case-insensitive, surrounding whitespace stripped".

## Mechanical

- No em dash. No section sign, which is hard to type on an ordinary keyboard.
- Wrap at 80: `python .github/scripts/wrap_markdown.py --width 80 <files>`.
- Use the vocabulary in `specs/CONVENTIONS.md` rule 4: record, event, iid, loss
  window, span. Do not coin a synonym for one of them.
- The repo mixes CRLF and LF. Edit in place; rewriting a whole file flips its
  endings and buries the real diff.
