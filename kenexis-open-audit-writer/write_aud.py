#!/usr/bin/env python3
"""
write_aud.py — Kenexis Open-Audit (.aud) writer

Save modifications to a .aud file in a way that survives the Open-Audit desktop
tool's loader. Preserves the format's quirks: string-literal sentinels
("null", "empty"), pre-encoded HTML entities, intentional whitespace,
string-encoded protocol numbers, key insertion order, and orphan records.

Use the helper functions for edits — they encode the safety rules. Direct
mutation of the parsed data dict is allowed but at your own risk; run
validate() before save() either way.

CLI usage (limited — most use is programmatic):
    python3 write_aud.py validate FILE.aud
    python3 write_aud.py round-trip FILE.aud   # load + save with no changes; expects byte equality
"""
import argparse
import html
import json
import os
import secrets
import string
import sys
from collections import OrderedDict
from pathlib import Path


# --------------------------- Constants & sentinels ------------------------- #

NULL_SENTINEL = "null"
EMPTY_SENTINEL = "empty"

# Fields that legitimately use the "null" string sentinel (per format review).
KNOWN_NULL_SENTINEL_FIELDS = {
    "Weighting_Factor_ID",
    "Selected_Score",
    "Assessor_Recommendation_Priority",
    "Assessor_Recommendation_Status",
    "Value",  # on Team_Members_Sessions
}

# Fields that store numbers as strings (protocol layer).
STRING_NUMERIC_FIELDS = {
    "Selected_Score",
    "Scoring_Criteria_Level",
    "Weighting_Factor_Score",
    "Weighted_Score",
}

# Top-level collections expected in a valid file.
TOP_LEVEL_KEYS = [
    "Overview",
    "Settings",
    "Team_Members",
    "Sessions",
    "Team_Members_Sessions",
    "Revalidation_History",
    "Categories",
    "Assessor_Recommendations",
    "Scoring_Criterias",
    "Weighting_Factors",
    "Parking_Lot",
    "Drawings",
    "Evidences",
]

# Canonical Scoring_Criterias and Weighting_Factors used when seeding new files.
DEFAULT_SCORING_CRITERIA = [
    {"Scoring_Criteria_Description": "Compliant", "Scoring_Criteria_Level": "10",
     "Scoring_Criteria_Assessor_Notes": "All of the standards requirements have been met."},
    {"Scoring_Criteria_Description": "Partially Compliant", "Scoring_Criteria_Level": "3",
     "Scoring_Criteria_Assessor_Notes": "The preponderance of the standards requirements have been met, but some gaps exist."},
    {"Scoring_Criteria_Description": "Non-Compliant", "Scoring_Criteria_Level": "0",
     "Scoring_Criteria_Assessor_Notes": "An insufficient portion of the standards requirements have been met to consider it to be partially compliant."},
]

DEFAULT_WEIGHTING_FACTORS = [
    {"Weighting_Factor_Code": "Critical", "Weighting_Factor_Description": "Critical to be compliant for appropriate implementation", "Weighting_Factor_Score": "10"},
    {"Weighting_Factor_Code": "Important", "Weighting_Factor_Description": "Important to be compliant for appropriate implementation", "Weighting_Factor_Score": "8"},
    {"Weighting_Factor_Code": "Helpful", "Weighting_Factor_Description": "Implementation is helpful to acheive objectives, not might not be required if other measures or methods are taken", "Weighting_Factor_Score": "5"},
    {"Weighting_Factor_Code": "Optional", "Weighting_Factor_Description": "Implementation is optional, objectives can be achieved with alternate means", "Weighting_Factor_Score": "0"},
]


# ------------------------------ ID generation ------------------------------ #

_ID_ALPHABET = string.digits + string.ascii_lowercase  # base36

def make_id(length=22):
    """Generate a base36-style random ID of the requested length (default 22).
    Matches the character class and length of IDs observed in real .aud files."""
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(length))


