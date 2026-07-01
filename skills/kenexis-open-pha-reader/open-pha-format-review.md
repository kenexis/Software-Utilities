# Open-PHA `.opha` File — Critical Format Review

Source file: `Texas City Gas Plant HAZOP-LOPA Cause-Indexed.opha` (351,238 bytes, single line, UTF-8).

This document is the analytical foundation for a future `open-pha-reader` / `open-pha-writer` skill set. It is the OPHA counterpart to `open-audit-format-review.md`. It catalogs the format's structure, its relationships, its data types, and — most importantly — the specific traps that will cause an AI agent to silently corrupt the file if it isn't told otherwise. It ends (§15) with a full field-by-field schema of the entire JSON object.

> **Single-sample caveat.** Every finding below is derived from **one** sample file: a *cause-indexed* HAZOP+LOPA study in `Analysis_Mode: "CauseConsequence"` with `Lopa_Mode: "Explicit"`. A deviation-indexed study, an implicit-LOPA study, or a checklist-only study may exercise field encodings this file does not. Where a rule is inferred from a single file rather than proven across modes, it is flagged. Treat this as a strong working spec, not a final one.

## 1. File Format at a Glance

The file is a **single-line, UTF-8-clean, JSON-encoded object**, written with no whitespace between separators (Python equivalent: `json.dumps(data, separators=(',', ':'), ensure_ascii=False)`). This was confirmed by re-serializing the parsed object with that exact encoder — the result was **byte-identical** to the original (351,238 bytes both ways).

The format is therefore round-trippable by any JSON library that:

1. Preserves insertion order of keys (Python 3.7+ dicts do this natively).
2. Uses a compact encoder with `(',', ':')` separators.
3. Does not normalize Unicode, escape non-ASCII as `\uNNNN`, or mutate string contents.

Pretty-printing for human review is fine internally, but the **bytes written back to disk must be the compact single-line form** for clean diffs and for the Open-PHA desktop tool to load the file as it expects.

This is the same wire convention as Open-Audit `.aud`. The two formats are cousins: same compact-JSON container, same opaque base36 IDs, same `Ds_Rev`/`Settings`/`Column_Visibility` GUI-state machinery, same `"empty"` seed-record idiom. **OPHA differs in three material ways** that the rest of this document dwells on: (a) it is a far larger and more deeply nested data model (a 4-level worksheet tree, not a flat question list); (b) it mixes **real JSON `null`/`true`/`false`** with **string sentinels `"null"`/`"true"`/`"false"`**, and the choice is field-specific; and (c) it uses **string-encoded scientific notation** for probabilities and frequencies.

## 2. Top-Level Schema

The document is a single JSON object with **21 top-level keys** — three configuration objects and eighteen record collections. Insertion order (as written by the tool) is:

| # | Key | Type | Purpose |
|---|---|---|---|
| 1 | `Overview` | object (19 keys) | Study metadata: name, coordinator, facility, project number, status, etc. |
| 2 | `Settings` | object (14 keys) | GUI state + semantic config (analysis mode, LOPA mode, numbering, visibility). See §11. |
| 3 | `Team_Members` | list | Roster of study participants. |
| 4 | `Sessions` | list | Working sessions held during the study. |
| 5 | `Team_Members_Sessions` | list | Many-to-many attendance join table. |
| 6 | `Revalidation_History` | list | Prior revalidation entries (seeded with an `"empty"` record). |
| 7 | `Nodes` | list | **The worksheet.** The 4-level HAZOP/LOPA tree — see §3. |
| 8 | `Safeguards` | list | Flat safeguard/IPL library, referenced by ID from consequences — see §4. |
| 9 | `Pha_Recommendations` | list | Flat PHA recommendation library, referenced by ID. |
| 10 | `Pha_Comments` | list | Flat PHA comment library, referenced by ID. |
| 11 | `Lopa_Recommendations` | list | Flat LOPA recommendation library, referenced by ID. |
| 12 | `Lopa_Comments` | list | Flat LOPA comment library, referenced by ID. |
| 13 | `Parking_Lot` | list | Deferred items (seeded with an `"empty"` record). |
| 14 | `Drawings` | list | Reference drawings (P&IDs, PFDs), referenced by ID from nodes. |
| 15 | `Risk_Criteria` | object (8 keys) | The risk-matrix definition — see §10. |
| 16 | `Check_Lists` | list | Checklists, each holding a nested `Check_List_Questions` list. |
| 17 | `Check_List_Recommendations` | list | Flat checklist-recommendation library, referenced by ID. |
| 18 | `Mocs` | list | Management-of-change entries (seeded with an `"empty"` record). |
| 19 | `Previous_Incidents` | list | Facility incident history (seeded with an `"empty"` record). |
| 20 | `Industry_Incidents` | list | Industry incident history (seeded with an `"empty"` record). |
| 21 | `Scais` | list | Safety controls, alarms & interlocks; references safeguards by ID. |

Note the spellings, which are wire-format and must not be "corrected": `Unit__Group` and `Sub__Unit` (double underscore), `E__Mail_Address` (double underscore), `Mocs` (not `MOCs`), `Scais` (not `SCAIs`).

## 3. The Worksheet — the `Nodes` Tree

This is the heart of the model and the biggest structural difference from Open-Audit. Where `.aud` is a flat three-level protocol (Categories → Elements → Questions), the OPHA worksheet is a **4-level containment tree** rooted at `Nodes`:

