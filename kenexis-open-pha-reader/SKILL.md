---
name: kenexis-open-pha-reader
description: Read and interpret a Kenexis Open-PHA (.opha) study file without mutating it. Parses the compact-JSON container preserving key order and every sentinel, then exposes typed views over the worksheet tree (Nodes → Deviations → Causes → Consequences), the flat safeguard and recommendation libraries, the team roster, and the risk matrix — with reference-ID lists resolved back to the records they point at. Use when the user wants to inspect, summarize, extract, audit, or report on an existing .opha file (a HAZOP, LOPA, or checklist study from Open-PHA). Trigger phrases include "read this opha file", "summarize the PHA study", "list the safeguards / recommendations in this study", "extract the worksheet", "what nodes are in this HAZOP", "load the .opha". For creating or modifying an .opha, use kenexis-open-pha-writer instead.
---

# Kenexis Open-PHA Reader

## Purpose

The Open-PHA desktop application stores a study as a single-line JSON object in a `.opha` file. That format is dense, deeply nested, and full of sentinel conventions that are easy to misread (string `"null"` vs. real JSON `null`, the `"empty"` reference sentinel, numbers stored as strings, tri-state string booleans). This skill loads an `.opha` file **once, faithfully, and read-only**, and hands back convenient accessors so downstream work (summaries, quality audits, reports, conversions) never has to touch the raw JSON or re-implement the sentinel rules.

The authoritative description of the format is `open-pha-format-review.md` in the project root. Read it if you need field-level detail; this skill implements its §13 read rules.

## When to use this skill

Trigger on any request to look at, but not change, an existing `.opha` file:

- "Summarize this PHA / HAZOP / LOPA study."
- "List the safeguards, recommendations, nodes, or team members."
- "Which consequences have no safeguards?" / "Which recommendations are open?"
- "Extract the worksheet to a table / report."
- Any first step of an audit or conversion that needs the study's contents.

If the user wants to **create** or **edit** a `.opha` file, use `kenexis-open-pha-writer`. Reading and writing are deliberately separate skills.

## Inputs

- Path to a `.opha` file.

## Outputs

- A `PhaStudy` object (from `read_opha.py`) wrapping the parsed document, offering:
  - `study.overview`, `study.settings`, `study.analysis_mode`, `study.lopa_mode`, `study.ds_rev`
  - collection views that exclude the `"empty"` seed placeholder: `team_members()`, `sessions()`, `drawings()`, `nodes()`, `safeguards()`, `pha_recommendations()`, `lopa_recommendations()`, `check_lists()`, `scais()`
  - `iter_consequences()` — flattens the Nodes → Deviations → Causes → Consequences tree, yielding a `ConsequenceContext(node, deviation, cause, consequence, …indices)`
  - reference resolvers: `resolve_safeguards(consequence)`, `resolve_pha_recommendations(consequence)`, `resolve_lopa_recommendations(consequence)`
  - risk-matrix lookups: `severity_index()`, `likelihood_index()`, `risk_rank_index()`, `risk_rank_of(consequence, stage)`
  - `summary()` — a headline dict (study name, mode, counts, round-trip status)
- Module-level sentinel predicates: `is_null_sentinel`, `is_empty_sentinel`, `is_unset`, `as_tristate`, `ids_in`.

## Procedure

1. Confirm the input path ends in `.opha` and exists.
2. Load with `read_opha.load(path)`.
3. **Verify fidelity first:** call `study.round_trip_ok()`. If it is not `True`, stop and report — the parse lost information and any downstream conclusion is untrustworthy. (It should always be `True` for a genuine Open-PHA file.)
4. Use the accessors to answer the user's question. Never index the raw dict for something an accessor already provides; the accessors already strip the `"empty"` seed rows and resolve references.
5. When interpreting a possibly-unset field, use the predicates rather than comparing to a literal: `is_unset(v)` covers `""`, `"null"`, JSON `null`, and `"empty"` together; `as_tristate(v)` maps `"true"/"false"/"null"` to `True/False/None`.
6. Present results in whatever form the user asked for (chat table, summary, or feed into a report/spreadsheet skill).

## Programmatic use

```python
import sys
sys.path.insert(0, "/path/to/kenexis-open-pha-reader")
from read_opha import load, is_unset

study = load("/path/to/study.opha")
assert study.round_trip_ok()

# Headline
print(study.summary())

# Every consequence with its safeguards
for ctx in study.iter_consequences():
    sgs = study.resolve_safeguards(ctx.consequence)
    print(ctx.node["Node_Description"], "|", ctx.consequence["Consequence"],
          "| safeguards:", [s["Safeguard"] for s in sgs])

# Consequences missing any safeguard
gaps = [ctx for ctx in study.iter_consequences()
        if not study.resolve_safeguards(ctx.consequence)]
```

## Quality bar

- `study.round_trip_ok()` is `True` for any real Open-PHA file — re-serializing the parse is byte-identical to the file on disk.
- The reader **never mutates** the document. It exposes interpretation through predicates, not by rewriting sentinels.
- Reference resolution matches the file: every non-`"empty"` ID in a `Safeguard_IDs` / `*_Recommendation_IDs` list resolves to a record in the corresponding flat library.

## What NOT to do

- **Never** convert `"null"` → JSON `null`, `"true"` → `True`, `"8.0E-3"` → a float, or collapse `[{"ID":"empty"}]` → `[]`. Reading is non-destructive; leave the data exactly as parsed.
- **Never** treat the `"empty"` seed record as real data. Use the collection accessors, which drop it.
- **Never** read a field through `Settings.Column_Visibility` — visibility is GUI state, not a data filter. Every record field holds data regardless of visibility.
- **Never** refuse based on hidden columns or unusual sentinels; that is expected format, not corruption.
- If `round_trip_ok()` is `False`, do **not** silently proceed — report that the file did not parse faithfully.