def is_valid_id(value):
    """True if the value looks like a real .aud ID (not 'empty' or 'null')."""
    if not isinstance(value, str):
        return False
    if value in (NULL_SENTINEL, EMPTY_SENTINEL, ""):
        return False
    if len(value) < 18 or len(value) > 26:
        return False
    return all(c in _ID_ALPHABET for c in value)


# ----------------------------- HTML encoding ------------------------------- #

def _encode_html(text):
    """Apply minimal HTML escaping for free-text fields. Idempotent for already-
    escaped content (won't double-escape '&amp;')."""
    if not isinstance(text, str):
        return text
    # Heuristic: if the text already contains pre-encoded entities, return as-is.
    if "&amp;" in text or "&lt;" in text or "&gt;" in text or "&quot;" in text or "&#" in text:
        return text
    return html.escape(text, quote=False)


# ------------------------------ Set field ---------------------------------- #

def set_field(record, key, value):
    """Set a field on a record, enforcing the format's conventions.

    - For STRING_NUMERIC_FIELDS, raises if value is not a string.
    - For KNOWN_NULL_SENTINEL_FIELDS, allows None to mean the "null" sentinel.
    - Applies HTML escaping to plausibly free-text fields.
    - Preserves whitespace verbatim.
    """
    if key in STRING_NUMERIC_FIELDS:
        if value is None:
            value = NULL_SENTINEL
        elif not isinstance(value, str):
            raise ValueError(
                f"Field {key!r} stores numbers as strings; got {type(value).__name__} "
                f"({value!r}). Pass a string."
            )
    elif key in KNOWN_NULL_SENTINEL_FIELDS and value is None:
        value = NULL_SENTINEL

    # Free-text fields: encode HTML if appears to be raw.
    free_text = key in {
        "Findings", "Assessor_Notes", "Assessor_Guidance",
        "Question_Description", "Element_Description", "Category_Description",
        "Assessor_Recommendation", "Assessor_Recommendation_Comments",
        "Parking_Lot_Issue", "Response", "Drawing_Description",
        "General_Notes", "Project_Description", "Session_Comments",
        "Revalidation_Comments", "Team_Member_Comments", "Evidence",
    }
    if free_text and isinstance(value, str):
        value = _encode_html(value)

    record[key] = value


# ------------------------ Find / list helpers ------------------------------ #

def find_question(data, question_id):
    """Locate a question record by ID. Returns the dict in place (mutations
    affect the underlying data)."""
    for cat in data.get("Categories", []):
        for el in cat.get("Elements", []):
            for q in el.get("Questions", []):
                if q.get("ID") == question_id:
                    return q
    raise KeyError(f"Question {question_id!r} not found")


def find_category(data, category_id):
    for cat in data.get("Categories", []):
        if cat.get("ID") == category_id:
            return cat
    raise KeyError(f"Category {category_id!r} not found")


def find_element(data, category_id, element_id):
    cat = find_category(data, category_id)
    for el in cat.get("Elements", []):
        if el.get("ID") == element_id:
            return el
    raise KeyError(f"Element {element_id!r} not found in category {category_id!r}")


# --------------------------- Add-record helpers ---------------------------- #

def add_team_member(data, name, company="", title="", department="",
                    expertise="", experience="", phone_number="",
                    email="", comments=""):
    """Append a team member record. Returns the new ID."""
    tm_id = make_id()
    record = OrderedDict([
        ("ID", tm_id),
        ("Name", name),
        ("Company", company),
        ("Title", title),
        ("Department", department),
        ("Expertise", expertise),
        ("Experience", experience),
        ("Phone_Number", phone_number),
        ("E__Mail_Address", email),
        ("Team_Member_Comments", comments),
    ])
    data.setdefault("Team_Members", []).append(record)
    return tm_id


