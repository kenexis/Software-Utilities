---
name: kenexis-open-pha-writer
description: Create or modify a Kenexis Open-PHA (.opha) study file in code so it opens in the Open-PHA desktop application. Writes compact single-line JSON with byte-perfect round-trip fidelity, encoding every field with the correct sentinel — string "null" vs. real JSON null on the severity family, the "empty" reference sentinel, numbers as verbatim strings (incl. scientific notation), and tri-state string booleans. Provides helpers to build the worksheet tree (Nodes → Deviations → Causes → Consequences), the flat safeguard and recommendation libraries, and to start a blank study from an embedded template. Use when the user wants to generate a new .opha, add or edit nodes/deviations/causes/consequences/safeguards/recommendations, or programmatically populate a PHA/LOPA study. Trigger phrases include "create an opha file", "build a PHA study in code", "add safeguards/recommendations to this study", "generate an Open-PHA file", "write the .opha". For read-only inspection, use kenexis-open-pha-reader instead.
---

# Kenexis Open-PHA Writer

## Purpose

The Open-PHA desktop application persists a study as a single-line JSON object in a `.opha` file, with a set of sentinel and encoding conventions that must be reproduced exactly or the file will silently corrupt (or fail to load). This skill writes `.opha` files correctly: compact JSON, preserved key order, the right sentinel for every field, and byte-perfect round-trip fidelity. It supports two workflows — **editing an existing study** and **building a new study from a blank template** — and validates structure before saving.

The authoritative format description is `open-pha-format-review.md` in the project root; this skill implements its §13 write rules and §15 schema.

## When to use this skill

- "Create / generate a new Open-PHA (.opha) file."
- "Add these safeguards / recommendations / nodes / consequences to the study."
- "Edit the consequence text / risk ranking / safeguard list in this .opha."
- Any workflow that must produce a `.opha` a user can open in Open-PHA.

For read-only inspection or summarizing, use `kenexis-open-pha-reader`.

## Two workflows, two confidence levels

**1. Edit an existing `.opha` (fully reliable, proven).**
`load()` a real file → mutate with the `add_*` helpers or by editing fields directly → `save()`. An unmodified `load → save` is **byte-identical** to the original, and a single-field edit changes only that field's bytes. This is the safe, high-fidelity path and should be preferred whenever the user already has a study file.

**2. Create a new `.opha` from the template (provisional).**
`new_pha()` clones an embedded blank template (`pha_template.opha`) that carries a complete `Settings` scaffold and a valid `Risk_Criteria` matrix, with all data collections in seed state. This produces a structurally valid, round-trip-stable file. **Caveat:** the template was derived from a single *populated* sample, not from a fresh export of the desktop tool, so a from-scratch file is **not yet confirmed to open in Open-PHA**. Tell the user this, and ask them to open the result in the application to confirm before relying on it. See `open-pha-format-review.md` §12 and §14 — the single highest-value next input is a freshly-created empty study exported from the desktop tool, which would let us replace the template with a verified one.

## Inputs

- For editing: a path to an existing `.opha` file.
- For creating: an optional study name and Overview metadata (facility, project number, etc.).
- The study content to write (safeguards, recommendations, worksheet rows), supplied by the user or an upstream skill.

## Outputs

- A `.opha` file on disk (compact, single line, UTF-8, no trailing newline).
- The in-memory study dict, for chaining further edits.

## Key API (`write_opha.py`)

- **IO:** `load(path)`, `save(data, path, validate_first=True)`, `dumps(data)`, `verify_round_trip(path, data=None)`, `make_id()`.
- **New file:** `new_pha(study_name="", **overview)` — overview kwargs are matched to Overview fields case-insensitively (`facility=…`, `project_number=…`).
- **Records / skeletons:** `new_record(record_type, **fields)` builds any record type in canonical field order with correct unset markers; `ref_list(ids)` / `empty_ref()` build reference lists.
- **Libraries:** `add_safeguard`, `add_pha_recommendation`, `add_lopa_recommendation`, `add_pha_comment`, `add_lopa_comment`, `add_drawing`, `add_team_member`, `add_session`.
- **Worksheet tree:** `add_node(data, …)` → node; `add_deviation(node, …)` → deviation; `add_cause(deviation, …)` → cause; `add_consequence(cause, …, safeguard_ids=[…], pha_recommendation_ids=[…])` → consequence; plus `add_enabling_event`, `add_conditional_modifier`.
- **Validation:** `validate(data)` returns issue dicts (`severity` = `error` blocks save, `warning` advisory).
- **Constants:** `NULL_SENTINEL`, `EMPTY_SENTINEL`, `JSON_NULL_FIELDS`, `SEED_COLLECTIONS`.

