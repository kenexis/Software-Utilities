---
name: kenexis-open-audit-writer
description: Modify and write Kenexis Open-Audit (.aud) files without corrupting them. Preserves sentinel values ("null", "empty"), HTML entities, whitespace, key insertion order, and string-encoded numbers on protocol fields, and calculates scores and category totals the way the application does. Use when the user wants to create an audit, add, edit, or remove audit content (categories, questions, scores, findings, recommendations, evidence, team members, sessions) and save the result to a .aud file. Trigger phrases include "edit .aud", "modify Open-Audit", "add a question to the audit", "score this question", "record findings", "add team member", "save Open-Audit", or any task that mutates a Kenexis Open-Audit document.
---

# Kenexis Open-Audit Writer

## Purpose

Open-Audit `.aud` files have several format conventions that ordinary JSON tooling will silently destroy: string-literal sentinels (`"null"`, `"empty"`), pre-encoded HTML entities, intentional trailing whitespace, string-encoded numbers on protocol fields, key-order significance, and orphan records that must be left in place. This skill writes `.aud` files in a way that survives Open-Audit's loader, produces clean diffs in version control, and never silently mutates fields the user did not touch.

Scores are the main risk. Open-Audit calculates them in the browser, stores the results in the file, and recalculates a question only when its row is edited, not on open or save. Reports and dashboards read what is stored, so a score written by hand looks fine and is wrong. Always score with `score_question()`.

This skill is the **only** sanctioned way to save changes to a `.aud` file. Use the companion `kenexis-open-audit-reader` skill for read-only access.

## When to use this skill

Trigger on any of:

- A user wants to add, modify, or delete content in a `.aud` file (questions, scores, findings, recommendations, evidence, team members, sessions, drawings, parking-lot items, etc.).
- A workflow generates audit results that need to be persisted back into the source `.aud` file.
- A new `.aud` file needs to be created from a template.

If the user only wants to *view* `.aud` content, use the reader skill instead.

## Critical safety rules — read these before any edit

These rules exist because each one corresponds to a real corruption pattern observed in the format. Violating any of them silently breaks the file.

1. **The string `"null"` is not JSON `null`.** It is the format's "no value selected" sentinel. Preserve it as a string. Never write `null` (real JSON null) to a field that originally held `"null"`. Fields known to use this sentinel: `Weighting_Factor_ID`, `Selected_Score`, `Assessor_Recommendation_Priority`, `Assessor_Recommendation_Status`, `Team_Members_Sessions[*].Value`.
2. **The string `"empty"` is not absence.** On `ID` fields it marks a blank seed row. A collection holds one while it has no real rows, and the first real row takes its place, as when a user fills that row in. A link list holds `{"ID":"empty"}` only while nothing is linked. The helpers do this for you. Do not delete a seed row from an empty collection.
3. **Numbers on protocol fields are strings.** `Selected_Score: "10"`, `Scoring_Criteria_Level: "10"`, `Weighting_Factor_Score: "10"`. Never coerce to integers. The exception is `Settings.Ds_Rev` (real integer) and `Settings.Column_Widths.*` (real floats); preserve those as numbers.
4. **HTML entities are pre-encoded** in free-text fields (`P&amp;ID`, not `P&ID`). Pass raw or already-encoded text; the helpers encode `&`, `<` and `>` the way the application does, and text that is already encoded is left as it is.
5. **Whitespace in string values is preserved.** Trailing tabs, newlines, leading spaces — the desktop tool round-trips them and so must this skill. Never `.strip()` field content.
6. **Key insertion order is significant** for diff hygiene. Use the canonical key order defined in this skill (or the order from the reader output if loaded from disk).
7. **Compact serialization, no trailing newline.** The file is one line: `json.dumps(data, separators=(",",":"), ensure_ascii=False)`.
8. **No cascade-delete.** When the user removes a Team_Member or Session, attendance records that reference it become orphans. Leave them in place. Surface the orphans for the user to decide.
9. **Settings is GUI state, but it is required.** Round-trip `Column_Widths`, `Column_Visibility`, `Column_Headers`, and `Encrypt` untouched, and never stub them. `new_audit()` writes the current `Ds_Rev`; never set it by hand.
10. **Refuse to write a file with `Settings.Encrypt: true`** until the encryption scheme is documented.
11. **Never write a score field directly.** `Scoring_Criteria_ID`, `Weighting_Factor_ID`, `Selected_Score`, `Selected_Weighting_Factor`, `Weighted_Score` and the category totals move together. Use `score_question()`. `set_field` refuses them.

## High-level workflow

