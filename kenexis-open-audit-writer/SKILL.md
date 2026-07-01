---
name: kenexis-open-audit-writer
description: Modify and write Kenexis Open-Audit (.aud) files without corrupting them. Preserves sentinel values ("null", "empty"), HTML entities, whitespace, key insertion order, and string-encoded numbers on protocol fields. Use when the user wants to add, edit, or remove audit content (questions, scores, findings, recommendations, evidence, team members, sessions) and save the result back to a .aud file. Trigger phrases include "edit .aud", "modify Open-Audit", "add a question to the audit", "record findings", "add team member", "save Open-Audit", or any task that mutates a Kenexis Open-Audit document.
---

# Kenexis Open-Audit Writer

## Purpose

Open-Audit `.aud` files have several format conventions that ordinary JSON tooling will silently destroy: string-literal sentinels (`"null"`, `"empty"`), pre-encoded HTML entities, intentional trailing whitespace, string-encoded numbers on protocol fields, key-order significance, and orphan records that must be left in place. This skill writes `.aud` files in a way that survives the desktop tool's loader, produces clean diffs in version control, and never silently mutates fields the user did not touch.

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
2. **The string `"empty"` is not absence.** On `ID` fields it is the format's placeholder/seed marker. Many collections have a permanent `{"ID": "empty", ...}` row whose other fields are blank. Do not delete these rows. When inserting new records, append after the sentinel row, not before.
3. **Numbers on protocol fields are strings.** `Selected_Score: "10"`, `Scoring_Criteria_Level: "10"`, `Weighting_Factor_Score: "10"`. Never coerce to integers. The exception is `Settings.Ds_Rev` (real integer) and `Settings.Column_Widths.*` (real floats); preserve those as numbers.
4. **HTML entities are pre-encoded** in free-text fields (`P&amp;ID`, not `P&ID`). Write content with entities pre-encoded. If a user supplies raw text, the writer applies HTML escaping before insertion.
5. **Whitespace in string values is preserved.** Trailing tabs, newlines, leading spaces — the desktop tool round-trips them and so must this skill. Never `.strip()` field content.
6. **Key insertion order is significant** for diff hygiene. Use the canonical key order defined in this skill (or the order from the reader output if loaded from disk).
7. **Compact serialization, no trailing newline.** The file is one line: `json.dumps(data, separators=(",",":"), ensure_ascii=False)`.
8. **No cascade-delete.** When the user removes a Team_Member or Session, attendance records that reference it become orphans. Leave them in place. Surface the orphans for the user to decide.
9. **Settings is GUI state.** Round-trip `Column_Widths`, `Column_Visibility`, and `Encrypt` untouched. Bump `Ds_Rev` only with explicit guidance.
10. **Refuse to write a file with `Settings.Encrypt: true`** until the encryption scheme is documented.

## High-level workflow

1. **Load the source file** via the reader skill (or accept an in-memory `audit.data`).
2. **Apply the user's changes** using the helpers in `write_aud.py` (`set_field`, `add_question`, `add_team_member`, `record_finding`, `add_recommendation`, etc.). Helpers enforce the safety rules above.
3. **Validate** the modified data with `validate(data)`. The validator returns a list of warnings/errors; fix anything fatal before saving.
4. **Save** with `save(data, output_path)`. The saver applies canonical compact JSON serialization.
5. **Verify the round-trip** by re-loading the saved file and comparing semantic content against the in-memory state. The verifier confirms no fields drifted.
6. **Present the result file** to the user with a `computer://` link.

## Step 1 — Load the source

For an existing file:

```python
import sys
sys.path.insert(0, '/path/to/kenexis-open-audit-reader')
sys.path.insert(0, '/path/to/kenexis-open-audit-writer')
from read_aud import load
from write_aud import save, validate, add_question, set_field, record_finding

audit = load("/path/to/study.aud")
data = audit.data   # the verbatim parsed object — this is what you mutate
```

For a new file from a template:

```python
from write_aud import new_audit
data = new_audit(study_name="My Study", facility="Plant 1", ds_rev=6)
```

