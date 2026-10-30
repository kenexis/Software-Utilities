---
name: kenexis-open-pha-writer
description: Create or modify a Kenexis Open-PHA (.opha) study file in code so it opens in Open-PHA (desktop or the KISS web application) and its reports run. Starts new studies from the application's own empty study for any Analysis_Mode / Lopa_Mode pair, at the current schema revision. Writes compact single-line JSON with byte-perfect round-trip fidelity and the correct sentinels. Provides helpers to build either worksheet tree (Nodes → Deviations → Causes → Consequences, or Nodes → Deviations → Consequences → Causes) and the flat safeguard and recommendation libraries, and refuses to save a file the application could not run. Use when the user wants to generate a new .opha, convert a PHA, HAZOP or LOPA from another tool or a document into Open-PHA, add or edit nodes/deviations/causes/consequences/safeguards/recommendations, or programmatically populate a PHA/LOPA study. Trigger phrases include "create an opha file", "build a PHA study in code", "convert this PHA to Open-PHA", "add safeguards/recommendations to this study", "generate an Open-PHA file", "write the .opha". For read-only inspection, use kenexis-open-pha-reader instead.
---

# Kenexis Open-PHA Writer

## Purpose

Open-PHA stores a study as a single-line JSON object in a `.opha` file. This skill writes those files correctly: compact JSON, preserved key order, the application's own record shapes, and byte-perfect round-trip fidelity.

A hand-built file is the main risk. Open-PHA and KISS import a file with almost no structural checks, so a file with gaps imports cleanly and fails later: reports return a server error and tabs throw in the browser. The application's updater does not repair a gap at or below the file's stated `Ds_Rev`. Always build through `new_pha()` and the `add_*` helpers.

The second risk is wrong results from a valid file. A clean `validate()` means the application can run the file. It does not mean the study is right, and it does not recalculate LOPA.

`open-pha-format-review.md` describes the format. Its note at the top lists what has changed since it was written.

## When to use this skill

- "Create / generate a new Open-PHA (.opha) file."
- "Convert this PHA / HAZOP / LOPA into Open-PHA."
- "Add these safeguards / recommendations / nodes / consequences to the study."
- "Edit the consequence text / risk ranking / safeguard list in this .opha."

For read-only inspection or summarizing, use `kenexis-open-pha-reader`.

## Two workflows

**1. Create a new `.opha`.** `new_pha()` clones the application's own empty study for the chosen modes and gives every record a fresh ID. Before building, decide:

- `analysis_mode`: `"CauseConsequence"` (the default, causes above consequences) or `"ConsequenceCause"` (consequences above causes). Choose it from how the source study is organized, and ask the user if that is unclear. It cannot be changed afterwards.
- `lopa_mode`: `"Explicit"` (the default, frequencies, PFDs and TMELs) or `"Implicit"` (likelihoods, credits and LOPA gaps). Write both styles' values when the source allows, so the study can switch.
- Risk criteria: the shipped study carries the application's default matrix. If the user has their own, pass `risk_criteria_from="their_study.opha"`, a study already saved by the current application with the same LOPA mode. Without it, map each source severity to the matrix row with the same TMEL, not the same number, and tell the user the study uses the default matrix.

**2. Edit an existing `.opha`.** `load()` a real file → mutate with the `add_*` helpers or by editing fields directly → `save()`. An unmodified `load → save` is **byte-identical**, and a single-field edit changes only that field's bytes. If `validate()` warns that the file is at an older revision, ask the user to open and save it in Open-PHA first.

## Converting another tool's data

"Verbatim" means keep a value's text once it is in the unit this format uses. It does not mean copy a source number unchanged. Convert first:

| Source value | Open-PHA field | Conversion |
|---|---|---|
| "1 in N years", or a Freq column holding N | `Frequency` (per year) | `1/N`, so Freq 10 is `1E-1` |
| RRF of an IPL or modifier | `Pfd`, `CM_Probability`, `EE_Probability` | `1/RRF` |
| The same RRF | `Ipl_Credit`, `CM_Credit`, `EE_Credit` | `log10(RRF)`, as a decimal string such as `1.48` |
| RRF required at a frequency of 1 per year | `Tmel` | `1/RRF` |
| A percentage modifier, such as 10% occupancy | `CM_Probability`, `EE_Probability` | `0.1` |
| The same percentage | `CM_Credit`, `EE_Credit` | `log10(1/fraction)`, so 10% is `1` |
| A frequency, when a `Likelihood_ID` is wanted | `Likelihood_ID` | The `Risk_Criteria.Likelihoods` row with that `Frequency`, or else the nearest row above it. Say which rows were moved up |