def add_session(data, session_label, date="", duration="", assessor_id=None, comments=""):
    """Append a session record. Returns the new ID."""
    s_id = make_id()
    record = OrderedDict([
        ("ID", s_id),
        ("Date", date),
        ("Duration", duration),
        ("Session", session_label),
        ("Assessor_ID", assessor_id if assessor_id else ""),
        ("Session_Comments", comments),
    ])
    data.setdefault("Sessions", []).append(record)
    return s_id


def mark_attendance(data, team_member_id, session_id, value="Present"):
    """Add a Team_Members_Sessions row. Value: 'Present', 'Partial', or None
    (which becomes the 'null' sentinel)."""
    if value is None:
        value = NULL_SENTINEL
    record = OrderedDict([
        ("ID", make_id()),
        ("Team_Member_ID", team_member_id),
        ("Session_ID", session_id),
        ("Value", value),
    ])
    data.setdefault("Team_Members_Sessions", []).append(record)
    return record["ID"]


def add_evidence(data, text):
    ev_id = make_id()
    record = OrderedDict([("ID", ev_id), ("Evidence", _encode_html(text))])
    data.setdefault("Evidences", []).append(record)
    return ev_id


def add_recommendation(data, text, priority=None, responsible_party="",
                       status=None, comments=""):
    """Append a recommendation. Priority/status are strings like 'High'/'Open'
    or None (-> 'null' sentinel). Returns the new recommendation ID."""
    rec_id = make_id()
    record = OrderedDict([
        ("ID", rec_id),
        ("Assessor_Recommendation", _encode_html(text)),
        ("Assessor_Recommendation_Priority", NULL_SENTINEL if priority is None else priority),
        ("Assessor_Recommendation_Responsible_Party", responsible_party),
        ("Assessor_Recommendation_Status", NULL_SENTINEL if status is None else status),
        ("Assessor_Recommendation_Comments", _encode_html(comments)),
    ])
    data.setdefault("Assessor_Recommendations", []).append(record)
    return rec_id


def link_evidence_to_question(data, question_id, evidence_id):
    q = find_question(data, question_id)
    lst = q.setdefault("Evidence_IDs", [])
    # Remove any "empty" sentinel entries first appearance? Per safety rule, keep them.
    # Append after the sentinel.
    lst.append(OrderedDict([("ID", evidence_id)]))


def link_recommendation_to_question(data, question_id, recommendation_id):
    q = find_question(data, question_id)
    lst = q.setdefault("Assessor_Recommendation_IDs", [])
    lst.append(OrderedDict([("ID", recommendation_id)]))


def add_question(data, category_id, element_id, question_text,
                 assessor_guidance="", assessor_notes="",
                 scoring_criterion_id=None, weighting_factor_id=None,
                 reference_documents=None):
    """Append a fully-populated Question record to an element. Returns ID."""
    el = find_element(data, category_id, element_id)
    q_id = make_id()
    refs = []
    for rd in (reference_documents or []):
        rd_id = make_id()
        clauses = []
        for c in rd.get("clauses", []):
            clauses.append(OrderedDict([("ID", make_id()), ("Clause_Description", c)]))
        refs.append(OrderedDict([
            ("ID", rd_id),
            ("Reference_Document_Description", rd.get("name", "")),
            ("Clauses", clauses if clauses else [OrderedDict([("ID", EMPTY_SENTINEL), ("Clause_Description", "")])]),
        ]))
    if not refs:
        refs = [OrderedDict([
            ("ID", EMPTY_SENTINEL),
            ("Reference_Document_Description", ""),
            ("Clauses", [OrderedDict([("ID", EMPTY_SENTINEL), ("Clause_Description", "")])]),
        ])]

    record = OrderedDict([
        ("ID", q_id),
        ("Question_Description", _encode_html(question_text)),
        ("Assessor_Notes", _encode_html(assessor_notes)),
        ("Assessor_Guidance", _encode_html(assessor_guidance)),
        ("Scoring_Criteria_ID", scoring_criterion_id if scoring_criterion_id else NULL_SENTINEL),
        ("Selected_Score", ""),
        ("Weighting_Factor_ID", weighting_factor_id if weighting_factor_id else NULL_SENTINEL),
        ("Selected_Weighting_Factor", ""),
        ("Weighted_Score", ""),
        ("Evidence_IDs", [OrderedDict([("ID", EMPTY_SENTINEL)])]),
        ("Assessor_Recommendation_IDs", [OrderedDict([("ID", EMPTY_SENTINEL)])]),
        ("Reference_Documents", refs),
        ("Findings", ""),
    ])
    el.setdefault("Questions", []).append(record)
    return q_id