## Step 2 — Apply changes

The writer ships helper functions for every common edit. Use them rather than mutating `data` by hand — the helpers handle the sentinels, ID generation, and key ordering for you.

### Setting a field on an existing record

```python
from write_aud import set_field, find_question

q = find_question(data, question_id="qvarnyo6a3gghtl0aivlkh")
set_field(q, "Selected_Score", "10")             # string, not int
set_field(q, "Findings", "Verified during interview on 2026-05-01.")
```

`set_field` enforces:
- Strings stored as strings (it will refuse `set_field(q, "Selected_Score", 10)` and ask for `"10"`).
- HTML escaping applied to free-text fields if the input contains unescaped `&`, `<`, or `>`.
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

### Adding a question to an element

```python
from write_aud import add_question

add_question(
    data,
    category_id="p898sk7oofhw7uswo1u2p",
    element_id="1a92y12dvsu8byvi6aw90o",
    question_text="Does the SRS specify the proof-test interval for each SIF?",
    assessor_guidance="Locate the SRS. Confirm a proof-test interval is documented per SIF...",
    scoring_criterion_id="do8dfmjmubjkmqaygs3a7",
    weighting_factor_id=None,                # writer stores as the "null" sentinel
    reference_documents=[{"name": "IEC 61511", "clauses": ["10.3.2"]}],
)
```

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

- **Errors** (fatal — refuse to write): JSON `null` written to a sentinel field; integer/float on a protocol field; missing required key on a record; invalid ID format; `Encrypt: true`.
- **Warnings** (non-fatal but worth noting): orphan foreign keys; trailing whitespace introduced by an edit; HTML entities not encoded.
- **Info**: count of records added/removed since load.

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

If the file was loaded with no changes, the verifier additionally checks **byte-identical** equality with the original. If the file was modified, it confirms the on-disk content reparses to a structure equal to `data`.

## Step 6 — Present to the user

When the writer has saved a file inside the workspace folder, finish the response with a single `computer://` link to the file and a one-line summary of what changed (count of edits, recommendations added, etc.). Do not include a transcript of every edit.

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

`new_audit` seeds the file with:
- The canonical `"empty"` sentinel record in every collection.
- The standard Scoring_Criterias (Compliant / Partially Compliant / Non-Compliant) and Weighting_Factors (Critical / Important / Helpful / Optional).
- A default `Column_Visibility` map with every field visible.
- `Ds_Rev: 6` (the version observed in the reference sample; override only if you know the target tool's expectation).

## What NOT to do

- **Never** call `json.dump` with `indent=` or `sort_keys=True`. Use the included `save` function.
- **Never** convert sentinel strings to JSON null, real numbers, or stripped/normalized values.
- **Never** strip whitespace from string content — even if it looks like an accident, the desktop tool round-trips it and your edit will produce a noisy diff.
- **Never** decode HTML entities (`P&amp;ID` → `P&ID`) when reading or writing field content.
- **Never** delete the `"empty"` sentinel record from a collection, even if it looks like a useless empty row.
- **Never** auto-cascade deletes. Removing a parent must not remove dependent records.
- **Never** rewrite or normalize IDs already present in the file.
- **Never** write to a file with `Settings.Encrypt: true`.
- **Never** bump `Settings.Ds_Rev` without explicit guidance.
- **Never** invent new top-level keys. The writer rejects unknown collections.

## Quality bar

The writer is correct if:

1. Loading a file, making no changes, and saving produces a **byte-identical** output to the input.
2. Loading a file, applying any single field change, and saving produces a file whose only diff is the bytes corresponding to that field.
3. Every saved file passes `validate(data)` with zero errors.
4. Re-loading any saved file produces an `Audit` object whose resolved views equal the in-memory state at save time.
5. New files created via `new_audit` open cleanly in the Open-Audit desktop tool (this is the human verification step — the test cannot be automated until we have a programmatic loader for the desktop tool's behavior).

The included `write_aud.py` script enforces criteria 1–4 automatically. Criterion 5 must be tested manually by saving a file from this skill and opening it in the Open-Audit desktop tool.
