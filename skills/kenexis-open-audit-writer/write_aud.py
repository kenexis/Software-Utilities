#!/usr/bin/env python3
"""
write_aud.py — Kenexis Open-Audit (.aud) writer

Save modifications to a .aud file in a way that survives Open-Audit's loader.
Preserves the format's quirks: string-literal sentinels ("null", "empty"),
pre-encoded HTML entities, intentional whitespace, string-encoded protocol
numbers, key insertion order, and orphan records.

New studies start from aud_template.json, the application's own empty study.

Scores are derived. The application calculates them in the browser, keeps the
results in the file, and recalculates a question only when its row is edited,
so a score written any other way is wrong until then. score_question() is the
way to score, and the add_* helpers keep their category's totals current.

Use the helper functions for edits — they encode the safety rules. Direct
mutation of the parsed data dict is allowed but at your own risk; run
validate() before save() either way.

CLI usage (limited — most use is programmatic):
    python3 write_aud.py validate FILE.aud
    python3 write_aud.py round-trip FILE.aud   # load + save with no changes; expects byte equality
"""
import argparse
import copy
import json
import math
import os
import re
import secrets
import string
import sys
from collections import OrderedDict
from pathlib import Path


# --------------------------- Constants & sentinels ------------------------- #

NULL_SENTINEL = "null"
EMPTY_SENTINEL = "empty"

HERE = Path(__file__).resolve().parent
TEMPLATE_PATH = HERE / "aud_template.json"

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

# Worked out from the selected criterion and factor; never set directly.
SCORE_INPUT_FIELDS = ("Scoring_Criteria_ID", "Weighting_Factor_ID")
DERIVED_QUESTION_FIELDS = ("Selected_Score", "Selected_Weighting_Factor", "Weighted_Score")
CATEGORY_ROLLUPS = (
    "Completed_Questions", "Total_Questions", "Percent_Complete",
    "Average_Score", "Average_Weighted_Score",
)

