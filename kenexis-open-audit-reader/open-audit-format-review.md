# Open-Audit `.aud` File — Critical Format Review

Source file: `SIS Functional Safety Assessment.aud` (180,709 bytes, single line, ASCII).

This document is the analytical foundation for a future `kenexis-open-audit-reader` / `kenexis-open-audit-writer` skill set. It catalogs the format's structure, its quirks, and — most importantly — the specific traps that will cause an AI agent to silently corrupt the file if it isn't told otherwise.

## 1. File Format at a Glance

The file is a **single-line, UTF-8-clean, JSON-encoded object**, written with no whitespace between separators (Python equivalent: `json.dumps(data, separators=(',', ':'), ensure_ascii=False)`). I confirmed this by re-serializing the parsed object with that exact encoder — the result was **byte-identical** to the original (180,709 bytes both ways). The format is therefore round-trippable by any JSON library that:

1. Preserves insertion order of keys (Python 3.7+ dicts do this; many languages need a special parser).
2. Uses a compact encoder.
3. Does not normalize Unicode, escape non-ASCII as `\uNNNN`, or mutate string contents.

Pretty-printing for human review is fine internally, but the **bytes written back to disk must be the compact form** if you want clean diffs in a git-like workflow or matching versions inside the Open-Audit desktop tool.

## 2. Top-Level Schema

The document is a single JSON object with thirteen top-level keys, each holding either a metadata object or a flat collection of records:

| Key | Type | Purpose |
|---|---|---|
| `Overview` | object (11 keys) | Study metadata: name, coordinator, facility, project number, etc. |
| `Settings` | object (4 keys) | GUI state: column widths, column visibility, encryption flag, datastore revision. |
| `Team_Members` | list | Roster of audit team members. |
| `Sessions` | list | Interview/working sessions held during the audit. |
| `Team_Members_Sessions` | list | Many-to-many attendance join table. |
| `Revalidation_History` | list | Prior revalidation entries. |
| `Categories` | list | The audit protocol itself — see §3. |
| `Assessor_Recommendations` | list | Findings and corrective actions. |
| `Scoring_Criterias` | list | Score rubric (Compliant / Partially Compliant / Non-Compliant). |
| `Weighting_Factors` | list | Question weights (Critical / Important / Helpful / Optional). |
| `Parking_Lot` | list | Items deferred mid-audit. |
| `Drawings` | list | Reference drawings. |
| `Evidences` | list | Evidence items cited by questions. |

Note the spelling: `Scoring_Criterias` (sic — pluralized as "criterias"), `Team_Members_Sessions` (snake_case but no separator between the two compound nouns). These are not typos to correct; they're the wire-format and changing them will disconnect every downstream reference.

## 3. The Audit Protocol — `Categories`

This is the most important structure. A category contains elements; an element contains questions. Three nested levels:

```
Categories[]
└── Elements[]
    └── Questions[]
        └── Reference_Documents[]
            └── Clauses[]
```

The sample contains 6 categories, 13 elements, 160 questions. Each level has its own `ID`. Categories also carry a `Session_IDs` list (which sessions this category was discussed in).

A `Question` carries the following keys, **in the exact order they appear in the file**:

```json
{
  "ID": "qvarnyo6a3gghtl0aivlkh",
  "Question_Description": "Has a Process Hazards Analysis ...",
  "Assessor_Notes": "A HAZOP for the entire gas plant ...",
  "Assessor_Guidance": "Verify program is in place to ensure ...",
  "Scoring_Criteria_ID": "do8dfmjmubjkmqaygs3a7",
  "Selected_Score": "10",
  "Weighting_Factor_ID": "null",
  "Selected_Weighting_Factor": "",
  "Weighted_Score": "",
  "Evidence_IDs": [{"ID": "3tbjnb0256kqx9b8scztw"}],
  "Assessor_Recommendation_IDs": [{"ID": "empty"}],
  "Reference_Documents": [{"ID": "...", "Reference_Document_Description": "IEC 61511", "Clauses": [{"ID": "...", "Clause_Description": "8.2.1"}]}],
  "Findings": ""
}
```

The `Assessor_Guidance` field is exactly the column we just added to the HAZOP protocol question catalog — meaning the Open-Audit format is already designed around the same assessor-guidance pattern. Good architectural alignment for the HAZOP-audit skill.

## 4. ID System

