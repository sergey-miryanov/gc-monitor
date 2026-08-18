# Spec conventions

How to write a spec here, and how to retire one. The open set is in [README.md](README.md), the
numbers that no longer have a file in [RETIRED.md](RETIRED.md).

## Templates

- [TEMPLATE-bugfix.md](TEMPLATE-bugfix.md): something is broken.
- [TEMPLATE-feature.md](TEMPLATE-feature.md): enhancements, ergonomics, cleanups. Adds a
  user-perspective solution statement and user stories.

Pick by whether the change fixes something or adds something, not by size. Both carry the same
seams-and-testing section 5; until you have written it you cannot tell whether the work is
finishable. 0024 fits neither and says so in its header, since it produces an upstream issue
instead of a change to this repo. Treat it as the exception, not a third template.

## Conventions

**1. Anchor on symbols, never line numbers.** Cite `ControlServer._accept_loop`, not
`control_server.py:114`. Line numbers rot within one release: the spec set this folder replaced
cited `run_cmd.py:69`, `monitor_loop.py:46` and `jsonl_exporter.py:32-97`, and all three pointed
at something else before anyone came back to them. Quote code only where the defect or the
decision **is** the code, trimmed to the decision-rich part and labelled with its symbol.
External sources are the exception, a CPython line or a Perfetto field number; pin those to a tag
or a version.

**2. Sketch the seam before the solution.** Every spec says how you will test the change, and at
what level, before anyone starts. Prefer an existing seam, the highest one that can observe the
change, and keep their total low. The ladder, highest first: a trace-processor SQL assertion, a
wire-format byte assertion, an exporter-level unit test, a private attribute. Do not hide a new
suite behind a pytest marker unless it is slow or probabilistic, because a deselected test
catches nothing ([ADR-0014](../docs/adr/0014-perfetto-integration-test-strategy.md) records that
mistake and its reversal).

**3. State the problem from the operator's perspective.** Lead with what an operator sees, or
what someone opening the trace afterwards cannot tell; name the faulty expression after that.
Feature specs go further and carry user stories. A change with no operator-facing consequence is
a cleanup, not a bug, however wrong the code looks.

**4. Use the project's vocabulary, and respect the ADRs.** One entry read out of the target's
ring is a **record**; one thing written into a trace is an **event**. gcmon identifies an
interpreter by its **iid** and publishes that as a Perfetto `tid`. An interval whose records the
target overwrote before gcmon read them is a **loss window** or a **blind interval**, never
"missing data". A `Processes`-track slice is a **span**. Timestamps are nanoseconds inside gcmon,
and the encoder converts them
([ADR-0009](../docs/adr/0009-nanoseconds-canonical-time-unit.md)). Link the ADRs a spec must not
contradict in its header, and if implementing it overturns one, amend the ADR rather than the
code alone.

**5. Say what is out of scope**, with the reason for each; that is what keeps a spec landable. An
alternative left open ("the implementer picks one") is a decision you skipped. Make it, or name
the fact that would settle it.

**6. Assert what the trace means, not that it parsed.** gcmon's characteristic bug is a wrong
protobuf field number or a message nested in the wrong parent: the trace still parses, and it
renders wrong.
Three such bugs shipped, and each time a human found it by opening the file in the UI
([ADR-0001](../docs/adr/0001-hand-rolled-perfetto-protobuf-encoder.md),
[ADR-0014](../docs/adr/0014-perfetto-integration-test-strategy.md)). A round-trip test reads a
value back through the same constant it wrote with, so it passes on a wrong field number and a
right one alike.

## Lifecycle

**Delete the file when a spec retires; move its row to [RETIRED.md](RETIRED.md).** This folder is
the open set, not a history: the prose goes, git keeps it. The number outlives the file, because
commit messages, ADRs and other specs cite it, and the row answers "what was 0038?" in one line:
that it landed, when, and where the durable part went.

Name the outcome in the row's **Kind** column as **Landed**, **Declined** or **Superseded**, with
a date for the first two and the superseding spec for the third, and drop the link. Keep the
summary, in the past tense if it read as a complaint, and point at whatever survived: an ADR, the
spec that replaced it, or nothing.

Retiring one spec is therefore three edits: delete the file, cut its row from `README.md`
including any mention in the suggested order, and paste it into `RETIRED.md` with its outcome. If
the work settled something durable, write an ADR under [`docs/adr/`](../docs/adr/README.md)
first, so the row has somewhere to point.

Assign numbers in order and **never reuse or renumber one**, so a reference to spec 0026 keeps
meaning one thing. Take the next number from the highest in either table. A gap in the *folder*
means a spec retired.

Mark a spec **Pinned** in its status line when a characterization test locks the behaviour it
wants to change, and name that test: fixing the bug then means changing the test in the same
commit. [0025](0025-control-server-accept-loop-survives-transient-errors.md) is the current
example, where a test asserts the buggy behaviour because it covers the branch instead of stating
what should happen.