# Free text the application stores entity-encoded.
FREE_TEXT_FIELDS = {
    "Findings", "Assessor_Notes", "Assessor_Guidance",
    "Question_Description", "Element_Description", "Category_Description",
    "Assessor_Recommendation", "Assessor_Recommendation_Comments",
    "Assessor_Recommendation_Responsible_Party",
    "Parking_Lot_Issue", "Response", "Responsible_Party", "Drawing_Description",
    "General_Notes", "Project_Description", "Session_Comments",
    "Revalidation_Comments", "Team_Member_Comments", "Evidence",
    "Study_Name", "Study_Coordinator", "Study_Coordinator_Contact_Info",
    "Facility", "Facility_Location", "Facility_Owner", "Unit",
    "Report_Number", "Project_Number",
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

# The category totals and these Settings trees exist from these revisions on.
MIN_REV_ROLLUPS = 5
MIN_REV_COLUMN_HEADERS = 7


# ------------------------------- Template ---------------------------------- #

_TEMPLATE = None


def _template():
    global _TEMPLATE
    if _TEMPLATE is None:
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
            _TEMPLATE = json.loads(f.read(), object_pairs_hook=OrderedDict)
    return _TEMPLATE


def current_ds_rev():
    """The schema revision of the shipped template: what new_audit() writes."""
    return _template()["dsRev"]


def _blank(kind):
    """A private copy of the application's blank record of the given kind."""
    doc = _template()["document"]
    category = doc["Categories"][0]
    element = category["Elements"][0]
    return copy.deepcopy({
        "category": category,
        "element": element,
        "question": element["Questions"][0],
        "reference_document": element["Questions"][0]["Reference_Documents"][0],
        "clause": element["Questions"][0]["Reference_Documents"][0]["Clauses"][0],
    }[kind])


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
    """Encode free text the way the application does: undo its three entities,
    then escape & < > again. Text already encoded comes out unchanged, and a raw
    '&' beside an encoded one is still escaped."""
    if not isinstance(text, str):
        return text
    decoded = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return decoded.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ------------------------------- Scoring ----------------------------------- #
# A port of the application's KxsCalc.js, including its loose numeric test. Keep
# the two in step: scores here must match what a row edit in the application
# would produce.

_FLOAT_PREFIX = re.compile(r"^\s*[+-]?(?:\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?|Infinity)")


def _is_numeric(value):
    """JavaScript's !isNaN(parseFloat(value))."""
    return value is not None and _FLOAT_PREFIX.match(str(value)) is not None


def _number(value):
    """JavaScript's unary plus on a string."""
    text = str(value).strip()
    if text == "":
        return 0.0
    try:
        return float(text)
    except ValueError:
        return math.nan


def _js_string(number):
    """JavaScript's number-to-string for the values scoring produces."""
    if math.isnan(number):
        return "NaN"
    if number == int(number) and abs(number) < 1e21:
        return str(int(number))
    return repr(number)


def _js_round(number):
    """JavaScript's Math.round, which rounds halves up, not to even, and passes
    NaN through."""
    return number if math.isnan(number) else math.floor(number + 0.5)


def _calc_question(data, question):
    criterion_id = question.get("Scoring_Criteria_ID")
    factor_id = question.get("Weighting_Factor_ID")
    score = weight = None
    selected_score = selected_factor = ""
    if criterion_id:
        score = [c.get("Scoring_Criteria_Level") for c in data.get("Scoring_Criterias", [])
                 if c.get("ID") == criterion_id]
        if score:
            selected_score = score[0]
    if factor_id:
        weight = [f.get("Weighting_Factor_Score") for f in data.get("Weighting_Factors", [])
                  if f.get("ID") == factor_id]
        if weight:
            selected_factor = weight[0]

    weighted = ""
    if score:
        if not _is_numeric(score[0]):
            weighted = "NaN"
        elif weight is None:
            weighted = _js_string(_number(score[0]))
        elif weight:
            weighted = _js_string(_number(score[0]) * _number(weight[0])) if _is_numeric(weight[0]) else "NaN"

    question["Selected_Score"] = selected_score
    question["Selected_Weighting_Factor"] = selected_factor
    question["Weighted_Score"] = weighted


def _column_visible(node, key):
    """Whether a column is on in Column_Visibility. As in the application, the
    last match in the tree wins."""
    found = None
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                found = v
            elif isinstance(v, dict):
                inner = _column_visible(v, key)
                if inner is not None:
                    found = inner
    return found


def _average(values):
    if not values:
        return "NaN"
    return _js_string(_js_round(sum(_number(v) for v in values) / len(values)))


def _category_totals(data, category):
    questions = [q for e in category.get("Elements", []) for q in e.get("Questions", [])]
    scores = [q.get("Selected_Score") for q in questions if _is_numeric(q.get("Selected_Score"))]
    weighted = [q.get("Weighted_Score") for q in questions if _is_numeric(q.get("Weighted_Score"))]
    using_weighting = _column_visible(data.get("Settings", {}).get("Column_Visibility"), "Weighting_Factor_ID")
    completed = len(weighted) if using_weighting else len(scores)
    total = len(questions)
    return OrderedDict([
        ("Completed_Questions", str(completed)),
        ("Total_Questions", str(total)),
        ("Percent_Complete", f"{_js_round(completed / total * 100)}%" if total else "NaN"),
        ("Average_Score", _average(scores)),
        ("Average_Weighted_Score", _average(weighted)),
    ])


def _update_category(data, category):
    category.update(_category_totals(data, category))


def _pool_id(data, collection, label_key, value, what):
    """Accept a record's ID or its label, and return the ID. '' stays ''."""
    if not value:
        return ""
    rows = [r for r in data.get(collection, []) if r.get("ID") != EMPTY_SENTINEL]
    for r in rows:
        if r.get("ID") == value:
            return value
    wanted = _encode_html(str(value)).casefold()
    for r in rows:
        if str(r.get(label_key, "")).casefold() == wanted:
            return r["ID"]
    raise ValueError(f"No {what} {value!r}. Choose one of: {[r.get(label_key) for r in rows]}")


def score_question(data, question_id, scoring_criterion=None, weighting_factor=None):
    """Score a question: the only way to do it. Pass a scoring criterion and/or
    weighting factor by label ('Compliant', 'Critical') or ID; '' clears one and
    None leaves it alone. Sets the IDs and everything derived from them, and
    updates the category's totals. Returns the question."""
    question, category = _find_question_and_category(data, question_id)
    if scoring_criterion is not None:
        question["Scoring_Criteria_ID"] = _pool_id(
            data, "Scoring_Criterias", "Scoring_Criteria_Description", scoring_criterion, "scoring criterion")
    if weighting_factor is not None:
        question["Weighting_Factor_ID"] = _pool_id(
            data, "Weighting_Factors", "Weighting_Factor_Code", weighting_factor, "weighting factor")
    _calc_question(data, question)
    _update_category(data, category)
    return question


def recalculate(data):
    """Recalculate every question and category. Use after changing a scoring
    level or weighting score, or when validate() reports a stale score."""
    for category in data.get("Categories", []):
        for element in category.get("Elements", []):
            for question in element.get("Questions", []):
                _calc_question(data, question)
        _update_category(data, category)


# ------------------------------ Set field ---------------------------------- #

def set_field(record, key, value):
    """Set a field on a record, enforcing the format's conventions.

    - Refuses scores and category totals: use score_question().
    - For STRING_NUMERIC_FIELDS, raises if value is not a string.
    - For KNOWN_NULL_SENTINEL_FIELDS, allows None to mean the "null" sentinel.
    - Applies HTML escaping to free-text fields.
    - Preserves whitespace verbatim.
    """
    if key in SCORE_INPUT_FIELDS or key in DERIVED_QUESTION_FIELDS or key in CATEGORY_ROLLUPS:
        raise ValueError(
            f"{key} is part of the score. Use score_question(data, question_id, ...), "
            "which keeps the score, its category and the dashboard in step."
        )
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

    if key in FREE_TEXT_FIELDS and isinstance(value, str):
        value = _encode_html(value)

    record[key] = value


# ------------------------ Find / list helpers ------------------------------ #

def _find_question_and_category(data, question_id):
    for cat in data.get("Categories", []):
        for el in cat.get("Elements", []):
            for q in el.get("Questions", []):
                if q.get("ID") == question_id:
                    return q, cat
    raise KeyError(f"Question {question_id!r} not found")


def find_question(data, question_id):
    """Locate a question record by ID. Returns the dict in place (mutations
    affect the underlying data)."""
    return _find_question_and_category(data, question_id)[0]


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

def _append(lst, record):
    """Add a record. The first real record takes the place of a lone "empty"
    seed row, as it does when a user fills in that row in the application."""
    if len(lst) == 1 and lst[0].get("ID") == EMPTY_SENTINEL:
        lst.clear()
    lst.append(record)
    return record


def _link(lst, record_id):
    """Add to a link list. A list holds {"ID":"empty"} only while it is empty."""
    lst[:] = [r for r in lst if r.get("ID") != EMPTY_SENTINEL]
    if not any(r.get("ID") == record_id for r in lst):
        lst.append(OrderedDict([("ID", record_id)]))


def add_team_member(data, name, company="", title="", department="",
                    expertise="", experience="", phone_number="",
                    email="", comments=""):
    """Append a team member record. Returns the new ID."""
    tm_id = make_id()
    record = OrderedDict([
        ("ID", tm_id),
        ("Name", _encode_html(name)),
        ("Company", _encode_html(company)),
        ("Title", _encode_html(title)),
        ("Department", _encode_html(department)),
        ("Expertise", _encode_html(expertise)),
        ("Experience", _encode_html(experience)),
        ("Phone_Number", phone_number),
        ("E__Mail_Address", email),
        ("Team_Member_Comments", _encode_html(comments)),
    ])
    _append(data.setdefault("Team_Members", []), record)
    return tm_id


def add_session(data, session_label, date="", duration="", assessor_id=None, comments=""):
    """Append a session record. Returns the new ID."""
    s_id = make_id()
    record = OrderedDict([
        ("ID", s_id),
        ("Date", date),
        ("Duration", duration),
        ("Session", _encode_html(session_label)),
        ("Assessor_ID", assessor_id if assessor_id else ""),
        ("Session_Comments", _encode_html(comments)),
    ])
    _append(data.setdefault("Sessions", []), record)
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
    _append(data.setdefault("Evidences", []), record)
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
        ("Assessor_Recommendation_Responsible_Party", _encode_html(responsible_party)),
        ("Assessor_Recommendation_Status", NULL_SENTINEL if status is None else status),
        ("Assessor_Recommendation_Comments", _encode_html(comments)),
    ])
    _append(data.setdefault("Assessor_Recommendations", []), record)
    return rec_id