```
Nodes[]
└── Deviations[]
    └── Causes[]
        ├── Enabling_Events[]                     (LOPA initiating-event enablers)
        └── Consequences[]                        ← the analytical center of gravity
            ├── Pha_Recommendation_IDs[]          → ref to flat Pha_Recommendations
            ├── Pha_Comment_IDs[]                 → ref to flat Pha_Comments
            ├── Lopa_Recommendation_IDs[]         → ref to flat Lopa_Recommendations
            ├── Lopa_Comment_IDs[]                → ref to flat Lopa_Comments
            ├── Safeguard_IDs[]                   → ref to flat Safeguards
            ├── Alarp_Analysis[]                  (nested, inline)
            └── Conditional_Modifiers[]           (nested, inline)
```

In the sample: **7 nodes, 69 deviations, 98 causes, 99 consequences.** The `CauseConsequence` (cause-indexed) analysis mode is why causes own consequences directly; a deviation-indexed study would likely invert or flatten part of this and has not been observed.

**The `Consequence` record is the most complex object in the format** — ~30 fields. It carries the full risk picture at **three lifecycle stages**, encoded as parallel field families:

- **Before safeguards:** `Likelihood_ID_Before_Safeguards`, `Consequence_Severity_ID_Before_Safeguards`, `Risk_Rank_ID_Before_Safeguards`, `Lopa_Risk_Rank_ID_Before_Safeguards`.
- **Current (with safeguards):** `Likelihood_ID`, `Consequence_Severity_ID`, `Risk_Rank_ID`, `Lopa_Risk_Rank_ID`.
- **After recommendations:** `Likelihood_ID_After_Recommendations`, `Consequence_Severity_ID_After_Recommendations`, `Risk_Rank_ID_After_Recommendations`, `Lopa_Risk_Rank_ID_After_Recommendations`.

Plus LOPA quantities (`Tmel`, `Mel`, `Lopa_Ratio`, `Rrf`, `Recommended_Sil`, `Lopa_Required`), an ALARP flag (`Alarp_Required`), and the nested/reference lists above. Two sub-objects live **inline** inside each consequence rather than in a flat library: `Alarp_Analysis[]` and `Conditional_Modifiers[]`. `Enabling_Events[]` lives inline inside each **Cause**.

## 4. Flat Libraries and the ID-Reference System

Safeguards, recommendations, and comments are **not stored inline** in the worksheet. They live in flat top-level lists and are joined to consequences (and checklist questions, and SCAIs) by ID. The reference is always a **wrapped single-key object list**:

```json
"Safeguard_IDs": [{"ID":"3l9max1z2lbojg1a57vujc"}, {"ID":"9m0v70arq1p8mebuxntrxw"}]
```

This indirection is what lets one safeguard protect many scenarios (many-to-many). Referential integrity in the sample is intact: all 73 distinct safeguard references in the worksheet resolve to the flat `Safeguards` list, zero dangling. The reference fields observed:

| Reference field | Location | Points to |
|---|---|---|
| `Safeguard_IDs` | Consequence, Check_List_Question, Scai | `Safeguards[]` |
| `Pha_Recommendation_IDs` | Consequence | `Pha_Recommendations[]` |
| `Pha_Comment_IDs` | Consequence | `Pha_Comments[]` |
| `Lopa_Recommendation_IDs` | Consequence | `Lopa_Recommendations[]` |
| `Lopa_Comment_IDs` | Consequence | `Lopa_Comments[]` |
| `Check_List_Recommendation_IDs` | Check_List_Question | `Check_List_Recommendations[]` |
| `Session_IDs` | Node | `Sessions[]` |
| `Drawing_IDs` | Node | `Drawings[]` |
| `Facilitator_ID`, `Scribe_ID` | Session | `Team_Members[]` |
| `Team_Member_ID`, `Session_ID` | Team_Members_Sessions | `Team_Members[]`, `Sessions[]` |

Every reference list contains **at least one element**; when nothing is linked, that element is the `"empty"` sentinel (`[{"ID":"empty"}]`) — never an empty array. See §5.2.

## 5. Sentinel Conventions — The Most Important Section

OPHA relies on **string-literal sentinels** in places where most JSON formats would use real `null` or key-absence. But — unlike Open-Audit, which used *only* string sentinels — OPHA **also uses real JSON `null` and real JSON booleans in specific fields**. Confusing any of these is the single biggest corruption risk. There are effectively **five** distinct "unset/empty" markers, and which one is correct depends on the field:

### 5.1 The string `"null"` — "no value selected" (1,469 occurrences)

The dominant sentinel. Used for unset foreign keys, unset enumerations, and unfilled tri-state booleans. Fields observed carrying `"null"` include `Likelihood_ID` and its Before/After siblings, `Risk_Rank_ID`, `Consequence_Type_ID`, `Selected_Sil`, `EE_Library_Id`, `CM_Library_Id`, `Alarp_Analysis_Category_ID`, the `Safeguard_Independent/Auditable/Effective/Hackable` quartet, `Safety_Critical`, attendance `Value`, and the `*_Priority` / `*_Status` recommendation fields. **This is a string, not JSON `null`.**

### 5.2 The string `"empty"` — "no record / no reference" (1,018 occurrences)

Two uses, both structural:

- **Seed records.** Several collections ship with a single placeholder record whose `ID` is the literal string `"empty"` and whose other fields are blank: `Revalidation_History`, `Parking_Lot`, `Mocs`, `Previous_Incidents`, `Industry_Incidents`, and `Risk_Criteria.Alarp_Analysis_Categories` all carry an `"empty"` seed in the sample. These are the tool's "this collection has no real rows yet" state.
- **Empty references.** A reference-ID list with nothing linked is written as `[{"ID":"empty"}]`, **not** `[]`. The overwhelming majority of the 1,018 `"empty"` IDs are these placeholder references inside `Safeguard_IDs`, `Pha_Recommendation_IDs`, etc.

An agent that "cleans up" by deleting empty rows, collapsing `[{"ID":"empty"}]` to `[]`, or de-duplicating wrapped keys **will break the format.**