IDs are **lowercase base36-style strings** (characters `[0-9a-z]`) typically 21–22 characters long, but with observed lengths of 19 to 24. There are 1,168 `ID` fields in this file producing 828 unique values; the gap (340) is fully accounted for by sentinel reuse — see §5.

IDs are foreign keys: `Scoring_Criteria_ID`, `Weighting_Factor_ID`, `Team_Member_ID`, `Session_ID`, `Assessor_ID`, plus the wrapped-list patterns `Evidence_IDs: [{"ID": ...}]`, `Assessor_Recommendation_IDs: [{"ID": ...}]`, and `Session_IDs: [{"ID": ...}]`. Wrapping each foreign-key reference in a single-key object is unusual — likely an artifact of the desktop tool's table-binding code — but it's the format and must be preserved.

**Implication for the skill:** ID values are opaque tokens. Never regenerate, normalize, or re-case them. When creating new records, generate IDs in the same character class and length range; the safest choice is a 21- or 22-character lowercase alphanumeric (e.g., `secrets.token_hex(11)` truncated, or a 22-char base36 random).

## 5. Sentinel Conventions — The Most Important Section

This format relies on **string-literal sentinels** in places where most modern JSON formats would use real `null` or absence-of-key. Confusing them with their JSON counterparts is the single biggest corruption risk.

### 5.1 The string `"null"` is *not* JSON `null`

The file contains **414 occurrences of the string `"null"`** and **zero occurrences of JSON `null`**. The desktop tool treats `"null"` as a sentinel for "no value selected" in fields that are otherwise expected to carry a foreign key or enumerated value. Fields observed using this sentinel:

- `Weighting_Factor_ID`
- `Selected_Score` (sometimes)
- `Assessor_Recommendation_Priority`
- `Assessor_Recommendation_Status`
- `Team_Members_Sessions[*].Value` (alongside `"Present"` and `"Partial"`)

An AI agent that "tidies" the file by converting `"null"` → JSON `null` will silently break every downstream lookup in the desktop tool. The skill must explicitly preserve the string form.

### 5.2 The string `"empty"` is the sentinel for an unfilled record/reference

Every collection in the file ships with at least one **placeholder record whose `ID` is the literal string `"empty"`** and whose other fields are blank. Examples from this file:

- `Parking_Lot[0].ID = "empty"` with empty fields — the seed record.
- `Drawings[0].ID = "empty"` — same.
- `Revalidation_History[0].ID = "empty"` — same.
- Inside questions: `Assessor_Recommendation_IDs: [{"ID": "empty"}]` is used to mean "no recommendation linked," not "linked to a record with ID=empty."

There are **328 instances of `ID = "empty"`** scattered throughout the file. An AI agent that:

- removes "obviously empty" rows,
- de-duplicates wrapped foreign keys,
- collapses `[{"ID": "empty"}]` lists to `[]`,

… will break the format. The desktop tool relies on these sentinel rows being present.

### 5.3 Empty string `""` is its own thing

Empty string is used for free-text fields that the user has not filled in (`Findings: ""`, `Selected_Weighting_Factor: ""`, `Weighted_Score: ""`). Don't conflate with `"null"` or `"empty"` — they're three distinct markers with three distinct meanings.

## 6. Number-as-String Convention

Every numeric field on a `Question` is encoded as a **string**, not a number:

- `Selected_Score: "10"`
- `Scoring_Criteria_Level: "10"`
- `Weighting_Factor_Score: "10"`
- `Weighted_Score: ""` (empty string when not yet computed)

The sole exception is `Settings.Ds_Rev: 6` (true integer) and `Settings.Column_Widths.*` (true floats like `300.594`, `299.6060485839844`).

**Implication:** an AI agent that "fixes the schema" by converting `"10"` to `10` will produce a file the Open-Audit desktop tool may refuse to load or may load with reset values. Preserve the string form on protocol/score fields and the numeric form on Settings fields. The skill must distinguish them by path, not by value.

## 7. Whitespace and HTML Entities in String Values

Two string-content quirks need preservation:

### 7.1 Trailing whitespace and tabs

I found **155 string fields with leading or trailing whitespace** in this file, including pathological examples like:

```
"Element_Description": "Layer of Protection Analysis (LOPA) / SIL Selection\t\t\t\t\n"
```

Four `Element_Description` entries end with `\t\t\t\t\n`. These are paste artifacts from a source spreadsheet, but the desktop tool stored and round-tripped them without complaint, so they're now part of the persisted state. An AI agent that strips whitespace on read or write will trigger a diff against the user's last save.