def link_evidence_to_question(data, question_id, evidence_id):
    _link(find_question(data, question_id).setdefault("Evidence_IDs", []), evidence_id)


def link_recommendation_to_question(data, question_id, recommendation_id):
    _link(find_question(data, question_id).setdefault("Assessor_Recommendation_IDs", []), recommendation_id)


def add_category(data, description, session_ids=None):
    """Add a category, with a blank element ready for add_element(). Returns the
    category record."""
    category = _blank("category")
    category["ID"] = make_id()
    category["Category_Description"] = _encode_html(description)
    if session_ids:
        category["Session_IDs"] = [OrderedDict([("ID", s)]) for s in session_ids]
    _append(data.setdefault("Categories", []), category)
    _update_category(data, category)
    return category


def add_element(data, category_id, description):
    """Add an element to a category. Returns the element record."""
    category = find_category(data, category_id)
    element = _blank("element")
    element["ID"] = make_id()
    element["Element_Description"] = _encode_html(description)
    _append(category["Elements"], element)
    _update_category(data, category)
    return element


def add_question(data, category_id, element_id, question_text,
                 assessor_guidance="", assessor_notes="",
                 scoring_criterion_id=None, weighting_factor_id=None,
                 reference_documents=None):
    """Append a fully-populated Question record to an element. Returns ID.
    scoring_criterion_id and weighting_factor_id take an ID or a label."""
    el = find_element(data, category_id, element_id)
    q_id = make_id()
    refs = []
    for rd in (reference_documents or []):
        doc = _blank("reference_document")
        doc["ID"] = make_id()
        doc["Reference_Document_Description"] = _encode_html(rd.get("name", ""))
        if rd.get("clauses"):
            doc["Clauses"] = []
            for c in rd["clauses"]:
                clause = _blank("clause")
                clause["ID"] = make_id()
                clause["Clause_Description"] = _encode_html(c)
                doc["Clauses"].append(clause)
        refs.append(doc)

    record = _blank("question")
    record["ID"] = q_id
    record["Question_Description"] = _encode_html(question_text)
    record["Assessor_Notes"] = _encode_html(assessor_notes)
    record["Assessor_Guidance"] = _encode_html(assessor_guidance)
    if refs:
        record["Reference_Documents"] = refs
    _append(el.setdefault("Questions", []), record)
    score_question(data, q_id, scoring_criterion_id, weighting_factor_id)
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