### 5.3 Empty string `""` — "user left this text blank"

Used for free-text and some ID fields the user has not filled in (`Boundary: ""`, `Node_Comments: ""`, and, notably, some ID fields like `Risk_Rank_ID: ""` and `Consequence_Severity_ID: ""`). Distinct from `"null"` and `"empty"`.

### 5.4 Real JSON `null` — used by a *specific* set of fields

This is the trap that has no analog in Open-Audit. A small, specific set of fields uses **real JSON `null`** (not the string) as their unset marker:

- `Consequence_Severity_ID` — and its `_Before_Safeguards` and `_After_Recommendations` siblings.
- `Severity_ID` inside `Risk_Criteria.Consequence_Intersections`.
- `Safeguard_Library_Version`.

The critical, non-obvious consequence: **`Likelihood_ID` uses the string `"null"` when unset, but the parallel field `Consequence_Severity_ID` uses real JSON `null`.** Same semantic ("no value selected"), same record, adjacent fields — different encodings. A writer must decide the unset encoding **per field name**, from the table in §15, and must never assume the severity family behaves like the likelihood family.

### 5.5 Real JSON `true` / `false` — GUI state only

Real booleans appear **only in `Settings`** (the `Numbering`, `Tab_Visibility`, `Column_Visibility`, `Column_Headers` trees, `Grid_Settings.invertAxis`, and the scalar flags `Encrypt`, `Consequence_Classification_Enabled`, `Presenter_Mode`). **No audit-data field is a real boolean.** Data-record booleans are strings — see §6.

## 6. Boolean Encoding — String Tri-State on Data Fields

Every boolean on a **data record** is a **string with three possible values**: `"true"`, `"false"`, or `"null"` (unset). Fields observed: `Is_Ipl`, `Is_Safeguard`, `Alarp_Required`, `Lopa_Required`, `Disabled`, `Check_List_Answer`, `Safety_Critical`, `Alarp_Analysis_Practical`.

So a checklist question's `Check_List_Answer` is `"true"` / `"false"` / `"null"` — a genuine tri-state, where `"null"` means "not yet answered." An agent that converts these to JSON booleans (or that maps `"null"` → `false`) destroys the unanswered/no distinction.

## 7. Number Encoding — Strings, Including Scientific Notation

Every numeric field on a **data record** is encoded as a **string**, and many use **scientific notation** exactly as the desktop tool's number formatter produced it:

- Probabilities / frequencies: `Frequency: "1E-3"`, `"1E+0"`; `EE_Probability: "0.25"`; `CM_Probability: "0.1"`.
- Safeguard PFD: `Pfd: "0.01"`, `"8.0E-3"`, `"0.001"`.
- LOPA quantities: `Tmel`, `Mel`, `Lopa_Ratio`, `Rrf` — all strings.
- Risk-criteria values: `Frequency: "1E-4"`, `RM_Tmel: "1E-5"`, `Code: "5"`, `Priority: "1"`, `Required_Lopa_Credits: "3"` — strings.

The **only true numbers in the file** are in `Settings`: `Ds_Rev` (int `39`) and `Column_Widths.*` (ints/floats, pixel widths). An agent that "fixes the schema" by converting `"8.0E-3"` to a float `0.008`, or that reformats `"1E+0"` to `"1"`, changes the persisted string the tool round-trips and will produce spurious diffs or reset values. **Preserve numeric strings verbatim, character-for-character.**

## 8. Enumerated String Values

Several fields are closed enumerations encoded as fixed strings. Observed value sets (may be non-exhaustive from one file):

| Field | Observed values |
|---|---|
| `Settings.Analysis_Mode` | `CauseConsequence` (deviation-indexed modes not observed) |
| `Settings.Lopa_Mode` | `Explicit` (`Implicit` not observed) |
| `Overview.Pha_Type` | `Project` |
| `Overview.Study_Status` | `In Progress` |
| `Selected_Sil` / `Recommended_Sil` | `NoSil`, `Sil1`, `Sil2`, plus `"null"` |
| attendance `Value` | `Present`, `Partial`, `Absent`, `"null"` |
| `Risk_Rankings.Color` | CSS-style color names, e.g. `maroon` |
| `*_Priority` / `*_Status` | free-ish strings + `"null"` (`Under Review`, etc.) |

## 9. ID System

IDs are **lowercase base36 strings** (character class `[0-9a-z]`), observed lengths **19–24** characters (mostly 21–22). The file contains 2,282 `ID` fields with 1,054 unique values; the remainder are the reused `"empty"` sentinel (1,018 occurrences). IDs are opaque foreign keys.

**Implication for the skill:** Never regenerate, normalize, or re-case an existing ID. When creating new records, generate IDs in the same character class and length range (a 21- or 22-char lowercase base36 random is safe). Never emit a real UUID with hyphens — that is not this format's shape.

## 10. `Risk_Criteria` — the Risk-Matrix Definition

A top-level **object** (not a list) with 8 sub-tables that jointly define the study's risk matrix and consequence taxonomy:

| Sub-table | Len (sample) | Record keys |
|---|---|---|
| `Likelihoods` | 5 | `ID`, `RM_Description`, `Frequency`, `Code` |
| `Severities` | 30 | `ID`, `Severity_Type`, `RM_Description`, `RM_Tmel`, `Code` |
| `Intersections` | 196 | `ID`, `Severity_ID`, `Likelihood_ID`, `Risk_Rank_ID` |
| `Risk_Rankings` | 7 | `ID`, `RM_Description`, `Code`, `Color`, `Priority`, `Required_Lopa_Credits` |
| `Consequence_Classifications` | 5 | `ID`, `CC_Description`, `Code`, `Severity_Type` |
| `Consequence_Magnitudes` | 1 | `ID`, `CS_Description`, `Code` |
| `Consequence_Intersections` | 5 | `ID`, `Consequence_Classification_ID`, `Consequence_Magnitude_ID`, `Severity_ID` |
| `Alarp_Analysis_Categories` | 1 | `ID`, `Alarp_Analysis_Category`, `Alarp_Analysis_Category_Description` (seeded `"empty"`) |