def record_finding(data, question_id, findings, recommendation_text=None,
                   recommendation_priority=None, responsible_party=""):
    """Set Findings text on a question. Optionally creates a recommendation and
    links it back to the question."""
    q = find_question(data, question_id)
    set_field(q, "Findings", findings)
    if recommendation_text:
        rec_id = add_recommendation(
            data,
            text=recommendation_text,
            priority=recommendation_priority,
            responsible_party=responsible_party,
        )
        link_recommendation_to_question(data, question_id, rec_id)


# ----------------------------- Removals (no cascade) ---------------------- #

def remove_team_member(data, team_member_id):
    """Remove a team member record. Does NOT remove attendance entries that
    reference it; those become orphans by design."""
    before = len(data.get("Team_Members", []))
    data["Team_Members"] = [tm for tm in data.get("Team_Members", []) if tm.get("ID") != team_member_id]
    return before - len(data["Team_Members"])


def remove_session(data, session_id):
    before = len(data.get("Sessions", []))
    data["Sessions"] = [s for s in data.get("Sessions", []) if s.get("ID") != session_id]
    return before - len(data["Sessions"])


def list_orphan_attendance(data):
    member_ids = {tm.get("ID") for tm in data.get("Team_Members", [])}
    session_ids = {s.get("ID") for s in data.get("Sessions", [])}
    return [
        row for row in data.get("Team_Members_Sessions", [])
        if row.get("Team_Member_ID") not in member_ids or row.get("Session_ID") not in session_ids
    ]


# ------------------------------- Validation -------------------------------- #

def validate(data):
    """Run safety-rule checks. Returns a list of {severity, message, path} dicts.
    severity is 'error' | 'warning' | 'info'."""
    issues = []

    def add(sev, msg, path=""):
        issues.append({"severity": sev, "message": msg, "path": path})

    # Top-level shape
    for k in TOP_LEVEL_KEYS:
        if k not in data:
            add("error", f"Missing top-level key {k!r}", k)

    # Settings checks
    settings = data.get("Settings", {})
    if not isinstance(settings.get("Ds_Rev"), int):
        add("error", "Settings.Ds_Rev must be an integer", "Settings.Ds_Rev")
    if settings.get("Encrypt") is True:
        add("error", "Encrypted .aud files are not supported", "Settings.Encrypt")

    # Walk all dict/list nodes looking for JSON null on sentinel fields and
    # numeric values on string-numeric fields. Skip the Settings subtree —
    # within Settings, key names like 'Selected_Score' are pixel widths or
    # visibility booleans, not protocol fields.
    def walk(node, path):
        if path == "Settings" or path.startswith("Settings."):
            return
        if isinstance(node, dict):
            for k, v in node.items():
                child_path = f"{path}.{k}" if path else k
                if child_path == "Settings" or child_path.startswith("Settings."):
                    continue
                if v is None and k in KNOWN_NULL_SENTINEL_FIELDS:
                    add("error", f"JSON null on sentinel field {k!r}; expected the string \"null\"", child_path)
                if k in STRING_NUMERIC_FIELDS and not isinstance(v, str) and v is not None:
                    add("error", f"Field {k!r} must be a string ({v!r} is {type(v).__name__})", child_path)
                walk(v, child_path)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    walk(data, "")

    # ID format checks
    def check_ids(node, path):
        if isinstance(node, dict):
            if "ID" in node and isinstance(node["ID"], str):
                v = node["ID"]
                if v not in (NULL_SENTINEL, EMPTY_SENTINEL) and not is_valid_id(v):
                    add("warning", f"ID {v!r} doesn't match expected format (length 18-26, base36)", f"{path}.ID")
            for k, val in node.items():
                check_ids(val, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                check_ids(item, f"{path}[{i}]")

    check_ids(data, "")

    # Orphan check (informational)
    orphans = list_orphan_attendance(data)
    if orphans:
        add("info", f"{len(orphans)} orphan attendance record(s) — preserved (no cascade)", "Team_Members_Sessions")

    return issues


# --------------------------------- Save ----------------------------------- #

def save(data, path, *, validate_first=True):
    """Write data to a .aud file using canonical compact serialization.
    Refuses to write if validation surfaces errors (set validate_first=False
    to override, at your own risk)."""
    if validate_first:
        issues = validate(data)
        errors = [i for i in issues if i["severity"] == "error"]
        if errors:
            details = "\n".join(f"  - {e['path']}: {e['message']}" for e in errors)
            raise ValueError(f"Validation failed; refusing to write:\n{details}")

    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    p = Path(path)
    # Write atomically: write to a temp path then rename.
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, p)
    return str(p)


