# Issue tracker: local markdown

Issues for this repo live as markdown files under `.scratch/`. That directory is
**gitignored**, so issues are local to one working copy: nothing publishes them,
and a fresh clone starts empty. Specs are the exception, and they are tracked.

## Specs live in `specs/`, not `.scratch/`

This repo had a spec convention before this file existed, and that convention
stays authoritative:

- One file per unit of forward-looking work at `specs/NNNN-kebab-title.md`.
  Numbers are assigned in order and never reused or renumbered; a gap in the
  folder means a spec retired.
- `specs/README.md` holds the index table (spec, kind, effort, summary) and the
  suggested order. A new spec adds a row there.
- `specs/RETIRED.md` holds the numbers that no longer have a file. A retired
  spec, landed or declined or superseded, **keeps its row** and loses its file:
  the row moves to that table, records the outcome in the Kind column and drops
  the link, so the number still resolves.
- Start from `specs/TEMPLATE-bugfix.md` or `specs/TEMPLATE-feature.md`; pick by
  whether the change fixes something or adds something, not by size.
- A spec states the problem, the evidence for it, the proposed change, and the
  seam it will be tested through. It does not record a decision.
- When the work lands, delete the spec file, cut its row from `README.md` and
  paste it into `RETIRED.md` with the outcome. If it settled something durable,
  write an ADR under `docs/adr/` first. See `docs/adr/README.md`.

`specs/CONVENTIONS.md` carries the rules a spec is held to: anchor on symbols
rather than line numbers, sketch the seam before the solution, state the problem
from the operator's perspective. Read it before writing one.

"Write a spec" therefore means `specs/NNNN-kebab-title.md`, tracked in git.
"Publish an issue" means a file under `.scratch/`, which is not.

## Issue conventions

- One feature per directory: `.scratch/<feature-slug>/`
- Implementation issues are one file per ticket at
  `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`. Never a
  single combined tickets file.
- Triage state is a `Status:` line near the top of each issue file. The role
  strings live in `triage-labels.md`.
- Comments and conversation history append to the bottom of the file under a `##
  Comments` heading.

## When a skill says "publish to the issue tracker"

Create a new file under `.scratch/<feature-slug>/`, creating the directory if
needed.

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path. The user normally passes the path or the
issue number directly.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a file with one **child** file per ticket.

- **Map**: `.scratch/<effort>/map.md`, holding the Notes / Decisions-so-far /
  Fog body.
- **Child ticket**: `.scratch/<effort>/issues/NN-<slug>.md`, numbered from `01`,
  with the question in the body. A `Type:` line records the ticket type
  (`research`/`prototype`/`grilling`/`task`); a `Status:` line records
  `claimed`/`resolved`.
- **Blocking**: a `Blocked by: NN, NN` line near the top. A ticket is unblocked
  when every file it lists is `resolved`.
- **Frontier**: scan `.scratch/<effort>/issues/` for files that are open,
  unblocked and unclaimed; first by number wins.
- **Claim**: set `Status: claimed` and save before any work.
- **Resolve**: append the answer under an `## Answer` heading, set `Status:
  resolved`, then append a context pointer (gist plus link) to the map's
  Decisions-so-far in `map.md`.