## Procedure

1. **Decide the workflow.** If the user has an existing file → `load()` it. If starting fresh → `new_pha()`, and warn about the provisional caveat above.
2. **Add safeguards and recommendations first** (they live in flat libraries), capturing the returned IDs.
3. **Build the worksheet tree top-down:** node → deviation → cause → consequence, passing the captured safeguard/recommendation IDs into `add_consequence(..., safeguard_ids=[…])`.
4. **Let the helpers own encoding.** Do not hand-write sentinels. The skeletons already place real JSON `null` on the severity family, string `"null"` on FK/enum/bool fields, `[{"ID":"empty"}]` on empty reference lists, and `""` on blank text. Pass numbers as strings verbatim (e.g. `pfd="8.0E-3"`).
5. **Validate and save:** `save(data, path)` runs `validate()` first and refuses on errors. Then call `verify_round_trip(path, data)` and confirm `ok` is `True`.
6. **Present** the file to the user. For a from-scratch file, explicitly ask them to open it in Open-PHA to confirm it loads.

## Programmatic use

```python
import sys
sys.path.insert(0, "/path/to/kenexis-open-pha-writer")
import write_opha as w

d = w.new_pha(study_name="Unit 400 HAZOP", facility="Refinery North")

sg  = w.add_safeguard(d, "PSV-401 relieves to flare", safeguard_type="PSV",
                      ipl_tag="PSV-401", is_ipl=True, pfd="0.01")
rec = w.add_pha_recommendation(d, "Perform LOPA on overpressure scenario",
                               priority="High", status="Under Review")

node = w.add_node(d, "Reactor feed section")
dev  = w.add_deviation(node, deviation="High Pressure", guide_word="High",
                       parameter="Pressure")
cause = w.add_cause(dev, cause="Blocked outlet", frequency="1E-1")
con   = w.add_consequence(cause, consequence="Vessel overpressure / rupture",
                          safeguard_ids=[sg], pha_recommendation_ids=[rec])

w.save(d, "/path/to/study.opha")
print(w.verify_round_trip("/path/to/study.opha", d))   # -> ok: True
```

## Files shipped with this skill

- `write_opha.py` — the writer module.
- `pha_template.opha` — the blank-study template `new_pha()` clones (Settings + Risk_Criteria scaffold; data collections seeded).
- `pha_skeletons.json` — canonical field skeleton for every record type, with the correct unset marker per field. `new_record()` reads this.

All three must sit in the same directory.

## Quality bar

1. `load(f)` → `save(copy)` with no edits produces a **byte-identical** file (`verify_round_trip` `ok: True`, `matches_data: True`).
2. A single-field edit changes only that field's bytes.
3. `new_pha()` output passes `validate()` with zero errors and round-trips cleanly.
4. Built records carry the correct per-field encoding: severity family = real JSON `null`; likelihood/rank/type/bool FKs = string `"null"`; reference lists = `[{"ID":"empty"}]` when empty; numbers = verbatim strings.
5. Populating a collection drops its lone `"empty"` seed; genuinely empty seed collections (`Parking_Lot`, `Mocs`, `Previous_Incidents`, `Industry_Incidents`, `Revalidation_History`) keep it.

## What NOT to do

- **Never** write `"null"` where the severity family (`Consequence_Severity_ID` and its Before/After siblings, `Severity_ID`) needs real JSON `null`, or vice versa. Use the skeletons/constants — do not hand-encode.
- **Never** convert numbers to real numeric types. `Pfd`, `Frequency`, `Tmel`, `Rrf`, etc. are strings, sometimes scientific notation. Pass them through verbatim.
- **Never** write an empty reference array. "Nothing linked" is `[{"ID":"empty"}]`.
- **Never** regenerate, re-case, or normalize existing IDs when editing. Only `make_id()` new records.
- **Never** cascade-delete. Removing a safeguard/session/team-member should flag orphaned references for the user, not silently rewrite the tree.
- **Never** bump `Settings.Ds_Rev` unless explicitly directed and the change is understood — it is the format's only version marker.
- **Never** pretty-print or add a trailing newline on save; the on-disk form is compact and single-line.
- **Never** present a from-scratch file as verified. State that it needs confirmation in the Open-PHA desktop tool until a real empty-study sample validates the template.