# -------------------------- Round-trip verification ----------------------- #

def verify_round_trip(saved_path, in_memory_data):
    """After save(), confirm the saved file reparses to a structure equal to the
    in-memory data. Returns {ok, mismatches, file_size}."""
    with open(saved_path, "r", encoding="utf-8") as f:
        text = f.read()
    parsed = json.loads(text)
    mismatches = _diff(in_memory_data, parsed, path="")
    return {
        "ok": not mismatches,
        "mismatches": mismatches,
        "file_size": len(text.encode("utf-8")),
    }


def _diff(a, b, path=""):
    """Return a list of mismatches between two structures.

    Treats any dict subclass (dict, OrderedDict) as equivalent — Python 3.7+
    dicts preserve insertion order, so the runtime class doesn't matter as long
    as keys and values match. Same for any list-like."""
    mm = []
    if isinstance(a, dict) and isinstance(b, dict):
        ak, bk = list(a.keys()), list(b.keys())
        if ak != bk:
            mm.append(f"{path}: key order/contents differ {ak[:5]} vs {bk[:5]}")
        for k in a:
            if k not in b:
                mm.append(f"{path}.{k}: missing in saved file")
                continue
            mm.extend(_diff(a[k], b[k], f"{path}.{k}"))
        return mm
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            mm.append(f"{path}: length {len(a)} vs {len(b)}")
        for i, (ai, bi) in enumerate(zip(a, b)):
            mm.extend(_diff(ai, bi, f"{path}[{i}]"))
        return mm
    if type(a) != type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        mm.append(f"{path}: type {type(a).__name__} vs {type(b).__name__}")
        return mm
    if a != b:
        mm.append(f"{path}: value {a!r} vs {b!r}")
    return mm


# ----------------------------- New file template -------------------------- #