def _is_unset(value):
    return value in (None, "", NULL_SENTINEL, EMPTY_SENTINEL)


def _validate_settings(settings, rev, add):
    if not isinstance(settings.get("Ds_Rev"), int) or isinstance(settings.get("Ds_Rev"), bool):
        add("error", "Settings.Ds_Rev must be an integer", "Settings.Ds_Rev")
        return
    if rev > current_ds_rev():
        add("error", f"Ds_Rev {rev} is newer than this writer's {current_ds_rev()}", "Settings.Ds_Rev")
    elif rev < current_ds_rev():
        add("warning", f"File is at revision {rev}; open and save it in Open-Audit to upgrade it "
                       f"to {current_ds_rev()}", "Settings.Ds_Rev")
    if settings.get("Encrypt") is True:
        add("error", "Encrypted .aud files are not supported", "Settings.Encrypt")

    if rev >= MIN_REV_ROLLUPS:
        tree = settings.get("Column_Visibility")
        path = ("Categories_Children", "Elements_Children", "Questions_Children")
        node = tree
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if not isinstance(node, dict) or not node:
            add("error", "Column_Visibility has no question columns; start from new_audit() "
                         "and do not stub Settings", "Settings.Column_Visibility")
    if rev >= MIN_REV_COLUMN_HEADERS and not isinstance(settings.get("Column_Headers"), dict):
        add("error", f"Column_Headers is required at revision {MIN_REV_COLUMN_HEADERS} and later, "
                     "and is not rebuilt on open; start from new_audit()", "Settings.Column_Headers")


def _validate_links(lst, pool, label, path, add):
    if not isinstance(lst, list):
        add("error", f"{label} must be a list", path)
        return
    ids = [r.get("ID") if isinstance(r, dict) else r for r in lst]
    if any(not isinstance(r, dict) for r in lst):
        add("error", f"{label} entries must be {{'ID': ...}} objects", path)
    if EMPTY_SENTINEL in ids and len(ids) > 1:
        add("warning", f"{label} holds an 'empty' entry beside real ones", path)
    for rid in ids:
        if rid != EMPTY_SENTINEL and rid not in pool:
            add("warning", f"{label} reference {rid!r} not found", path)


