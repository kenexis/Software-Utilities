---
name: kenexis-open-audit-reader
description: Read Kenexis Open-Audit (.aud) files without corrupting them. Preserves sentinel values ("null", "empty"), HTML entities, whitespace, and key insertion order. Use any time you need to extract audit protocol questions, findings, scores, recommendations, team rosters, sessions, evidence, drawings, or parking-lot items from a .aud file. Trigger phrases include "read .aud", "open audit file", "load Open-Audit", "extract from Open-Audit", "what's in this audit file", "list the audit questions", "show audit findings", or any task that requires working with the contents of a Kenexis Open-Audit document.
---

# Kenexis Open-Audit Reader

## Purpose

Open-Audit `.aud` files are JSON-encoded but full of fragile conventions: string-literal sentinels (`"null"`, `"empty"`), pre-encoded HTML entities (`P&amp;ID`), preserved trailing whitespace, string-encoded numbers on protocol fields, key-order significance, and orphan records left behind by GUI deletes. A naïve `json.load` works, but a naïve consumer of the loaded object will misinterpret the data and a naïve write-back will corrupt the file.

This skill reads a `.aud` file in a way that:

1. Preserves every byte verbatim for round-trip safety,
2. Exposes a clean, resolved view of the audit content (questions with their scoring criteria already linked, attendance with orphans flagged, etc.),
3. Provides predicates for the format's sentinels so consumers don't compare strings by hand.

Use this skill **before** any work that touches `.aud` content — protocol audits, report generation, data extraction, dashboard rendering, etc.

## When to use this skill

Trigger on any of:

- A user uploads or references a file ending in `.aud` and asks anything about it.
- A user mentions "Kenexis Open-Audit" or "Open-Audit file."
- A task requires reading audit protocol questions, scores, findings, recommendations, or team data from a Kenexis source.
- The HAZOP or SIS quality-audit skills need to load a protocol from disk.

Do **not** use this skill if the user wants to *modify* the file. For modifications, use the companion `kenexis-open-audit-writer` skill, which loads via this reader, applies edits, and writes back safely.

## File format — what you must know before reading

A `.aud` file is a single-line, compact JSON object. The full reverse-engineered specification lives in `open-audit-format-review.md`; the highlights:

- **Single version marker:** `Settings.Ds_Rev` (integer). Preserve as-is.
- **String sentinels are not their JSON counterparts.** The string `"null"` means "no value selected"; the string `"empty"` on an `ID` field means "placeholder/seed record." There are zero JSON `null` values in a typical file.
- **Numbers on protocol fields are stored as strings** (`Selected_Score: "10"`). Numbers in `Settings.Column_Widths.*` and `Settings.Ds_Rev` are real numbers. Do not normalize.
- **HTML entities are pre-encoded** in free-text fields. Read verbatim.
- **Trailing whitespace and tabs in string values** are intentional or tolerated; preserve.
- **Foreign keys are wrapped objects** in some places: `Evidence_IDs: [{"ID": "abc..."}]`, not `Evidence_IDs: ["abc..."]`. Handle both shapes when resolving.
- **Settings.Column_Visibility is GUI state, not a data filter.** Read every field on every record regardless of visibility flags.
- **Orphan records are common.** Many-to-many tables (notably `Team_Members_Sessions`) accumulate orphans because the desktop tool does not cascade-delete. The reader surfaces orphans for review without removing them.

## High-level workflow

1. **Confirm the input.** Locate the `.aud` file (uploaded path, workspace path, or user-supplied absolute path).
2. **Run the loader script** (`read_aud.py`, included with this skill) to parse the file into a structured `Audit` view.
3. **Use the `Audit` object** to answer the user's question. Common operations are listed in §Output schema below.
4. **Never mutate the loaded data.** If the user wants to change anything, hand off to the writer skill instead.

## Step 1 — Confirm the input

Determine the absolute path of the `.aud` file. If the user uploaded the file, it lives under the uploads directory; if they pointed to a workspace path, use that. If unclear, ask the user to confirm.

## Step 2 — Run the loader

Invoke the loader via Python in the bash sandbox. Example:

```bash
python3 /path/to/kenexis-open-audit-reader/read_aud.py "/path/to/study.aud" --summary
```

Common modes:

- `--summary` — prints a one-screen overview: study name, facility, counts of questions/team/sessions/findings, and a list of currently hidden GUI fields.
- `--questions` — emits every question as a JSON line with resolved scoring criterion, weighting factor, evidence text, and recommendations.
- `--findings` — emits all questions whose `Findings` field is non-empty.
- `--orphans` — lists attendance records and other links whose foreign keys point to deleted parents.
- `--raw` — dumps the parsed object as pretty JSON for debugging only. Do **not** save this output back as a `.aud` file.

For programmatic use inside a larger Python step, import the script:

```python
import sys
sys.path.insert(0, '/path/to/kenexis-open-audit-reader')
from read_aud import load, Audit, is_unset, is_null_sentinel, is_empty_id

audit = load("/path/to/study.aud")
for q in audit.questions():
    if q['findings'].strip():
        print(q['category'], '/', q['element'], '/', q['question_text'][:80], '->', q['findings'])
```

## Step 3 — Use the resolved data

The `Audit` object exposes the following methods. All return new lists; the underlying data is never mutated.

