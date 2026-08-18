# NNNN: <imperative title: the capability this adds>

> Copy this file to `specs/NNNN-kebab-title.md` and delete every `>` guidance line as you
> fill it in. See [CONVENTIONS.md](CONVENTIONS.md) for the rules these sections exist to
> enforce.
>
> Use this template for enhancements, ergonomics and cleanups: anything where the change is
> *wanted* rather than *broken*. For a defect, use [TEMPLATE-bugfix.md](TEMPLATE-bugfix.md).

- **Status:** Not started | In progress | Blocked (`<on what>`)
- **Kind:** feature (enhancement | ergonomics | efficiency | cleanup)
- **Effort:** XS | S | M | L
- **Origin:** `<where this came from>`
- **Respects:** `<ADR links whose decisions this must not contradict, or "none">`

## 1. Problem statement

> The problem from the **operator's** perspective: what they cannot do today, or what costs
> them something. Not the implementation gap; that belongs in section 4.

## 2. Solution

> The solution from the operator's perspective: what changes for them. Still no
> implementation. A reader should be able to stop here and know what shipping this feels like.

## 3. User stories

> A numbered list, `As a <actor>, I want <capability>, so that <benefit>`. Be extensive:
> cover the neighbouring cases, the failure paths, and the operator who must *not* be
> disturbed by this change. The actors here are usually: an operator attaching to a production
> process, a developer profiling their own script under `gcmon run`, someone reading a trace
> in the Perfetto UI, a CI job, a gcmon maintainer adding an output format.

1. As a `<actor>`, I want `<capability>`, so that `<benefit>`.
2. …

## 4. Implementation decisions

> The decisions, not a patch: which modules gain or lose responsibility, what the interface
> between them becomes, what data shape is introduced, which existing abstraction absorbs the
> change. Anchor on **symbols**, never line numbers.
>
> Inline a snippet only where it encodes a decision more precisely than prose can: a type
> shape, a state machine, a signature. Trim to the decision-rich part; this is not a working
> demo.
>
> Where an alternative was rejected, say so and why: that is the part of a spec that stops the
> discussion happening twice. An alternative left open ("the implementer picks one") is a
> decision the spec failed to make; name what would settle it instead.

## 5. Seams and testing decisions

> Sketch the seam **before** the implementation starts, and prefer an existing one. Use the
> highest seam that can observe the new behavior; do not put a new suite behind a marker
> unless it is slow or probabilistic
> ([ADR-0014](../docs/adr/0014-perfetto-integration-test-strategy.md)).

- **Seam:** `<the one seam this is tested through, and why it is the highest available>`
- **New seam needed:** `<none | what, at what level, and why nothing existing reaches it>`
- **What makes a good test here:** `<external behavior only. For anything touching the
  Perfetto encoder, assert what the trace *means* through the trace processor, not that the
  bytes round-tripped.>`
- **Prior art:** `<the closest existing test to model this on>`
- **Cases:**
  1. `<the new capability, observed from outside>`
  2. `<the regression guard: what must stay byte-identical for everyone not using it>`

## 6. Out of scope

> The adjacent features this deliberately does not include, with reasons. Be generous here:
> for an enhancement this section is what keeps the change landable.

## 7. Further notes

> Open design questions to settle when this is picked up, dependencies on other specs,
> rejected alternatives that need more than a sentence. Delete the section if empty.