1. **Load the source file** via the reader skill (or accept an in-memory `audit.data`).
2. **Apply the user's changes** using the helpers in `write_aud.py` (`score_question`, `set_field`, `add_category`, `add_element`, `add_question`, `add_team_member`, `record_finding`, `add_recommendation`, etc.). Helpers enforce the safety rules above.
3. **Validate** the modified data with `validate(data)`. The validator returns a list of warnings/errors; fix anything fatal before saving.
4. **Save** with `save(data, output_path)`. The saver applies canonical compact JSON serialization. Tell the user about any warnings.
5. **Verify the round-trip** by re-loading the saved file and comparing semantic content against the in-memory state. The verifier confirms no fields drifted.
6. **Present the result file** to the user.

## Step 1 — Load the source

For an existing file:

```python
import sys
sys.path.insert(0, '/path/to/kenexis-open-audit-reader')
sys.path.insert(0, '/path/to/kenexis-open-audit-writer')
from read_aud import load
from write_aud import save, validate, add_question, score_question, set_field, record_finding

audit = load("/path/to/study.aud")
data = audit.data   # the verbatim parsed object — this is what you mutate
```

For a new file from a template:

```python
from write_aud import new_audit
data = new_audit(study_name="My Study", facility="Plant 1")
```

## Step 2 — Apply changes

The writer ships helper functions for every common edit. Use them rather than mutating `data` by hand — the helpers handle the sentinels, ID generation, and key ordering for you.

### Scoring a question

```python
from write_aud import score_question

score_question(data, "qvarnyo6a3gghtl0aivlkh",
               scoring_criterion="Compliant",     # a label from Scoring_Criterias, or its ID
               weighting_factor="Critical")       # a label from Weighting_Factors, or its ID
```

This sets the IDs, the selected score, the weighted score and the category's totals together. Leave an argument out to keep its value, or pass `""` to clear it. If a scoring level or weighting score itself changes, run `recalculate(data)`.

### Setting a field on an existing record

```python
from write_aud import set_field, find_question

q = find_question(data, question_id="qvarnyo6a3gghtl0aivlkh")
set_field(q, "Findings", "Verified during interview on 2026-05-01.")
```

`set_field` enforces:
- No score fields (use `score_question`).
- Numbers on protocol fields stored as strings (it will refuse `10` and ask for `"10"`).
- HTML escaping applied to free-text fields.
- Whitespace passed through verbatim.

### Recording a finding

`record_finding` is a higher-level helper that sets the Findings text and optionally creates an associated Assessor_Recommendation, linking the recommendation back to the question.

```python
from write_aud import record_finding

record_finding(
    data,
    question_id="qvarnyo6a3gghtl0aivlkh",
    findings="Some potential SIF on the P&ID were not discussed in the PHA.",
    recommendation_text="Review the P&IDs to develop a complete list of SIFs.",
    recommendation_priority="High",          # writer encodes per the priority enum
    responsible_party="Process Safety Lead",
)
```

### Adding categories, elements and questions

```python
from write_aud import add_category, add_element, add_question

category = add_category(data, "Safety Requirements")
element = add_element(data, category["ID"], "SRS Content")

add_question(
    data,
    category_id=category["ID"],
    element_id=element["ID"],
    question_text="Does the SRS specify the proof-test interval for each SIF?",
    assessor_guidance="Locate the SRS. Confirm a proof-test interval is documented per SIF...",
    weighting_factor_id="Critical",          # optional: a label or ID; so is scoring_criterion_id
    reference_documents=[{"name": "IEC 61511", "clauses": ["10.3.2"]}],
)
```

Never hand-build these records. A category needs its totals and a question needs every field; the helpers supply both.

### Adding a team member or session

```python
from write_aud import add_team_member, add_session

tm_id = add_team_member(
    data,
    name="Pat Rivera",
    company="Kenexis",
    title="SIS Engineer",
)

add_session(
    data,
    session_label="Closeout Meeting",
    date="11/15/2025",
    assessor_id=tm_id,
)
```

### Adding evidence and linking it to a question

```python
from write_aud import add_evidence, link_evidence_to_question

ev_id = add_evidence(data, text="SIS Cause-and-Effect Matrix Rev 4, dated 2025-09-12")
link_evidence_to_question(data, question_id="qvarnyo6a3gghtl0aivlkh", evidence_id=ev_id)
```

### Removing a record

The writer offers `remove_team_member`, `remove_session`, and similar helpers. **None cascade-delete.** They detach the parent and leave orphan references for the user to handle.

```python
from write_aud import remove_team_member, list_orphan_attendance

remove_team_member(data, team_member_id="yjqzovxqsvqj7ussd3iojo")
orphans = list_orphan_attendance(data)
print(f"{len(orphans)} attendance rows now orphaned. Review before deciding.")
```