def _validate_question(data, q, path, add):
    for key in _blank("question"):
        if key not in q:
            add("error", f"Question is missing {key}; build questions with add_question()", f"{path}.{key}")

    criterion = q.get("Scoring_Criteria_ID")
    score = q.get("Selected_Score")
    if _is_unset(criterion) and not _is_unset(score):
        add("error", "Question has a Selected_Score but no Scoring_Criteria_ID, so the dashboard "
                     "misses it and a row edit erases it; use score_question()", path)
    if any(c.get("ID") == criterion for c in data.get("Scoring_Criterias", [])) and criterion != EMPTY_SENTINEL:
        expected = copy.copy(q)
        _calc_question(data, expected)
        if _is_unset(score):
            add("error", "Question has a Scoring_Criteria_ID but no Selected_Score, so reports show "
                         "no score; use score_question()", path)
        elif any(expected.get(k) != q.get(k) for k in DERIVED_QUESTION_FIELDS):
            add("warning", "Stored score differs from its criterion and factor; run recalculate(data)", path)
    elif not _is_unset(criterion):
        add("warning", f"Scoring criterion {criterion!r} not found", path)

    factor = q.get("Weighting_Factor_ID")
    if not _is_unset(factor) and not any(f.get("ID") == factor for f in data.get("Weighting_Factors", [])):
        add("warning", f"Weighting factor {factor!r} not found", path)

    evidence = {r.get("ID") for r in data.get("Evidences", [])}
    recommendations = {r.get("ID") for r in data.get("Assessor_Recommendations", [])}
    _validate_links(q.get("Evidence_IDs", []), evidence, "Evidence_IDs", f"{path}.Evidence_IDs", add)
    _validate_links(q.get("Assessor_Recommendation_IDs", []), recommendations,
                    "Assessor_Recommendation_IDs", f"{path}.Assessor_Recommendation_IDs", add)


def _validate_protocol(data, rev, add):
    for ci, cat in enumerate(data.get("Categories", [])):
        cp = f"Categories[{ci}]"
        if rev >= MIN_REV_ROLLUPS:
            for key in CATEGORY_ROLLUPS:
                if key not in cat:
                    add("error", f"Category is missing {key}; build categories with add_category()",
                        f"{cp}.{key}")
        elements = cat.get("Elements")
        if not isinstance(elements, list):
            add("error", "Category has no Elements list", cp)
            continue
        if not elements:
            add("warning", "Category has no elements", cp)
        for ei, el in enumerate(elements):
            ep = f"{cp}.Elements[{ei}]"
            questions = el.get("Questions")
            if not isinstance(questions, list):
                add("error", "Element has no Questions list", ep)
                continue
            if not questions:
                add("warning", "Element has no questions", ep)
            for qi, q in enumerate(questions):
                _validate_question(data, q, f"{ep}.Questions[{qi}]", add)
        if rev >= MIN_REV_ROLLUPS and all(k in cat for k in CATEGORY_ROLLUPS):
            expected = _category_totals(data, cat)
            if any(cat.get(k) != v for k, v in expected.items()):
                add("warning", "Stored category totals are out of date; run recalculate(data)", cp)


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
    for k in data:
        if k not in TOP_LEVEL_KEYS:
            add("error", f"Unknown top-level key {k!r}", k)

    # Settings checks
    settings = data.get("Settings", {})
    rev = settings.get("Ds_Rev") if isinstance(settings.get("Ds_Rev"), int) else 0
    _validate_settings(settings, rev, add)

    # Protocol tree: required fields, scores, links
    _validate_protocol(data, rev, add)
    if not [r for r in data.get("Scoring_Criterias", []) if r.get("ID") != EMPTY_SENTINEL]:
        add("warning", "No scoring criteria, so no question can be scored", "Scoring_Criterias")

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

def dumps(data):
    """Serialize to the exact on-disk wire form (compact, single line)."""
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


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

    text = dumps(data)
    p = Path(path)
    # Write atomically: write to a temp path then rename.
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, p)
    return str(p)


# -------------------------- Round-trip verification ----------------------- #

def verify_round_trip(saved_path, in_memory_data):
    """After save(), confirm the saved file reparses to a structure equal to the
    in-memory data. Returns {ok, byte_identical, mismatches, file_size}."""
    with open(saved_path, "r", encoding="utf-8") as f:
        text = f.read()
    parsed = json.loads(text)
    mismatches = _diff(in_memory_data, parsed, path="")
    return {
        "ok": not mismatches,
        "byte_identical": dumps(in_memory_data) == text,
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
              unit="", coordinator_name="", coordinator_email="", general_notes=""):
    """Construct a fresh .aud document from the application's empty study, at
    the current revision, with its standard scoring and weighting tables."""
    data = copy.deepcopy(_template()["document"])
    for key in ("Scoring_Criterias", "Weighting_Factors"):
        for row in data[key]:
            row["ID"] = make_id()
    overview = data["Overview"]
    for key, value in (("Study_Name", study_name), ("Study_Coordinator", coordinator_name),
                       ("Study_Coordinator_Contact_Info", coordinator_email), ("Facility", facility),
                       ("Facility_Location", facility_location), ("Facility_Owner", facility_owner),
                       ("Unit", unit), ("Report_Number", report_number), ("Project_Number", project_number),
                       ("Project_Description", project_description), ("General_Notes", general_notes)):
        overview[key] = _encode_html(value)
    return data


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
        out_text = dumps(data)
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