Other rules for a conversion:

- **Safeguard records are shared.** `Is_Ipl` and `Pfd` sit on the library record and apply to every scenario linked to it. If a safeguard is credited in one scenario and not in another, or with a different value, make two records. Merging on the name alone changes risk results.
- **Data with no field goes in a LOPA or PHA comment,** not nowhere. Tell the user what went there and what was dropped.
- **Never invent text.** If the source has no deviation names, say so. Make one deviation per source deviation, using the part of the reference that identifies it (`1.12` in `1.12.2.1.1`) as its label. With no reference either, use one blank deviation per node.
- **Numbering comes from row position.** Open-PHA numbers rows itself, so strip list numbers from imported text and leave numbering on. Gaps in the source's numbering are not preserved. If other documents cite the source's numbers, keep them in the text and turn off each such level with `data["Settings"]["Numbering"]["Nodes"] = False` (likewise `"Deviations"`, `"Causes"`, `"Consequences"`), keeping the other keys.
- **`Cause_Type` and `Safeguard_Type` are free text,** so keep the source's wording. These columns and `Ipl_Tag` start hidden, as does `Safeguard_Comments`, so call `show_columns(data, "Cause_Type", "Safeguard_Type", "Ipl_Tag", "Safeguard_Comments")` for those holding data.
- **`Is_Safeguard` and `Is_Ipl` choose the worksheet.** The PHA worksheet lists a linked safeguard if `Is_Safeguard` is true, and the LOPA worksheet lists it if `Is_Ipl` is true. An IPL that was not a HAZOP safeguard has `is_safeguard=False`. A source row with no credit has `is_ipl=False`, so it is not on the LOPA worksheet; if the note matters there, also put it in a LOPA comment.
- **A modifier with no effect (RRF 1 or 100%)** does not change the results. Write it only if the source names it.
- **A source severity number has no field.** Map it by TMEL and keep the number in a LOPA comment.
- **Severity comes with its category.** Pass `**severity_fields(data, severity_id)` to `add_consequence`. In a `CauseConsequence` study `Consequence_Type_ID` chooses the list of severities a row can hold, so without it the application blanks the severity and TMEL the first time the worksheet opens, and the user's next save makes that permanent. `validate()` refuses a mismatch.
- **The Before/After fields are optional.** Set `Consequence_Severity_ID` and `Likelihood_ID`. Use `_Before_Safeguards` and `_After_Recommendations` only if the source rates those stages separately.
- **Two `Likelihood_ID` fields mean different things.** On a cause it is the initiating likelihood for Implicit LOPA. On a consequence it is the PHA likelihood, which with the severity gives `Risk_Rank_ID`. If the source has no PHA likelihood, leave it blank and tell the user; `validate()` does not report this.
- **Set `Lopa_Required="true"` on every LOPA scenario.** The default is `"false"`, and such a row is left off the LOPA worksheet and LOPA reports.
- **Pass plain text.** The record helpers apply the application's encoding, so write `P < 5 & T > 9` as it reads. If you set a text field directly, use `encode_text()`.

## What the application calculates

These fields are inputs: `Frequency`, `Likelihood_ID`, severities, `Tmel`, `Pfd`, `Ipl_Credit`, modifier and enabling-event values, and `Is_Ipl`.

These are calculated, and the application overwrites them whenever a study opens or changes: `Mel`, `Lopa_Ratio`, `Rrf`, `Recommended_Sil`, `Lopa_Risk_Rank_ID` and its Before/After siblings, and `Lopa_Gap` in Implicit mode. Leave them blank. Do not work them out and do not report a value you worked out as Open-PHA's result. Checking your own arithmetic against the source does not show what the application will calculate.

**A LOPA factor counts only if its column is visible on the LOPA worksheet.** That covers conditional modifiers, enabling events, IPLs and recommendation credits. The application's empty study hides some of these columns, so `new_pha()` turns them on. `validate()` refuses a study holding a factor whose column is hidden. If the study's owner hid it on purpose, pass `allow_hidden_factors=True`. Otherwise call `show_lopa_columns(data)`. Other columns, such as `Cause_Type`, stay hidden by default and do not affect the results.