## Step 3 — Validate

Before writing, run the validator. It checks every safety rule and reports problems by severity.

```python
from write_aud import validate

issues = validate(data)
fatal = [i for i in issues if i["severity"] == "error"]
warnings = [i for i in issues if i["severity"] == "warning"]
for i in fatal:
    print("ERROR:", i["message"], i.get("path"))
if fatal:
    raise SystemExit("Refusing to write — fix errors first")
for i in warnings:
    print("WARN:", i["message"], i.get("path"))
```

The validator surfaces:

- **Errors** (fatal — refuse to write): a score with no criterion, or a criterion with no score; a question or category missing required fields; JSON `null` written to a sentinel field; integer/float on a protocol field; a link list entry that is not an `{"ID": ...}` object; missing or unknown top-level keys; missing `Column_Visibility` or `Column_Headers`; a newer `Ds_Rev` than this writer; `Encrypt: true`.
- **Warnings** (non-fatal but worth noting): scores or category totals out of date (run `recalculate(data)`); links to records that do not exist; empty elements; an older `Ds_Rev`; malformed IDs.
- **Info**: orphan attendance records.

## Step 4 — Save

```python
from write_aud import save
save(data, "/path/to/study.aud")     # overwrites in place if same path
save(data, "/path/to/study-revB.aud")  # write to a new path
```

`save` performs:
1. A final `validate(data)` pass; refuses to write on errors.
2. Compact JSON serialization with `(",", ":")` separators and `ensure_ascii=False`.
3. UTF-8 byte write with no trailing newline.

## Step 5 — Verify round-trip

After save, re-read the file and confirm semantic equality. The writer ships `verify_round_trip` for this purpose:

```python
from write_aud import verify_round_trip

result = verify_round_trip("/path/to/study.aud", data)
assert result["ok"], result["mismatches"]
print(f"Round-trip OK. File size: {result['file_size']} bytes.")
```

`ok` confirms the on-disk content reparses to a structure equal to `data`, and `byte_identical` confirms the bytes match its compact form.

## Step 6 — Present to the user

Finish the response with the path to the file and a one-line summary of what changed (count of edits, recommendations added, etc.), plus any warnings. Do not include a transcript of every edit.

## Creating a new file

```python
from write_aud import new_audit, save

data = new_audit(
    study_name="HAZOP Quality Audit — Unit 41",
    facility="Refinery North",
    facility_owner="Acme Energy",
    project_number="2300.001",
    project_description="HAZOP Quality Audit for Unit 41 revalidation",
    coordinator_name="Pat Rivera",
    coordinator_email="pat.rivera@kenexis.com",
)
save(data, "/path/to/new-audit.aud")
```

`new_audit` starts from `aud_template.json`, the application's own empty study, so the file has:
- A blank seed row in each collection.
- The standard Scoring_Criterias (Compliant / Partially Compliant / Non-Compliant) and Weighting_Factors (Critical / Important / Helpful / Optional).
- The application's default column settings. Weighting columns start hidden, as they do in a study created in the application.
- The current `Ds_Rev`.

## What NOT to do

- **Never** call `json.dump` with `indent=` or `sort_keys=True`. Use the included `save` function.
- **Never** convert sentinel strings to JSON null, real numbers, or stripped/normalized values.
- **Never** strip whitespace from string content — even if it looks like an accident, the desktop tool round-trips it and your edit will produce a noisy diff.
- **Never** decode HTML entities (`P&amp;ID` → `P&ID`) when reading or writing field content.
- **Never** delete the `"empty"` seed row from an empty collection, even if it looks like a useless empty row.
- **Never** write `Selected_Score` or another score field directly, and never call `validate_first=False` to get a score past validation.
- **Never** hand-build `Settings`, or category, element or question records.
- **Never** auto-cascade deletes. Removing a parent must not remove dependent records.
- **Never** rewrite or normalize IDs already present in the file.
- **Never** write to a file with `Settings.Encrypt: true`.
- **Never** bump `Settings.Ds_Rev` without explicit guidance.
- **Never** invent new top-level keys. The validator rejects unknown collections.

## Quality bar

The writer is correct if:

1. Loading a file, making no changes, and saving produces a **byte-identical** output to the input.
2. Loading a file, applying any single field change, and saving produces a file whose only diff is the bytes corresponding to that field.
3. Every saved file passes `validate(data)` with zero errors.
4. Re-loading any saved file produces an `Audit` object whose resolved views equal the in-memory state at save time.
5. Scores and category totals match what a row edit in the application would produce.
6. New files created via `new_audit` open cleanly in Open-Audit (this is the human verification step). Ask the user to open a new file and run a report before relying on it.