### 7.2 HTML entities are pre-encoded

Free-text fields contain pre-encoded HTML entities, e.g.:

```
"Findings": "Some potential SIF that were shown on the P&amp;IDs were not discussed in the PHA..."
```

Note the `P&amp;ID` rather than `P&ID`. This means the desktop tool renders these fields through an HTML-aware widget and the persisted form is HTML-escaped. A skill that "cleans up" entities (`&amp;` → `&`) will both corrupt the file *and* cause re-display problems in the desktop tool when it next escapes the now-bare ampersand.

The safe rule for the skill: **read string values verbatim, write string values verbatim.** If a user wants to edit text, edit the persisted (entity-encoded) form, not a decoded one.

## 8. Orphan Records — the Cleanup Trap

The `Team_Members_Sessions` table illustrates a recurring pattern:

- Roster has **3 team members**, calendar has **3 sessions** → 9 valid attendance combinations expected.
- The file contains **300 attendance records**, referencing **33 distinct team-member IDs** and **13 distinct session IDs**.
- Of those, **30 of 33 team-member IDs are not in the roster** and **10 of 13 session IDs are not in the calendar**.

This means the desktop tool **does not cascade-delete attendance records when a team member or session is removed.** The orphans persist forever. A well-meaning AI agent that "cleans up dangling references" will (a) remove a large fraction of the file, (b) potentially confuse the desktop tool's internal counters, and (c) destroy audit history if the orphans are later resurrected.

**Skill rule:** Treat the file as immutable except for explicit user-driven edits. Never auto-cascade. If the user wants cleanup, surface the orphans for review and require confirmation per record.

## 9. Settings Section — GUI State

The `Settings` object has four top-level keys: `Ds_Rev`, `Column_Widths`, `Column_Visibility`, and `Encrypt`. Each has its own behavior the skill must respect.

### 9.1 `Ds_Rev` — the datastore revision

`Settings.Ds_Rev: 6` is the **only version marker in the entire file**. It is a plain integer at the root of `Settings`. No separate file format version, schema version, application version, or per-collection version exists — `Ds_Rev` is the single hook the desktop tool has for forward-compatibility.

**Skill rule:** Preserve `Ds_Rev` exactly as read. Never invent a higher value. Bump it only when Kenexis publishes explicit guidance about what semantic changes correspond to a given revision. Writing `Ds_Rev: 7` against a real `Ds_Rev: 6` desktop tool could cause it to refuse to load the file or trigger an unintended migration path.

### 9.2 `Column_Widths` — pixel state

`Settings.Column_Widths` contains **floats with absurd precision** (e.g., `299.6060485839844`). These are literal pixel widths from the desktop window manager and will be different on every machine.

Two notable substructures inside `Column_Widths`:

- **Vestigial entries.** The map includes the keys `reference` and `Group_Description`, neither of which corresponds to an actual field name anywhere in the data. These are likely leftovers from earlier datastore revisions or reserved for unimplemented features. The reader should not treat them as a defect; the writer must round-trip them.
- **Schema discoverability.** Width entries roughly correspond to columns the GUI knows how to display. They are *not* an authoritative schema — they undercount the real field set (54 width entries vs. 89 distinct field names actually used in records).

**Skill rule:** Round-trip `Column_Widths` untouched. Never normalize, round, or recompute. Never use it as the schema source of truth.

### 9.3 `Column_Visibility` — the show/hide tree

This is the field that drives whether the GUI shows or hides a given column inside a record table. Important for the audit-protocol skill because **fields can be hidden in the GUI but still hold real data**.

The structure is a tree that mirrors the data hierarchy. Top-level collections (`Team_Members`, `Sessions`, `Categories`, etc.) appear as visibility booleans, alongside `<CollectionName>_Children` entries whose values are objects keyed by individual field names. Categories nests deeper because of the `Categories → Elements → Questions → Reference_Documents` structure:

```
Column_Visibility
├── Overview: true
├── Overview_Children: { Study_Name: true, Facility_Location: false, ... }
├── Team_Members: true
├── Team_Members_Children: { Name: true, Department: false, Expertise: false, ... }
├── Categories: true
└── Categories_Children:
    └── Elements_Children:
        └── Questions_Children: { Question_Description: true, Selected_Score: false, ... }
            └── Reference_Documents_Children: { ... }
```