## Inputs

- For editing: a path to an existing `.opha` file.
- For creating: a study name, the two modes, optional Overview metadata (facility, project number, etc.), and optionally the user's risk criteria.
- The study content to write (safeguards, recommendations, worksheet rows), supplied by the user or an upstream skill.

## Outputs

- A `.opha` file on disk (compact, single line, UTF-8, no trailing newline).
- The in-memory study dict, for chaining further edits.
- The warnings `save()` returned, passed on to the user.

## Key API (`write_opha.py`)

- **IO:** `load(path)`, `save(data, path, validate_first=True, allow_hidden_factors=False)` (returns warnings), `dumps(data)`, `verify_round_trip(path, data=None)`, `make_id()`.
- **New file:** `new_pha(study_name="", template_path=None, analysis_mode=None, lopa_mode=None, risk_criteria_from=None, **overview)`. Overview kwargs are matched to Overview fields case-insensitively (`facility=…`, `project_number=…`).
- **Records:** `new_record(record_type, analysis_mode=..., **fields)` builds any record type in canonical field order; an unknown field is an error. `ref_list(ids)` / `empty_ref()` build reference lists.
- **Libraries:** `add_safeguard`, `add_pha_recommendation`, `add_lopa_recommendation`, `add_pha_comment`, `add_lopa_comment`, `add_drawing`, `add_team_member`, `add_session`.
- **Worksheet tree:** `add_node(data, …)` → `add_deviation(node, …)`, then by mode:
  - `CauseConsequence`: `add_cause(deviation, …)` → `add_consequence(cause, safeguard_ids=[…], …)`
  - `ConsequenceCause`: `add_consequence(deviation, …)` → `add_cause(consequence, safeguard_ids=[…], …)`
  - Plus `add_enabling_event(cause, …)` and `add_conditional_modifier(consequence, …)`, which needs `category="Safety"` (or another of the five) in `ConsequenceCause`.
- **Ratings:** pass any record field as a keyword: `add_cause(dev, cause="…", frequency="1E-1", Likelihood_ID=lik_id)`; `add_consequence(cause, consequence="…", Lopa_Required="true", **severity_fields(data, sev_id))`, which works in both trees. Pass `Tmel=` after it only if the source's TMEL differs from the matrix row's.
- **Columns:** `show_columns(data, "Cause_Type", …)` turns on any column. `show_lopa_columns(data)` turns on every column the calculation depends on.
- **Text:** `encode_text(text)`, for setting a text field directly.
- **Validation:** `validate(data, allow_hidden_factors=False)` returns issue dicts (`severity` = `error` blocks save, `warning` advisory).
- **Constants:** `CAUSE_CONSEQUENCE`, `CONSEQUENCE_CAUSE`, `NULL_SENTINEL`, `EMPTY_SENTINEL`, and `current_ds_rev()`.

## Procedure

1. **Decide the workflow and, for a new study, both modes and the risk criteria.** Look at how the source is organized before starting; a wrong mode means starting again.
2. **Add safeguards and recommendations first** (they live in flat libraries), capturing the returned IDs.
3. **Build the worksheet tree top-down** with the helpers for that mode, passing the captured IDs. Look up `Likelihood_ID` and severity IDs from `data["Risk_Criteria"]`.
4. **Carry the ratings over.** If the source has frequencies, severities or risk ranks, write them. Do not leave them for later: the study opens and reports without them, and the gap is easy to miss.
5. **Let the helpers own encoding.** Do not hand-write sentinels or copy a record from another file's shape.
6. **Validate and save:** `warnings = save(data, path)` refuses on errors. Then call `verify_round_trip(path, data)` and confirm `ok` is `True`.
7. **Present** the file and every warning to the user, especially rows with no rating. Say which results you could not check. Ask them to open it in Open-PHA, compare MEL and RRF with the source, and run a report before relying on it.

If `save()` refuses, fix the cause. It refuses what the application cannot run or would calculate wrongly. Never pass `validate_first=False` to get past an error.

## Programmatic use

