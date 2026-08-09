# NNNN — <imperative title: what will be true once this lands>

> Copy this file to `specs/NNNN-kebab-title.md` and delete every `>` guidance line as you
> fill it in. See [README §Conventions](README.md#conventions) for the rules these sections
> exist to enforce.

- **Status:** Not started | In progress | Blocked (`<on what>`) | **Pinned** (`<test that locks current behavior>`)
- **Kind:** bug — correctness | availability | safety | regression | reporting | cosmetic
- **Effort:** XS | S | M | L
- **Origin:** `<where this came from — a review, a CI failure, an issue, a session>`
- **Respects:** `<ADR links whose decisions this must not contradict, or "—">`

## 1. Problem

> What the **operator** experiences, in their words — the command they ran, the trace they
> opened, and what was wrong with it. Not the faulty expression; that is §2. One short
> paragraph.

## 2. Evidence

> Why we believe it. Anchor on **symbols** — `module.function`, a class, a constant — never
> on line numbers, which go stale within a release and turn a spec into an archaeology
> exercise.
>
> Quote code only where the defect **is** the code (a wrong operator, a missing branch, a
> `break` that should be a `continue`) and prose cannot be more precise. Trim to the
> decision-rich part and label it with the symbol it lives in. If the evidence is external —
> a Perfetto `.proto` field number, a CPython header, a trace-processor table — cite it with
> enough detail to re-check without the original session.

## 3. Scope

> Two lists, both explicit. **Affected:** which subcommands, which `--format` values, which
> platforms. **Not affected:** the near neighbours a reader would otherwise assume are broken
> — this is what stops the fix growing. Say why the suite didn't catch it, if it didn't; that
> gap is usually the most useful line in the spec.

## 4. Proposed change

> The fix, as numbered steps. State the shape of the change, not a patch. Where a choice was
> already settled, say which option and why the others lose; where it is open, say so and name
> what would settle it.

## 5. Seams and testing decisions

> Sketch the seam **before** the implementation starts, and prefer an existing one — the fewer
> distinct seams this codebase has, the better. Use the highest seam that can observe the
> defect: a trace-processor SQL assertion beats a wire-format byte assertion, which beats
> reaching into a private attribute. Do not put a new suite behind a marker unless it is slow
> or probabilistic; a deselected test catches nothing ([ADR-0014](../docs/adr/0014-perfetto-integration-test-strategy.md)).

- **Seam:** `<the one seam this is tested through, and why it is the highest available>`
- **New seam needed:** `<none | what, at what level, and why nothing existing reaches it>`
- **What makes a good test here:** `<external behavior only. For anything touching the
  Perfetto encoder, assert what the trace *means* — a round-trip through our own constant is
  equally happy with a right and a wrong field number.>`
- **Prior art:** `<the closest existing test to model this on>`
- **Cases:**
  1. `<the case that fails today>`
  2. `<the regression guard: what must stay byte-identical>`

## 6. Out of scope

> What a reader might reasonably expect to be included and is not, with the reason. Adjacent
> defects, the larger refactor this one gestures at, the feature someone will suggest.

## 7. Further notes

> Anything that does not fit above: history, a rejected approach and why, a dependency on
> another spec. Delete the section if empty.