In this sample file, **thirteen fields are currently hidden**:

| Collection | Hidden fields |
|---|---|
| `Overview` | `Facility_Location` |
| `Team_Members` | `Department`, `Expertise`, `Experience`, `Phone_Number`, `E__Mail_Address`, `Team_Member_Comments` |
| `Sessions` | `Duration`, `Session_Comments` |
| `Categories → Elements → Questions` | `Selected_Score`, `Weighting_Factor_ID`, `Selected_Weighting_Factor`, `Weighted_Score` |

Four of those thirteen are on the `Question` record itself. A protocol skill that reads only the GUI-visible columns would silently miss the score, the weighting factor, the selected weighting factor, and the weighted score for every question — exactly the data the audit consumes.

Two complications the reader must handle:

- **Some data fields never appear in `Column_Visibility` at all.** Structural fields — `ID`, `Session_IDs`, `Evidence_IDs`, `Assessor_Recommendation_IDs`, `Reference_Documents`, `Clauses`, and the top-level collection wrappers — are present in records but not declared in the visibility tree. These are always-on; the GUI does not expose them as user-toggleable columns. The skill must never interpret "absent from `Column_Visibility`" as "hidden."
- **Some `Column_Visibility` keys never appear as data fields.** `Group_Description` and a few `_Children` containers exist in the visibility tree but not in records. Like the vestigial widths, these should be preserved on round-trip and ignored semantically.

**Skill rule:** **The reader ignores `Column_Visibility` entirely when extracting data for the audit protocol.** Read every field on every record. The visibility tree is GUI state; it is not a data filter and it is not a schema. The only legitimate use case for the visibility tree is optional report-rendering ("the assessor chose to hide notes; should we hide them in the published audit report?"), and even that is a downstream concern, not a reading-stage concern.

The interpretation rule for any individual field, written precisely:

```
field_visible_in_gui(record_kind, field_name) =
    Column_Visibility[record_kind + "_Children"][field_name] == true
    if that path exists; otherwise true (always-on by default).
```

Even when this returns `false`, the field's value in the data must still be read.

### 9.4 `Encrypt`

`Settings.Encrypt: false` — a boolean. The desktop tool likely supports encrypted audit files. None of our samples exercise the encrypted path, so the skill should preserve the flag as-is and refuse to operate on a file with `Encrypt: true` until Kenexis documents the cipher and key derivation. Treat any encrypted-flagged file as out-of-scope until then.

## 10. Key Ordering

JSON technically has no order. The Open-Audit desktop tool produces files with a **specific, stable insertion order** within every object. Reserializing with `sort_keys=True` produced a non-identical file (same byte count, different content arrangement). Diff tools and version control will surface every key-order change as a "modification" — so the writer skill should preserve insertion order. In Python, this is the default; in many languages, it requires `OrderedDict` or equivalent.

## 11. What the Skill Must Do (Rules)

A correct `open-audit-reader` / `open-audit-writer` pair must obey these rules:

**On read:**

1. Parse JSON preserving key insertion order.
2. Do **not** decode HTML entities in string values.
3. Do **not** strip whitespace from string values.
4. Do **not** convert string `"null"`, `"empty"`, `"10"`, etc. to their JSON equivalents.
5. Surface the schema as-is to consumers, with the sentinel conventions intact.
6. Provide convenience accessors that *interpret* sentinels (e.g., `is_null(value)` returns true for both JSON null and `"null"` string), without mutating the underlying data.
7. **Ignore `Settings.Column_Visibility` when extracting data.** Read every field on every record. Visibility is GUI state, not a data filter. Absent keys mean "always on," not "hidden."
8. Refuse to operate on a file with `Settings.Encrypt: true` until the encryption scheme is documented.
9. Do not treat `Settings.Column_Widths` as a schema. It undercounts real fields and contains vestigial entries (`reference`, `Group_Description`) that have no corresponding data.

**On write (round-trip preservation):**

10. Serialize as compact JSON with `(',', ':')` separators and no ASCII escaping.
11. Preserve key insertion order.
12. Preserve sentinel string values (`"null"`, `"empty"`) exactly.
13. Preserve string-encoded numbers on protocol fields (`Selected_Score: "10"`, not `10`).
14. Preserve true numeric values on `Settings.Column_Widths.*` and `Settings.Ds_Rev`.
15. Write a single line, no trailing newline.

**On modification:**