| Method | Returns |
|---|---|
| `audit.overview` | The Overview dict, fields verbatim. |
| `audit.team_members()` | List of team member records. |
| `audit.sessions()` | List of session records. |
| `audit.attendance(include_orphans=False)` | Resolved attendance: each record annotated with team-member name (if known) and session label (if known). Orphans are excluded by default. |
| `audit.orphans()` | Lists orphaned `Team_Members_Sessions` records (foreign keys pointing to deleted parents). |
| `audit.categories()` | Hierarchical view: each category contains its elements; each element contains its questions. |
| `audit.questions()` | **Flat, fully resolved** view of every question with category and element labels, scoring criterion description, weighting factor, evidence text(s), recommendation text(s), and clause references. Use this for most analyses. |
| `audit.scoring_criteria()` | The score rubric. |
| `audit.weighting_factors()` | The weight rubric. |
| `audit.assessor_recommendations()` | All recommendations with status and priority resolved. |
| `audit.parking_lot()`, `audit.drawings()`, `audit.evidences()`, `audit.revalidation_history()` | Pass-through accessors. |
| `audit.hidden_fields()` | Map of `(collection, field) → bool` for fields the GUI is currently hiding. Informational only — the reader has already loaded everything. |

A resolved question entry looks like:

```python
{
  'id': 'qvarnyo6a3gghtl0aivlkh',
  'category': 'Preliminary Engineering and Conceptual Design',
  'element': 'Hazard and Risk Analysis',
  'question_text': 'Has a Process Hazards Analysis been completed for equipment under control of SIS?',
  'assessor_notes': 'A HAZOP for the entire gas plant ...',
  'assessor_guidance': 'Verify program is in place ...',
  'scoring_criterion': {'description': 'Compliant', 'level': '10', 'notes': '...'},
  'selected_score': '10',
  'weighting_factor': None,        # string sentinel "null" → Python None at the resolved layer
  'selected_weighting_factor': '',
  'weighted_score': '',
  'evidence': ['Reference to PHA report ...', 'Interview notes from ...'],
  'recommendations': [],
  'reference_documents': [{'name': 'IEC 61511', 'clauses': ['8.2.1']}],
  'findings': '',
  'raw': { ... original question dict, unmodified ... },
}
```

The `raw` field is the unmodified record from the file; if you need to write back later, pass `raw` (or the whole `audit.data`) to the writer skill.

## Sentinel handling

The reader exposes three predicates so consumers don't have to memorize the conventions:

- `is_unset(value)` — `True` for `None`, `""`, `"null"`, or `"empty"`. Use this whenever you want "is this field meaningfully populated?"
- `is_null_sentinel(value)` — `True` only for the string `"null"`. Use this when distinguishing the "user explicitly cleared this" sentinel from "never set."
- `is_empty_id(value)` — `True` only for the string `"empty"`. Use this on `ID` fields to recognize seed/placeholder records.

In the resolved views (`audit.questions()`, `audit.attendance()`, etc.), the reader translates `"null"` → Python `None` and skips records whose `ID` is `"empty"`. The `raw` substructure on each item still holds the verbatim values for round-trip safety.

## Hidden fields

`Settings.Column_Visibility` controls what the desktop GUI displays. The reader **does not filter on this**; every field is loaded and exposed. If a downstream consumer (e.g., a report generator) wants to mirror the GUI's hide/show state, call `audit.hidden_fields()` and apply visibility logic at presentation time, not at read time.

## Common operations — examples

**Count completed vs. incomplete questions:**

```python
audit = load(path)
qs = audit.questions()
done = [q for q in qs if not is_unset(q['selected_score'])]
print(f"{len(done)}/{len(qs)} questions scored")
```

**List every question whose Findings field has content:**

```bash
python3 read_aud.py study.aud --findings
```

**Pull every recommendation with status and responsible party:**

```python
audit = load(path)
for r in audit.assessor_recommendations():
    print(r['priority'], '|', r['status'], '|', r['responsible_party'], '|', r['text'][:80])
```

**Map a question back to its scoring criterion description:**

The resolved view already includes `scoring_criterion`. If you only have a raw question, call `audit.resolve_scoring_criterion(question['Scoring_Criteria_ID'])`.

**Detect orphan attendance records:**

```bash
python3 read_aud.py study.aud --orphans
```

## What NOT to do

- **Never** call `json.dumps` on `audit.data` and write it back. Use the writer skill, which applies the correct serialization.
- **Never** convert `"null"` strings to Python `None` *in* `audit.data`. The resolved views already do this for you, but the raw data must stay verbatim for round-trip writing.
- **Never** strip whitespace from string values you read.
- **Never** decode HTML entities in field values (`P&amp;ID` stays as-is).
- **Never** drop sentinel records (`{"ID": "empty", ...}`) from collections.
- **Never** filter the data based on `Column_Visibility`. That field controls GUI display, not data presence.
- **Never** treat `Column_Widths` as a schema. Real fields exist in records that have no `Column_Widths` entry, and `Column_Widths` lists vestigial keys (`reference`, `Group_Description`) with no corresponding data.

## Quality bar

The reader is correct if, given any `.aud` file:

1. `load(path)` succeeds and returns a dict whose keys are in the same order as the file.
2. The serialization `json.dumps(data, separators=(',',':'), ensure_ascii=False)` produces byte-identical output to the file's content.
3. Every resolved view is consistent with the underlying data (every `q['scoring_criterion']` matches the criterion whose `ID` equals the question's `Scoring_Criteria_ID`).
4. No exception is raised on files that contain orphan attendance records or other dangling foreign keys.

The included `read_aud.py` enforces all four. If the user provides a file that fails check (2), surface the discrepancy — it indicates either a new datastore revision or a corrupted file.