`Intersections` is the matrix lookup: 196 rows ≈ the full cross-product of severities and likelihoods, each mapping a (Severity_ID, Likelihood_ID) pair to a Risk_Rank_ID. `Severities` has 30 rows because severity is defined per `Severity_Type` (Safety, Environment, Asset, Community, Reputation — six categories × five levels). `Consequence_Intersections.Severity_ID` uses real JSON `null` when unmapped (see §5.4).

## 11. `Settings` — GUI State and Semantic Config

`Settings` mixes throwaway GUI state with load-bearing semantic configuration. The 14 keys:

**Semantic (a writer must get these right):**

- `Ds_Rev` — integer datastore revision, `39` in this file. The **only version marker** in the format. Preserve exactly; never invent a higher value. (Contrast: the Open-Audit sample was `Ds_Rev: 6`. OPHA's schema is much further evolved.)
- `Analysis_Mode` — `"CauseConsequence"`. Drives the worksheet tree shape.
- `Lopa_Mode` — `"Explicit"`. Drives whether LOPA fields are populated.
- `Numbering` — object of per-collection booleans (real JSON bool) toggling auto-numbering (`Nodes: true`, `Pha_Comments: false`, …).
- `Consequence_Category_Names` — maps the five internal severity types to display `{name, code}` pairs (e.g. `Safety → {"Safety and Environmental","S&E"}`).
- `Consequence_Classification_Enabled` — real bool.
- `Encrypt` — real bool `false`. Treat any `true` file as out-of-scope until the cipher is documented.
- `Study_Library_Id`, `Presenter_Mode` — config scalars.

**GUI state (round-trip untouched; never use as schema):**

- `Column_Widths` — 145 entries, ints/floats of pixel widths. Undercounts and over-counts the real field set; not authoritative. Includes vestigial keys (`reference`, `ref`) with no matching data field.
- `Column_Visibility` — per-collection show/hide tree with `<Collection>` booleans and `<Collection>_Children` field maps. **GUI state, not a data filter.** A field hidden here still holds real data. The reader must ignore this when extracting data and read every field on every record.
- `Column_Headers` — parallel tree of user-overridden column captions (mostly `""`).
- `Tab_Visibility` — which UI tabs are shown (`ParkingLotTab: false`, `ScaisTab: false`, …).
- `Grid_Settings` — matrix-widget layout for `Intersections`, `Team_Members_Sessions`, `Consequence_Intersections` (`headerHlocation`, `headerVlocation`, `invertAxis`).

**Skill rule:** round-trip `Column_Widths`, `Column_Visibility`, `Column_Headers`, `Tab_Visibility`, and `Grid_Settings` verbatim; never normalize, recompute, or treat as the source of truth for the data schema.

## 12. Required Fields and Well-Formedness

In the sample, **almost every declared field on a record is present on every record of that type** — the desktop tool writes the full field set on each row rather than omitting unset keys (it fills the correct sentinel instead). This strongly implies the safest well-formedness rule is: **emit the complete field set for a record type, in canonical order, using the correct sentinel for each unfilled field** (rather than omitting keys).

The fields that are **genuinely optional** (present on only some records in the sample) are all **library-integration fields** and one disable flag:

- `Disabled` (on `Nodes`, `Safeguards`) — appears only when a row has been disabled.
- `Cause_Library_Id`, `Cause_Library_Version` (on `Causes`).
- `Safeguard_Library_Id`, `Safeguard_Library_Version` (on `Safeguards`).
- `CM_Library_Id` (on `Conditional_Modifiers`).
- `EE_Library_Id` (on `Enabling_Events`) — present on all in this file, but is a library field and may be absent in files created without the content library.

Because these track an external content library, a from-scratch writer can safely **omit** them (or emit `""`/`"null"` per §15). Everything else should be present. **This "present-everywhere" observation comes from one file and is the highest-value thing to confirm against a second sample** (see §14).

## 13. What the Skill Must Do (Rules)

A correct `open-pha-reader` / `open-pha-writer` pair must obey these rules.

**On read:**

1. Parse JSON preserving key insertion order.
2. Do **not** decode HTML entities, strip whitespace, or otherwise mutate string contents.
3. Do **not** convert string `"null"`, `"empty"`, `"true"`, `"false"`, `"10"`, `"8.0E-3"` to their JSON equivalents.
4. Do **not** convert real JSON `null` on the severity family to a string, or vice versa.
5. Surface the schema as-is, sentinels intact, with convenience predicates (`is_unset(field, value)` that knows per-field whether unset means `""`, `"null"`, or JSON `null`).
6. **Ignore `Settings.Column_Visibility` when extracting data.** Read every field on every record.
7. Refuse to operate on a file with `Settings.Encrypt: true` until the encryption scheme is documented.

**On write (round-trip preservation):**

8. Serialize as compact JSON with `(',', ':')` separators, `ensure_ascii=False`, single line, no trailing newline.
9. Preserve key insertion order.
10. Preserve each sentinel exactly, using the **per-field** unset encoding from §15 (string `"null"` vs JSON `null` vs `""`).
11. Preserve string-encoded numbers verbatim, including scientific notation (`"8.0E-3"`, `"1E+0"`).
12. Preserve real numeric values on `Settings.Ds_Rev` and `Settings.Column_Widths.*`, and real booleans in the `Settings` trees.
13. Quality bar: an unmodified read→write is **byte-identical** to the input; a single-field edit changes only that field's bytes.

**On modification / creation:**

14. New IDs match the base36 `[0-9a-z]`, 21–22-char shape. Never emit hyphenated UUIDs.
15. Keep every reference list non-empty: use `[{"ID":"empty"}]` for "nothing linked," never `[]`.
16. Seed the seed-record collections (`Revalidation_History`, `Parking_Lot`, `Mocs`, `Previous_Incidents`, `Industry_Incidents`, `Alarp_Analysis_Categories`) with an `"empty"` record when they have no real rows.
17. Do **not** cascade-delete. Removing a Safeguard/Session/Team_Member should flag orphaned references for review, not silently rewrite the tree (mirror the Open-Audit rule).
18. When adding a record, emit the **complete** canonical field set (§15) with the correct sentinel per field; do not omit keys.
19. Bump `Ds_Rev` only when explicitly directed and the semantic change is understood.
20. A complete new study must also define `Risk_Criteria` (matrix) and the `Settings` semantic config; the worksheet's risk-rank/severity/likelihood IDs must reference IDs that exist in `Risk_Criteria`.

## 14. What to Verify Before Building the Writer

1. **A second sample in a different mode** — a deviation-indexed and/or implicit-LOPA study — to confirm the worksheet tree shape and which consequence fields are populated vs. sentinel.
2. **A file saved from the desktop tool with no edits**, to confirm the tool does not itself "clean" whitespace/entities/orphans, and that key order is stable across saves.
3. **A freshly created empty study**, to capture the exact seed state of every collection and the canonical `Risk_Criteria` defaults — the authoritative template for a from-scratch writer.
4. **A file with a real Parking_Lot / Moc / Previous_Incident row** alongside the seed, to confirm whether the `"empty"` seed stays in position when real records exist.
5. **Confirmation of the per-field unset encoding** in §15 against another file — especially whether the severity family is *always* real JSON `null` and the likelihood family *always* string `"null"`.
6. **Any schema shipped with the desktop tool** (JSON Schema / TypeScript types), which would be authoritative and remove the reverse-engineering uncertainty — including the true `Selected_Sil`, `Study_Status`, `Pha_Type`, and `Analysis_Mode` enumerations.

Until those exist, the writer should be conservative: read everything, change only what the user asked for, write it back exactly as encountered.

---

## 15. Full Schema of the Open-PHA JSON Object

The complete field-by-field schema, derived from the sample file. Notation:

- **Type/encoding** describes the *persisted* form: `string`, `string-num` (numeric value encoded as a string, incl. scientific notation), `string-bool` (`"true"`/`"false"`/`"null"`), `string-enum`, `id` (base36 FK), `id-list` (`[{"ID":…}]`, never empty — `[{"ID":"empty"}]` when unlinked), `int`, `float`, `bool` (real JSON), `null` (real JSON null as the unset marker).
- **Unset marker** is the value written when the field has no data: `""`, `"null"` (string), `null` (JSON), or `"empty"`.
- **Req** — `Y` = present on every record of this type in the sample (emit it); `opt` = library/optional field that may be omitted.

### 15.1 `Overview` (object)

| Field | Type | Unset | Notes |
|---|---|---|---|
| `Study_Name` | string | `""` | Study title. |
| `Study_Coordinator` | string | `""` | |
| `Study_Coordinator_Contact_Info` | string | `""` | Email/phone. |
| `Pha_Type` | string-enum | `""` | e.g. `Project`. |
| `Study_Status` | string-enum | `""` | e.g. `In Progress`. |
| `Revalidation_Due_Date` | string | `""` | `MM/DD/YYYY`. |
| `Facility` | string | `""` | |
| `Facility_Location` | string | `""` | |
| `Facility_Owner` | string | `""` | |
| `Overview_Company` | string | `""` | |
| `Site` | string | `""` | |
| `Plant` | string | `""` | |
| `Unit__Group` | string | `""` | double underscore. |
| `Unit` | string | `""` | |
| `Sub__Unit` | string | `""` | double underscore. |
| `Report_Number` | string | `""` | |
| `Project_Number` | string | `""` | |
| `Project_Description` | string | `""` | |
| `General_Notes` | string | `""` | |

### 15.2 `Settings` (object)

| Field | Type | Notes |
|---|---|---|
| `Ds_Rev` | int | Datastore revision (sample: 39). Only version marker. |
| `Analysis_Mode` | string-enum | `CauseConsequence` observed. |
| `Lopa_Mode` | string-enum | `Explicit` observed. |
| `Column_Widths` | object | 145 entries, int/float pixel widths. Round-trip only. |
| `Encrypt` | bool | Real bool. `true` ⇒ out of scope. |
| `Numbering` | object | Per-collection real-bool auto-number toggles (13 keys). |
| `Study_Library_Id` | string | `""` when unset. |
| `Consequence_Classification_Enabled` | bool | Real bool. |
| `Consequence_Category_Names` | object | 5 keys → `{name, code}` for Safety/Environment/Asset/Community/Reputation. |
| `Column_Visibility` | object | Show/hide tree (`<Collection>` + `<Collection>_Children`). GUI state. |
| `Tab_Visibility` | object | 12 `…Tab` real-bool flags. |
| `Column_Headers` | object | Overridden captions (mostly `""`). Parallels Column_Visibility. |
| `Presenter_Mode` | bool | Real bool. |
| `Grid_Settings` | object | Layout for `Intersections`, `Team_Members_Sessions`, `Consequence_Intersections` (`headerHlocation`, `headerVlocation`, `invertAxis`). |

### 15.3 `Team_Members[]`

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | — | Y |
| `Name` | string | `""` | Y |
| `Company` | string | `""` | Y |
| `Title` | string | `""` | Y |
| `Department` | string | `""` | Y |
| `Expertise` | string | `""` | Y |
| `Experience` | string | `""` | Y |
| `Phone_Number` | string | `""` | Y |
| `E__Mail_Address` | string | `""` | Y (double underscore) |
| `Team_Member_Comments` | string | `""` | Y |

### 15.4 `Sessions[]`

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | — | Y |
| `Date` | string | `""` | Y (`MM/DD/YYYY`) |
| `Duration` | string | `""` | Y |
| `Session` | string | `""` | Y (session name/number) |
| `Facilitator_ID` | id → Team_Members | `""`/`"null"` | Y |
| `Scribe_ID` | id → Team_Members | `""`/`"null"` | Y |
| `Session_Comments` | string | `""` | Y |

### 15.5 `Team_Members_Sessions[]` (attendance join)

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | — | Y |
| `Team_Member_ID` | id → Team_Members | `"empty"` | Y |
| `Session_ID` | id → Sessions | `"empty"` | Y |
| `Value` | string-enum | `"null"` | Y — `Present` / `Partial` / `Absent` / `"null"` |

### 15.6 `Revalidation_History[]` (seed collection)

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | `"empty"` (seed) | Y |
| `Start_Date` | string | `""` | Y |
| `End_Date` | string | `""` | Y |
| `Revalidation_Comments` | string | `""` | Y |

### 15.7 `Nodes[]`

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | — | Y |
| `Node_Description` | string | `""` | Y |
| `Intention` | string | `""` | Y |
| `Boundary` | string | `""` | Y |
| `Design_Conditions` | string | `""` | Y |
| `Operating_Conditions` | string | `""` | Y |
| `Node_Color` | string | `""` | Y (CSS color name/hex) |
| `Hazardous_Materials` | string | `""` | Y |
| `Equipment_Tags` | string | `""` | Y |
| `Location` | string | `""` | Y |
| `Node_Comments` | string | `""` | Y |
| `Session_IDs` | id-list → Sessions | `[{"ID":"empty"}]` | Y |
| `Drawing_IDs` | id-list → Drawings | `[{"ID":"empty"}]` | Y |
| `Deviations` | list<Deviation> | `[]`/seed | Y |
| `Disabled` | string-bool | — | opt (only when disabled) |

### 15.8 `Nodes[].Deviations[]`

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | — | Y |
| `Deviation` | string | `""` | Y |
| `Guide_Word` | string | `""` | Y |
| `Parameter` | string | `""` | Y |
| `Design_Intent` | string | `""` | Y |
| `Deviation_Comments` | string | `""` | Y |
| `Causes` | list<Cause> | — | Y |

### 15.9 `…Deviations[].Causes[]`

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | — | Y |
| `Cause` | string | `""` | Y |
| `Cause_Type` | string | `""` | Y |
| `Enabling_Events` | list<Enabling_Event> | — | Y |
| `Cause_Hackable` | string-bool | `""`/`"null"` | Y |
| `Frequency` | string-num | `""` | Y (incl. `1E-3`) |
| `Consequences` | list<Consequence> | — | Y |
| `Cause_Library_Id` | id | — | opt (library) |
| `Cause_Library_Version` | string | `""` | opt (library) |

### 15.10 `…Causes[].Enabling_Events[]`

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | `"empty"` | Y |
| `EE_Description` | string | `""` | Y |
| `EE_Library_Id` | id | `"null"` | Y (library FK) |
| `EE_Probability` | string-num | `""` | Y |

### 15.11 `…Causes[].Consequences[]` (the central record)

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | `"empty"` | Y |
| `Consequence` | string | `""` | Y |
| `Likelihood_ID_Before_Safeguards` | id → Likelihoods | `"null"` | Y |
| `Risk_Rank_ID_Before_Safeguards` | id → Risk_Rankings | `""` | Y |
| `Likelihood_ID` | id → Likelihoods | `"null"` | Y |
| `Risk_Rank_ID` | id → Risk_Rankings | `""`/`"null"` | Y |
| `Lopa_Required` | string-bool | `"null"` | Y |
| `Recommended_Sil` | string-enum | `""` | Y (`Sil1`…/`NoSil`) |
| `Pha_Recommendation_IDs` | id-list → Pha_Recommendations | `[{"ID":"empty"}]` | Y |
| `Likelihood_ID_After_Recommendations` | id → Likelihoods | `"null"` | Y |
| `Risk_Rank_ID_After_Recommendations` | id → Risk_Rankings | `""` | Y |
| `Pha_Comment_IDs` | id-list → Pha_Comments | `[{"ID":"empty"}]` | Y |
| `Lopa_Recommendation_IDs` | id-list → Lopa_Recommendations | `[{"ID":"empty"}]` | Y |
| `Lopa_Comment_IDs` | id-list → Lopa_Comments | `[{"ID":"empty"}]` | Y |
| `Alarp_Analysis` | list<Alarp_Analysis> | — | Y (inline) |
| `Alarp_Required` | string-bool | `"null"` | Y |
| `Consequence_Type_ID` | id → Consequence_Classifications | `"null"` | Y |
| `Consequence_Severity_ID_Before_Safeguards` | id → Severities | **`null` (JSON)** / `""` | Y |
| `Consequence_Severity_ID` | id → Severities | **`null` (JSON)** / `""` | Y |
| `Consequence_Severity_ID_After_Recommendations` | id → Severities | **`null` (JSON)** / `""` | Y |
| `Lopa_Risk_Rank_ID_Before_Safeguards` | id → Risk_Rankings | `""` | Y |
| `Lopa_Risk_Rank_ID` | id → Risk_Rankings | `""` | Y |
| `Lopa_Risk_Rank_ID_After_Recommendations` | id → Risk_Rankings | `""` | Y |
| `Conditional_Modifiers` | list<Conditional_Modifier> | — | Y (inline) |
| `Safeguard_IDs` | id-list → Safeguards | `[{"ID":"empty"}]` | Y |
| `Tmel` | string-num | `""` | Y |
| `Mel` | string-num | `""`/`"0"` | Y |
| `Lopa_Ratio` | string-num | `""` | Y |
| `Rrf` | string-num | `""` | Y |
| `Scenario_Hackable` | string-bool | `""` | Y |

> **Encoding trap (see §5.4):** the `Consequence_Severity_ID` family uses **real JSON `null`** as the unset marker, while the parallel `Likelihood_ID` and `Consequence_Type_ID` families use the **string `"null"`**. Encode per field name.

### 15.12 `…Consequences[].Alarp_Analysis[]` (inline)

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | `"empty"` | Y |
| `Alarp_Analysis_Category_ID` | id → Alarp_Analysis_Categories | `"null"` | Y |
| `Alarp_Analysis_Description` | string | `""` | Y |
| `Alarp_Analysis_Practical` | string-bool | `"null"` | Y |
| `Alarp_Analysis_Comments` | string | `""` | Y |

### 15.13 `…Consequences[].Conditional_Modifiers[]` (inline)

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | `"empty"` | Y |
| `CM_Description` | string | `""` | Y |
| `CM_Probability` | string-num | `""` | Y |
| `CM_Library_Id` | id | `"null"` | opt (library) |

### 15.14 `Safeguards[]` (flat library)

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | — | Y |
| `Safeguard` | string | `""` | Y |
| `Safeguard_Type` | string-enum | `""` | Y (e.g. `PSV`) |
| `Ipl_Tag` | string | `""` | Y |
| `Safeguard_Category` | string | `""` | Y |
| `Is_Safeguard` | string-bool | `"null"` | Y |
| `Safeguard_Independent` | string-bool | `"null"` | Y |
| `Safeguard_Auditable` | string-bool | `"null"` | Y |
| `Safeguard_Effective` | string-bool | `"null"` | Y |
| `Safeguard_Hackable` | string-bool | `"null"` | Y |
| `Is_Ipl` | string-bool | `"null"` | Y |
| `Pfd` | string-num | `""` | Y (incl. `8.0E-3`) |
| `Safety_Critical` | string-bool | `"null"` | Y |
| `Selected_Sil` | string-enum | `"null"` | Y (`NoSil`/`Sil1`/`Sil2`) |
| `Required_Response_Time` | string | `""` | Y |
| `Test_Interval` | string | `""` | Y |
| `Safeguard_Comments` | string | `""` | Y |
| `Disabled` | string-bool | — | opt |
| `Safeguard_Library_Id` | id | — | opt (library) |
| `Safeguard_Library_Version` | int/`null` | `null` (JSON) | opt (library) |

### 15.15 `Pha_Recommendations[]` (flat library)

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | — | Y |
| `Pha_Recommendation` | string | `""` | Y |
| `Pha_Recommendation_Priority` | string-enum | `"null"` | Y |
| `Pha_Recommendation_Responsible_Party` | string | `""` | Y |
| `Pha_Recommendation_Status` | string-enum | `"null"` | Y |
| `Pha_Recommendation_Due_Date` | string | `""` | Y (`MM/DD/YYYY`) |
| `Pha_Recommendation_Comments` | string | `""` | Y |

### 15.16 `Pha_Comments[]` / `Lopa_Comments[]` (flat libraries)

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | — | Y |
| `Pha_Comment` / `Lopa_Comment` | string | `""` | Y |

### 15.17 `Lopa_Recommendations[]` (flat library)

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | — | Y |
| `Lopa_Recommendation` | string | `""` | Y |
| `Lopa_Recommendation_Pfd` | string-num | `""` | Y |
| `Lopa_Recommendation_Priority` | string-enum | `"null"` | Y |
| `Lopa_Recommendation_Responsible_Party` | string | `""` | Y |
| `Lopa_Recommendation_Status` | string-enum | `"null"` | Y |
| `Lopa_Recommendation_Due_Date` | string | `""` | Y |
| `Lopa_Recommendation_Comments` | string | `""` | Y |

### 15.18 `Parking_Lot[]` (seed collection)

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | `"empty"` (seed) | Y |
| `Parking_Lot_Issue` | string | `""` | Y |
| `Response` | string | `""` | Y |
| `Responsible_Party` | string | `""` | Y |
| `Start_Date` | string | `""` | Y |
| `End_Date` | string | `""` | Y |

### 15.19 `Drawings[]`

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | — | Y |
| `Drawing` | string | `""` | Y (drawing number) |
| `Revision` | string | `""` | Y |
| `Document_Type` | string-enum | `""` | Y (P&ID, PFD, …) |
| `Drawing_Description` | string | `""` | Y |
| `Link` | string | `""` | Y (URL/path) |

### 15.20 `Check_Lists[]` and nested `Check_List_Questions[]`

`Check_Lists[]`:

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | — | Y |
| `Check_List_Description` | string | `""` | Y |
| `Check_List_Comments` | string | `""` | Y |
| `Check_List_Questions` | list<Check_List_Question> | — | Y |

`Check_Lists[].Check_List_Questions[]`:

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | — | Y |
| `Check_List_Question` | string | `""` | Y |
| `Check_List_Answer` | string-bool | `"null"` | Y (tri-state: `"true"`/`"false"`/`"null"`) |
| `Check_List_Justification` | string | `""` | Y |
| `Check_List_Recommendation_IDs` | id-list → Check_List_Recommendations | `[{"ID":"empty"}]` | Y |
| `Safeguard_IDs` | id-list → Safeguards | `[{"ID":"empty"}]` | Y |

### 15.21 `Check_List_Recommendations[]` (flat library)

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | — | Y |
| `Check_List_Recommendation` | string | `""` | Y |
| `Check_List_Recommendation_Priority` | string-enum | `"null"` | Y |
| `Check_List_Recommendation_Responsible_Party` | string | `""` | Y |
| `Check_List_Recommendation_Status` | string-enum | `"null"` | Y |
| `Check_List_Recommendation_Due_Date` | string | `""` | Y |
| `Check_List_Recommendation_Comments` | string | `""` | Y |

### 15.22 `Mocs[]` (seed collection)

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | `"empty"` (seed) | Y |
| `Moc_Number` | string | `""` | Y |
| `Moc_Short_Description` | string | `""` | Y |
| `Moc_Long_Description` | string | `""` | Y |
| `Moc_Status` | string-enum | `""` | Y |
| `Moc_Duration` | string | `""` | Y |
| `Moc_Link` | string | `""` | Y |
| `Moc_Comments` | string | `""` | Y |

### 15.23 `Previous_Incidents[]` (seed collection)

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | `"empty"` (seed) | Y |
| `Previous_Incident_Reference` | string | `""` | Y |
| `Previous_Incident_Date` | string | `""` | Y |
| `Previous_Incident_Level` | string | `""` | Y |
| `Previous_Incident_Description` | string | `""` | Y |
| `Previous_Incident_Status` | string-enum | `""` | Y |
| `Previous_Incident_Action` | string | `""` | Y |
| `Previous_Incident_Link` | string | `""` | Y |
| `Previous_Incident_Comments` | string | `""` | Y |

### 15.24 `Industry_Incidents[]` (seed collection)

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | `"empty"` (seed) | Y |
| `Industry_Incident_Source` | string | `""` | Y |
| `Industry_Incident_Description` | string | `""` | Y |
| `Industry_Incident_Action` | string | `""` | Y |
| `Industry_Incident_Link` | string | `""` | Y |
| `Industry_Incident_Comments` | string | `""` | Y |

### 15.25 `Scais[]` (safety controls, alarms & interlocks)

| Field | Type | Unset | Req |
|---|---|---|---|
| `ID` | id | — | Y |
| `Scai_Description` | string | `""` | Y |
| `Scai_Type` | string-enum | `""` | Y |
| `Scai_Hackable` | string-bool | `""`/`"null"` | Y |
| `Scai_Tag` | string | `""` | Y |
| `Scai_Safety_Critical` | string-bool | `"null"` | Y |
| `Scai_Selected_Sil` | string-enum | `""`/`"null"` | Y |
| `Scai_Required_Response_Time` | string | `""` | Y |
| `Scai_Test_Interval` | string | `""` | Y |
| `Scai_Category` | string | `""` | Y |
| `Scai_Comments` | string | `""` | Y |
| `Safeguard_IDs` | id-list → Safeguards | `[{"ID":"empty"}]` | Y |

### 15.26 `Risk_Criteria` (object of sub-tables)

`Likelihoods[]`:

| Field | Type | Notes |
|---|---|---|
| `ID` | id | |
| `RM_Description` | string | e.g. `Insignificant` |
| `Frequency` | string-num | e.g. `1E-4` |
| `Code` | string-num | e.g. `0` |

`Severities[]`:

| Field | Type | Notes |
|---|---|---|
| `ID` | id | |
| `Severity_Type` | string-enum | `Safety`/`Environment`/`Asset`/`Community`/`Reputation` |
| `RM_Description` | string | e.g. `Very High - Multiple Fatalities` |
| `RM_Tmel` | string-num | target mitigated event likelihood, e.g. `1E-5` |
| `Code` | string-num | e.g. `5` |

`Intersections[]` (matrix lookup):

| Field | Type | Notes |
|---|---|---|
| `ID` | id | |
| `Severity_ID` | id → Severities | |
| `Likelihood_ID` | id → Likelihoods | |
| `Risk_Rank_ID` | id → Risk_Rankings | |

`Risk_Rankings[]`:

| Field | Type | Notes |
|---|---|---|
| `ID` | id | |
| `RM_Description` | string | e.g. `Very High` |
| `Code` | string | e.g. `V` |
| `Color` | string-enum | CSS color name, e.g. `maroon` |
| `Priority` | string-num | e.g. `1` |
| `Required_Lopa_Credits` | string-num | e.g. `3` |

`Consequence_Classifications[]`:

| Field | Type | Notes |
|---|---|---|
| `ID` | id | |
| `CC_Description` | string | may be `""` |
| `Code` | string | may be `""` |
| `Severity_Type` | string-enum | as above |

`Consequence_Magnitudes[]`:

| Field | Type | Notes |
|---|---|---|
| `ID` | id | |
| `CS_Description` | string | may be `""` |
| `Code` | string | may be `""` |

`Consequence_Intersections[]`:

| Field | Type | Notes |
|---|---|---|
| `ID` | id | |
| `Consequence_Classification_ID` | id → Consequence_Classifications | |
| `Consequence_Magnitude_ID` | id → Consequence_Magnitudes | |
| `Severity_ID` | id → Severities / **`null` (JSON)** | real JSON null when unmapped |

`Alarp_Analysis_Categories[]` (seed collection):

| Field | Type | Notes |
|---|---|---|
| `ID` | id / `"empty"` | seeded `"empty"` |
| `Alarp_Analysis_Category` | string | may be `""` |
| `Alarp_Analysis_Category_Description` | string | may be `""` |

---

*Prepared from a single sample (`Texas City Gas Plant HAZOP-LOPA Cause-Indexed.opha`, Ds_Rev 39, CauseConsequence / Explicit-LOPA). Confirm §12 (required-field completeness) and §5.4 (per-field null encoding) against additional samples before relying on the writer in production. Cross-reference with `open-audit-format-review.md` for the sibling `.aud` format.*
