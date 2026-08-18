# 0028: Let an exporter report its own output path

- **Status:** Not started
- **Kind:** feature (cleanup)
- **Effort:** XS
- **Origin:** post-v0.2.0 code review (old spec 18, REQ-5)
- **Respects:** [ADR-0008](../docs/adr/0008-buffered-exporter-and-encoder-protocol.md) (exporter/encoder split), [ADR-0012](../docs/adr/0012-trace-output-formats.md) (dual output)

## 1. Problem statement

Nothing an operator sees is wrong. This is a maintenance cost: `--format chrome+perfetto`
works only because `CombinedTraceExporter` reads a private attribute off each sub-exporter, and
two `# type: ignore` comments hold the type checker off while it does so. Any exporter that
stores its path under a different name (or stores no path, as a future stream or in-memory
exporter would) silently breaks the combined format, and the checkers that exist to catch
exactly that have been told not to look.

## 2. Solution

`--format chrome+perfetto` keeps behaving identically. What changes is that "where does this
exporter write?" becomes a question the `EventsExporter` interface answers, so a new exporter
either answers it or fails to compile, instead of failing at runtime in one caller.

## 3. User stories

1. As a maintainer adding an output format, I want the compiler to tell me an exporter must
   expose its output path, so that I do not discover the requirement from a `chrome+perfetto`
   run months later.
2. As a maintainer running `mypy` and `pyrefly`, I want no `# type: ignore` on the dual-output
   path, so that a genuine type error there is not hidden by a blanket suppression.
3. As an operator using `--format chrome+perfetto`, I want the two output files to land
   exactly where they do today, so that nothing about my workflow changes.
4. As a maintainer of `StdoutExporter`, I want a sensible answer for an exporter that writes
   to a stream and not a file, so that the interface does not force a fictional path.

## 4. Implementation decisions

Add an abstract read-only `output_path` property to `EventsExporter`. `BufferedTraceExporter`
implements it from the `output_path` it already takes in its constructor and stores as
`_output_path`, which covers `ChromeTraceExporter` and `PerfettoExporter` in one place.
`CombinedTraceExporter.chrome_path` and `perfetto_path` read the public property, and both
`# type: ignore[attr-defined, no-any-return]` comments go.

`CombinedTraceExporter` itself has two paths and no single one; it keeps `chrome_path` /
`perfetto_path` as its public surface and implements `output_path` by returning the chrome
path, the file the operator names on the command line, and the one
`derive_combined_paths` treats as the base.

`StdoutExporter` writes to a stream. Rather than invent a path for it, `output_path` is typed
`Path | None` on the ABC and `StdoutExporter` returns `None`; `JsonlExporter` returns its own
path, which is already `Path | None` today. `CombinedTraceExporter` only ever wraps the two
file exporters, so its call sites are unaffected by the optionality.

**Rejected:** a separate `OutputPathProvider` protocol that only some exporters implement. It
reproduces the current situation (the combined exporter would still need a cast or an
`isinstance` check), and one property on one ABC is smaller than a second type to explain.

**Rejected:** leaving the `# type: ignore` comments and adding a comment explaining them. The
suppression is not the problem; the missing contract is.

## 5. Seams and testing decisions

- **Seam:** `tests/exporters/test_combined_exporter.py`, at the exporter's public surface.
  Nothing higher can see this: the behaviour under test is a type contract, and a
  trace-processor assertion would only confirm the files still land where they always did.
- **New seam needed:** none.
- **What makes a good test here:** assert that every concrete `EventsExporter` subclass answers
  `output_path`, discovered by walking `EventsExporter.__subclasses__()` rather than listing
  the classes, since a hardcoded list is one more thing to forget when adding an exporter, which is
  the failure this spec exists to prevent. Plus a `chrome+perfetto` run asserting the two files
  exist at the derived paths, which is the behaviour that must not change.
- **Prior art:** `tests/exporters/test_combined_exporter.py` for the fan-out assertions;
  `derive_combined_paths`' own doctests for the path derivation.
- **Cases:**
  1. Every concrete exporter answers `output_path`, and the combined exporter's answers match
     `derive_combined_paths` for the base path given.
  2. Regression guard: a `--format chrome+perfetto` run writes the same two files, and
     `mypy` / `pyrefly` are clean with no suppression on the property.

## 6. Out of scope

- The JSONL/stdout layering work in [0029](0029-jsonl-and-stdout-duplicate-the-buffering.md).
  It touches the same two classes, but this change is independent and much smaller; doing it
  first shrinks that one.
- Exposing anything else about an exporter (flush threshold, buffered count, encoder identity).
  One property, driven by one existing caller.
- Making `EventEncoder` an ABC rather than a `Protocol`. Unrelated, and ADR-0008 chose the
  protocol deliberately.
