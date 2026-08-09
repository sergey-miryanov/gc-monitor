# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the
codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root, or
- **`CONTEXT-MAP.md`** at the repo root if it exists — it points at one `CONTEXT.md` per
  context. Read each one relevant to the topic.
- **`docs/adr/`** — read ADRs that touch the area you're about to work in. In multi-context
  repos, also check `src/<context>/docs/adr/` for context-scoped decisions.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest
creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and
`/improve-codebase-architecture`) creates them lazily when terms or decisions actually get
resolved.

`CONTEXT.md` does not exist in this repo yet. `docs/adr/` does, with an index and reading order
in `docs/adr/README.md` — start there rather than listing the directory.

## The forward-looking complement

`docs/adr/` records decisions already taken. `specs/` records work specified but not yet built,
one file per open item, deleted when it lands. Read `specs/README.md` when you need to know what
is already planned in the area you're about to work in — proposing something that is already
spec'd is the most common way to duplicate work here. Its `## Conventions` section also carries
this repo's vocabulary rules, which the next section depends on.

## File structure

Single-context repo (most repos), which is what this repo is:

```
/
├── CONTEXT.md                          ← not yet written
├── docs/adr/
│   ├── 0008-buffered-exporter-and-encoder-protocol.md
│   └── 0011-process-lifetime-and-ordering.md
├── specs/                              ← open work, not decisions
└── src/
```

Multi-context repo (presence of `CONTEXT-MAP.md` at the root):

```
/
├── CONTEXT-MAP.md
├── docs/adr/                          ← system-wide decisions
└── src/
    ├── ordering/
    │   ├── CONTEXT.md
    │   └── docs/adr/                  ← context-specific decisions
    └── billing/
        ├── CONTEXT.md
        └── docs/adr/
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a
test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary
explicitly avoids.

Until `CONTEXT.md` exists, `specs/README.md#conventions` holds the vocabulary rules: one entry
read out of the target's ring is a **record**, one thing written into a trace is an **event**, an
interpreter is identified by its **iid**, an interval whose records were overwritten unread is a
**loss window**, and a `Processes`-track slice is a **span**.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing
language the project doesn't use (reconsider) or there's a real gap (note it for
`/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently
overriding:

> _Contradicts ADR-0007 (shared trace converter pipeline) — but worth reopening because…_