```python
import sys
sys.path.insert(0, "/path/to/kenexis-open-pha-writer")
import write_opha as w

d = w.new_pha(study_name="Unit 400 HAZOP", facility="Refinery North",
              analysis_mode="CauseConsequence", lopa_mode="Explicit")

sg  = w.add_safeguard(d, "PSV-401 relieves to flare", safeguard_type="PSV",
                      ipl_tag="PSV-401", is_ipl=True, pfd="0.01", Ipl_Credit="2")   # RRF 100
rec = w.add_pha_recommendation(d, "Perform LOPA on overpressure scenario",
                               priority="High", status="Under Review")

severity = next(s for s in d["Risk_Criteria"]["Severities"] if s["Severity_Type"] == "Safety")

node = w.add_node(d, "Reactor feed section")
dev  = w.add_deviation(node, deviation="High Pressure", guide_word="High",
                       parameter="Pressure")
cause = w.add_cause(dev, cause="Blocked outlet", frequency="1E-1")   # 1 in 10 years
con   = w.add_consequence(cause, consequence="Vessel overpressure / rupture",
                          safeguard_ids=[sg], pha_recommendation_ids=[rec], Lopa_Required="true",
                          **w.severity_fields(d, severity["ID"]))

warnings = w.save(d, "/path/to/study.opha")
print(w.verify_round_trip("/path/to/study.opha", d))   # -> ok: True
```

The same study in `ConsequenceCause` swaps the last two levels, and safeguards move to the cause:

```python
d = w.new_pha(study_name="Unit 400 HAZOP", analysis_mode="ConsequenceCause")
...
con   = w.add_consequence(dev, consequence="Vessel overpressure / rupture",
                          pha_recommendation_ids=[rec], Lopa_Required="true",
                          **w.severity_fields(d, severity["ID"]))
cause = w.add_cause(con, cause="Blocked outlet", frequency="1E-1", safeguard_ids=[sg])
```

## Files shipped with this skill

- `write_opha.py` — the writer module.
- `pha_templates.json` — the application's own empty study for each of the four mode pairs. Every record shape comes from these documents.

Both must sit in the same directory.

## Quality bar

1. `load(f)` → `save(copy)` with no edits produces a **byte-identical** file (`verify_round_trip` `ok: True`, `matches_data: True`).
2. A single-field edit changes only that field's bytes.
3. `new_pha()` output passes `validate()` with zero errors in all four mode pairs, and no two studies share an ID.
4. Built records have every field the application writes, in its order. Reference lists are `[{"ID":"empty"}]` when empty, and numbers are verbatim strings.
5. A parent with no real child keeps the application's blank `"empty"` row; the first real child replaces it.

## What NOT to do

- **Never** hand-build or stub `Settings` or `Risk_Criteria`, or leave any of their objects as `{}`. `Numbering`, `Grid_Settings`, `Tab_Visibility`, `Consequence_Category_Names`, `Column_Visibility` and `Column_Headers` are all load-bearing. Every report fails without `Numbering`.
- **Never** build worksheet records as plain dicts or copy them from a sample. A consequence without `Alarp_Analysis` and `Alarp_Required` breaks reports that include the ALARP section. Use the helpers, or `new_record()` if no helper fits.
- **Never** edit `Settings.Analysis_Mode` or `Settings.Lopa_Mode` on a study. The tree and the column settings follow the mode, so changing the value alone damages the file. Start again with `new_pha()`.
- **Never** hand-calculate `Mel`, `Lopa_Ratio`, `Rrf` or the other calculated fields, or hide a LOPA factor's column.
- **Never** set `Settings.Ds_Rev` by hand. `new_pha()` writes the current revision, and an edited file keeps its own. A file claiming a revision it does not fully meet is never repaired on open.
- **Never** pass `validate_first=False` to get a file past an error.
- **Never** leave out ratings the source has, or present a study with unrated rows without saying so.
- **Never** convert numbers to real numeric types. `Pfd`, `Frequency`, `Tmel`, `Rrf`, etc. are strings, sometimes scientific notation. Pass them through verbatim.
- **Never** write an empty reference array. "Nothing linked" is `[{"ID":"empty"}]`.
- **Never** regenerate, re-case, or normalize existing IDs when editing. Only `make_id()` new records.
- **Never** cascade-delete. Removing a safeguard/session/team-member should flag orphaned references for the user, not silently rewrite the tree.
- **Never** pretty-print or add a trailing newline on save; the on-disk form is compact and single-line.