def new_audit(study_name="", facility="", facility_owner="", facility_location="",
              project_number="", project_description="", report_number="",
              unit="", coordinator_name="", coordinator_email="", general_notes="",
              ds_rev=6):
    """Construct a fresh .aud document, seeded with the canonical empty
    sentinels and standard scoring/weighting tables."""
    data = OrderedDict()
    data["Overview"] = OrderedDict([
        ("Study_Name", study_name),
        ("Study_Coordinator", coordinator_name),
        ("Study_Coordinator_Contact_Info", coordinator_email),
        ("Facility", facility),
        ("Facility_Location", facility_location),
        ("Facility_Owner", facility_owner),
        ("Unit", unit),
        ("Report_Number", report_number),
        ("Project_Number", project_number),
        ("Project_Description", _encode_html(project_description)),
        ("General_Notes", _encode_html(general_notes)),
    ])
    data["Settings"] = OrderedDict([
        ("Ds_Rev", ds_rev),
        ("Column_Widths", OrderedDict()),
        ("Encrypt", False),
        ("Column_Visibility", _default_visibility()),
    ])
    data["Team_Members"] = []
    data["Sessions"] = []
    data["Team_Members_Sessions"] = []
    data["Revalidation_History"] = [OrderedDict([
        ("ID", EMPTY_SENTINEL),
        ("Start_Date", ""),
        ("End_Date", ""),
        ("Revalidation_Comments", ""),
    ])]
    data["Categories"] = []
    data["Assessor_Recommendations"] = [OrderedDict([
        ("ID", EMPTY_SENTINEL),
        ("Assessor_Recommendation", ""),
        ("Assessor_Recommendation_Priority", NULL_SENTINEL),
        ("Assessor_Recommendation_Responsible_Party", ""),
        ("Assessor_Recommendation_Status", NULL_SENTINEL),
        ("Assessor_Recommendation_Comments", ""),
    ])]
    data["Scoring_Criterias"] = [
        OrderedDict([("ID", make_id()), *list(sc.items())]) for sc in DEFAULT_SCORING_CRITERIA
    ]
    data["Weighting_Factors"] = [
        OrderedDict([("ID", make_id()), *list(wf.items())]) for wf in DEFAULT_WEIGHTING_FACTORS
    ]
    data["Parking_Lot"] = [OrderedDict([
        ("ID", EMPTY_SENTINEL),
        ("Parking_Lot_Issue", ""),
        ("Response", ""),
        ("Responsible_Party", ""),
        ("Start_Date", ""),
        ("End_Date", ""),
    ])]
    data["Drawings"] = [OrderedDict([
        ("ID", EMPTY_SENTINEL),
        ("Drawing", ""),
        ("Revision", ""),
        ("Document_Type", ""),
        ("Drawing_Description", ""),
        ("Link", ""),
    ])]
    data["Evidences"] = [OrderedDict([("ID", EMPTY_SENTINEL), ("Evidence", "")])]
    return data