16. New IDs must match the existing character class and length distribution (21–22 char lowercase alphanumeric is safe).
17. When adding a child to a list whose first record is the `"empty"` sentinel, **do not delete the sentinel** — append after it (or insert at index 1) unless the desktop tool's behavior is verified.
18. When deleting a parent record (Team_Member, Session, etc.), do **not** cascade. Flag orphans for the user to review.
19. When adding a Question, populate every key in the canonical order shown in §3, with the right sentinel for each unfilled field.
20. Bump `Settings.Ds_Rev` only when explicitly directed and the format change is understood. `Ds_Rev` is the **only** version marker in the entire file; treat it as authoritative for forward-compatibility decisions.
21. Round-trip `Settings.Column_Widths` and `Settings.Column_Visibility` untouched, including vestigial entries that don't correspond to any data field.
22. When the user adds or removes a field via the skill (extending the schema), update the corresponding `Column_Visibility.<Collection>_Children` entry — defaulting new fields to visible (`true`) — and add a width entry. Do not change visibility on fields the user did not touch.

**On creation (new files):**

23. Seed every collection with an `"empty"` sentinel record matching the field shape of real records.
24. Include the canonical Scoring_Criterias and Weighting_Factors entries (or accept them as configuration), and reference their IDs in subsequent records.

## 12. Risks of an Incorrect Skill

If any of the rules above are violated, the symptoms a user will see (in order of severity):

- **File silently corrupts** but loads — the desktop tool ignores or resets fields, user loses data they thought was saved.
- **File refuses to load** — the desktop tool's parser rejects the file outright (less common; more graceful than silent corruption but more visibly broken).
- **Round-trip diffs** — every edit looks like an edit-everywhere-change in version control, even if the user only changed one field. Erodes trust and makes review impossible.
- **Orphan-related crashes** — if the desktop tool relies on orphan records for some calculation, removing them may produce divide-by-zero or null-deref bugs.
- **Encoding double-escape or double-unescape** — `P&amp;ID` becomes `P&amp;amp;ID` or vice versa, slowly degrading every text field across edits.

## 13. Recommended Skill Structure

Two skills (or one skill with both modes) — pure markdown, following the same template as the existing Kenexis skills:

### `kenexis-open-audit-reader`

- **Inputs:** path to `.aud` file.
- **Outputs:** a structured Python dict (or analogue) representing the Audit, with all sentinels preserved and accompanied by helper predicates (`is_unset`, `is_empty_sentinel`, `is_null_sentinel`).
- **Procedure:** load file as bytes → decode UTF-8 → parse JSON with order preservation → wrap in an `Audit` accessor object that exposes typed views (`.questions()`, `.team_members()`, `.findings()`, etc.) without mutation.
- **Quality bar:** output dict + `json.dumps(..., separators=(',',':'), ensure_ascii=False)` produces byte-identical content to the input.

### `kenexis-open-audit-writer`

- **Inputs:** a parsed Audit object (from the reader) and any modifications.
- **Outputs:** a new `.aud` file on disk.
- **Procedure:** validate sentinel conventions on every modified field → ID-generate new records → write as compact JSON with the encoder settings in §11 rule 7 → flush as single line.
- **Quality bar:** if no changes were made, the output file is byte-identical to the input. If a single field was changed, only the bytes corresponding to that field differ.

A third helper skill — `kenexis-open-audit-template` — would generate a fresh empty `.aud` file with the canonical sentinel seeds in every collection, ready to be populated.

## 14. What I'd Verify Next Before Building the Skill

1. **A second sample file** to confirm the sentinel patterns are consistent (e.g., is `"null"` always lowercase? Always for the same fields?).
2. **A file produced by saving a slightly-modified copy** of this one in the desktop tool, to see exactly which keys change and whether key ordering is stable across edits.
3. **A file with a real Parking_Lot or Drawings entry** alongside the seed, to confirm the seed stays in position when real records exist.
4. **A file that has been opened and re-saved without changes**, to check whether the desktop tool ever "cleans" anything (whitespace, entities, orphans) on its own. If it does, the skill can mirror that behavior; if it doesn't, the skill must not.
5. **The schema (if any) shipped with the desktop tool** — a JSON Schema or TypeScript type definition would be authoritative and remove the reverse-engineering uncertainty.

Until those samples exist, the skill should be conservative: read everything, change only what the user asked for, and write it back exactly as encountered.