def _default_visibility():
    """A default Column_Visibility tree with everything visible."""
    cv = OrderedDict()
    for top in ["Overview", "Settings", "Team_Members", "Sessions",
                "Team_Members_Sessions", "Revalidation_History", "Categories",
                "Assessor_Recommendations", "Scoring_Criterias",
                "Weighting_Factors", "Parking_Lot", "Drawings", "Evidences"]:
        cv[top] = True
    cv["Overview_Children"] = OrderedDict([
        ("Study_Name", True), ("Study_Coordinator", True),
        ("Study_Coordinator_Contact_Info", True), ("Facility", True),
        ("Facility_Location", True), ("Facility_Owner", True), ("Unit", True),
        ("Report_Number", True), ("Project_Number", True),
        ("Project_Description", True), ("General_Notes", True),
    ])
    cv["Team_Members_Children"] = OrderedDict([
        ("Name", True), ("Company", True), ("Title", True), ("Department", True),
        ("Expertise", True), ("Experience", True), ("Phone_Number", True),
        ("E__Mail_Address", True), ("Team_Member_Comments", True),
    ])
    cv["Sessions_Children"] = OrderedDict([
        ("Date", True), ("Duration", True), ("Session", True),
        ("Assessor_ID", True), ("Session_Comments", True),
    ])
    cv["Categories_Children"] = OrderedDict([
        ("Category_Description", True), ("Completed_Questions", True),
        ("Total_Questions", True), ("Percent_Complete", True),
        ("Average_Score", True), ("Average_Weighted_Score", True),
        ("Session_IDs", True), ("Element", True),
        ("Elements_Children", OrderedDict([
            ("Element_Description", True), ("Questions", True),
            ("Questions_Children", OrderedDict([
                ("Question_Description", True), ("Assessor_Notes", True),
                ("Assessor_Guidance", True), ("Scoring_Criteria_ID", True),
                ("Selected_Score", True), ("Weighting_Factor_ID", True),
                ("Selected_Weighting_Factor", True), ("Weighted_Score", True),
                ("Evidence_IDs", True), ("Assessor_Recommendation_IDs", True),
                ("Reference_Documents", True),
                ("Reference_Documents_Children", OrderedDict([
                    ("Reference_Document_Description", True), ("Clauses", True),
                ])),
                ("Findings", True),
            ])),
            ("Elements", True),
        ])),
    ])
    cv["Assessor_Recommendations_Children"] = OrderedDict([
        ("Assessor_Recommendation", True),
        ("Assessor_Recommendation_Priority", True),
        ("Assessor_Recommendation_Responsible_Party", True),
        ("Assessor_Recommendation_Status", True),
        ("Assessor_Recommendation_Comments", True),
    ])
    cv["Scoring_Criterias_Children"] = OrderedDict([
        ("Scoring_Criteria_Description", True), ("Scoring_Criteria_Level", True),
        ("Scoring_Criteria_Assessor_Notes", True),
    ])
    cv["Weighting_Factors_Children"] = OrderedDict([
        ("Weighting_Factor_Code", True), ("Weighting_Factor_Description", True),
        ("Weighting_Factor_Score", True),
    ])
    cv["Parking_Lot_Children"] = OrderedDict([
        ("Parking_Lot_Issue", True), ("Response", True),
        ("Responsible_Party", True), ("Start_Date", True), ("End_Date", True),
    ])
    cv["Drawings_Children"] = OrderedDict([
        ("Drawing", True), ("Revision", True), ("Document_Type", True),
        ("Drawing_Description", True), ("Link", True),
    ])
    cv["Evidences_Children"] = OrderedDict([("Evidence", True)])
    cv["Revalidation_History_Children"] = OrderedDict([
        ("Start_Date", True), ("End_Date", True), ("Revalidation_Comments", True),
    ])
    return cv


# --------------------------------- CLI ------------------------------------- #

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    p_val = sub.add_parser("validate", help="Validate an existing .aud file")
    p_val.add_argument("path")
    p_rt = sub.add_parser("round-trip", help="Load + save unchanged; expect byte equality")
    p_rt.add_argument("path")
    args = p.parse_args(argv)

    if args.cmd == "validate":
        with open(args.path, "r", encoding="utf-8") as f:
            data = json.loads(f.read())
        issues = validate(data)
        if not issues:
            print("OK — no issues.")
            return
        for i in issues:
            print(f"[{i['severity'].upper():7}] {i['path']}: {i['message']}")
        if any(i["severity"] == "error" for i in issues):
            sys.exit(1)

    elif args.cmd == "round-trip":
        with open(args.path, "rb") as f:
            original = f.read()
        data = json.loads(original.decode("utf-8"))
        out_text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        out_bytes = out_text.encode("utf-8")
        if out_bytes == original:
            print(f"OK — byte-identical round-trip ({len(original)} bytes).")
        else:
            print(f"DIFF — original {len(original)} bytes, reserialized {len(out_bytes)} bytes.")
            for i in range(min(len(original), len(out_bytes))):
                if original[i] != out_bytes[i]:
                    ctx_a = original[max(0, i-30):i+50].decode("utf-8", errors="replace")
                    ctx_b = out_bytes[max(0, i-30):i+50].decode("utf-8", errors="replace")
                    print(f"  first diff at byte {i}")
                    print(f"  orig: ...{ctx_a}...")
                    print(f"  reser: ...{ctx_b}...")
                    break
            sys.exit(1)


if __name__ == "__main__":
    main()
