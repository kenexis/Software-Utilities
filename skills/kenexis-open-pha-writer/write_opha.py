#!/usr/bin/env python3
"""
write_opha.py — Kenexis Open-PHA (.opha) writer.

Creates and modifies Open-PHA study files with byte-perfect round-trip
fidelity. Encodes every field with the correct sentinel per the format
review (open-pha-format-review.md):

  * compact single-line JSON, separators=(',',':'), ensure_ascii=False
  * key insertion order preserved
  * string sentinels "null" / "empty" and empty-string "" kept distinct
  * REAL JSON null on the severity family (see JSON_NULL_FIELDS)
  * numbers stored verbatim as strings (incl. scientific notation)
  * reference lists never empty ([{"ID":"empty"}] when unlinked)
  * base36 IDs, 22 chars

Two companion data files live next to this module and are required:
  * pha_template.opha   — a blank study (Settings + Risk_Criteria scaffold)
  * pha_skeletons.json  — canonical field skeleton for every record type

Primary, fully-reliable workflow: load() an existing file -> mutate with the
add_* helpers -> save(). An unmodified load->save is byte-identical.

new_pha() builds a study from the embedded template. Because that template was
derived from a single populated sample (not a fresh export from the desktop
tool), treat from-scratch files as PROVISIONAL until confirmed to open in the
Open-PHA application. See open-pha-format-review.md §12 and §14.
"""
import json
import copy
import random
import string
from collections import OrderedDict
from pathlib import Path

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

NULL_SENTINEL = "null"     # string "no value selected"
EMPTY_SENTINEL = "empty"   # string "no record / no reference"

_ID_ALPHABET = string.digits + string.ascii_lowercase   # base36 [0-9a-z]
_ID_LEN = 22

HERE = Path(__file__).resolve().parent
TEMPLATE_PATH = HERE / "pha_template.opha"
SKELETON_PATH = HERE / "pha_skeletons.json"

# The 21 top-level keys, in canonical insertion order.
TOP_LEVEL_ORDER = [
    "Overview", "Settings", "Team_Members", "Sessions", "Team_Members_Sessions",
    "Revalidation_History", "Nodes", "Safeguards", "Pha_Recommendations",
    "Pha_Comments", "Lopa_Recommendations", "Lopa_Comments", "Parking_Lot",
    "Drawings", "Risk_Criteria", "Check_Lists", "Check_List_Recommendations",
    "Mocs", "Previous_Incidents", "Industry_Incidents", "Scais",
]

# Fields whose UNSET marker is real JSON null (NOT the string "null").
JSON_NULL_FIELDS = {
    "Consequence_Severity_ID",
    "Consequence_Severity_ID_Before_Safeguards",
    "Consequence_Severity_ID_After_Recommendations",
    "Severity_ID",               # only inside Risk_Criteria.Consequence_Intersections
    "Safeguard_Library_Version",
}

# Collections that keep a single "empty" seed record while unpopulated.
SEED_COLLECTIONS = {
    "Revalidation_History", "Parking_Lot", "Mocs",
    "Previous_Incidents", "Industry_Incidents",
}

# Reference-list field names (wrapped [{"ID": ...}] lists).
_REF_LIST_SUFFIX = "_IDs"


# --------------------------------------------------------------------------- #
# Low-level IO
# --------------------------------------------------------------------------- #

def make_id(n=_ID_LEN):
    """Generate a fresh opaque base36 ID matching the format's shape."""
    return "".join(random.choice(_ID_ALPHABET) for _ in range(n))


def dumps(data):
    """Serialize to the exact on-disk wire form (compact, single line)."""
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def load(path):
    """Load an .opha file into an ordered dict, preserving everything."""
    with open(path, "r", encoding="utf-8") as f:
        return json.loads(f.read(), object_pairs_hook=OrderedDict)


def save(data, path, validate_first=True):
    """Write an .opha file as compact single-line JSON (no trailing newline).

    If validate_first, raises ValueError on any validation error."""
    if validate_first:
        issues = validate(data)
        errs = [i for i in issues if i["severity"] == "error"]
        if errs:
            msg = "; ".join(f"{e['path']}: {e['message']}" for e in errs[:10])
            raise ValueError(f"Refusing to save — {len(errs)} validation error(s): {msg}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(data))


def verify_round_trip(path, data=None):
    """Read a saved file back and confirm compact-JSON round-trip stability.

    Returns {ok, file_size, matches_data}. ok=True means re-serializing the
    file's own parse is byte-identical to the file (the format invariant).
    matches_data compares against an in-memory dict when supplied."""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    reparsed = json.loads(raw, object_pairs_hook=OrderedDict)
    result = {"ok": dumps(reparsed) == raw,
              "file_size": len(raw.encode("utf-8"))}
    if data is not None:
        result["matches_data"] = (dumps(data) == raw)
    return result


# --------------------------------------------------------------------------- #
# Skeletons & template
# --------------------------------------------------------------------------- #

_SKELETONS = None


def _skeletons():
    global _SKELETONS
    if _SKELETONS is None:
        with open(SKELETON_PATH, "r", encoding="utf-8") as f:
            _SKELETONS = json.loads(f.read(), object_pairs_hook=OrderedDict)
    return _SKELETONS


def new_record(record_type, assign_id=True, **fields):
    """Build a canonical record of the given type from its skeleton.

    Every field is present in canonical order with the correct unset marker.
    Provided **fields override defaults. ID is auto-generated unless
    assign_id=False or an 'ID' override is supplied.

    record_type is a skeleton key, e.g. 'Safeguards', 'Nodes', 'Deviations',
    'Consequences', 'Check_List_Questions', 'RC:Severities'.
    """
    skels = _skeletons()
    if record_type not in skels:
        raise KeyError(f"Unknown record type {record_type!r}. "
                       f"Known: {list(skels)}")
    rec = copy.deepcopy(skels[record_type])
    if assign_id and "ID" in rec and "ID" not in fields:
        rec["ID"] = make_id()
    for k, v in fields.items():
        if k not in rec:
            raise KeyError(f"Field {k!r} not in {record_type} skeleton")
        rec[k] = v
    return rec


def empty_ref():
    """The canonical 'nothing linked' reference list."""
    return [OrderedDict([("ID", EMPTY_SENTINEL)])]


def ref_list(ids):
    """Wrap a list of IDs into the [{"ID": ...}] form. Empty -> empty_ref()."""
    ids = [i for i in (ids or []) if i and i != EMPTY_SENTINEL]
    if not ids:
        return empty_ref()
    return [OrderedDict([("ID", i)]) for i in ids]


def new_pha(study_name="", template_path=None, **overview):
    """Create a new study dict from the blank template.

    overview kwargs (e.g. facility='...', project_number='...') are written to
    matching Overview fields when present. Study_Name defaults to study_name.
    """
    data = load(template_path or TEMPLATE_PATH)
    if "Study_Name" in data["Overview"]:
        data["Overview"]["Study_Name"] = study_name
    # Resolve kwargs against Overview field names case-insensitively, so
    # facility=... maps to "Facility", project_number=... to "Project_Number".
    lut = {k.lower(): k for k in data["Overview"]}
    for k, v in overview.items():
        real = lut.get(k.lower())
        if real is None:
            raise KeyError(f"{k!r} is not an Overview field")
        data["Overview"][real] = v
    return data


# --------------------------------------------------------------------------- #
# Collection helpers (append a record, dropping a lone "empty" seed)
# --------------------------------------------------------------------------- #

def _append(data, collection, record):
    """Append record to a top-level collection, replacing a lone empty seed."""
    lst = data.setdefault(collection, [])
    if len(lst) == 1 and lst[0].get("ID") == EMPTY_SENTINEL:
        # collection was in unpopulated seed state -> replace the seed
        lst.clear()
    lst.append(record)
    return record


def add_safeguard(data, safeguard, safeguard_type="", ipl_tag="",
                  is_safeguard=True, is_ipl=False, pfd="", selected_sil=None,
                  **extra):
    rec = new_record("Safeguards", Safeguard=safeguard,
                     Safeguard_Type=safeguard_type, Ipl_Tag=ipl_tag,
                     Is_Safeguard=_bool_str(is_safeguard),
                     Is_Ipl=_bool_str(is_ipl), Pfd=str(pfd) if pfd != "" else "",
                     **extra)
    if selected_sil is not None:
        rec["Selected_Sil"] = selected_sil
    _append(data, "Safeguards", rec)
    return rec["ID"]


def add_pha_recommendation(data, text, priority=None, responsible_party="",
                           status=None, due_date="", comments=""):
    rec = new_record("Pha_Recommendations", Pha_Recommendation=text,
                     Pha_Recommendation_Responsible_Party=responsible_party,
                     Pha_Recommendation_Due_Date=due_date,
                     Pha_Recommendation_Comments=comments)
    if priority is not None:
        rec["Pha_Recommendation_Priority"] = priority
    if status is not None:
        rec["Pha_Recommendation_Status"] = status
    _append(data, "Pha_Recommendations", rec)
    return rec["ID"]


def add_lopa_recommendation(data, text, **extra):
    rec = new_record("Lopa_Recommendations", Lopa_Recommendation=text, **extra)
    _append(data, "Lopa_Recommendations", rec)
    return rec["ID"]


def add_pha_comment(data, text):
    rec = new_record("Pha_Comments", Pha_Comment=text)
    _append(data, "Pha_Comments", rec)
    return rec["ID"]


def add_lopa_comment(data, text):
    rec = new_record("Lopa_Comments", Lopa_Comment=text)
    _append(data, "Lopa_Comments", rec)
    return rec["ID"]


def add_drawing(data, drawing, revision="", document_type="",
                description="", link=""):
    rec = new_record("Drawings", Drawing=drawing, Revision=revision,
                     Document_Type=document_type, Drawing_Description=description,
                     Link=link)
    _append(data, "Drawings", rec)
    return rec["ID"]


def add_team_member(data, name, company="", title="", **extra):
    rec = new_record("Team_Members", Name=name, Company=company, Title=title,
                     **extra)
    _append(data, "Team_Members", rec)
    return rec["ID"]


def add_session(data, session, date="", facilitator_id=None, scribe_id=None,
                **extra):
    rec = new_record("Sessions", Session=session, Date=date, **extra)
    if facilitator_id:
        rec["Facilitator_ID"] = facilitator_id
    if scribe_id:
        rec["Scribe_ID"] = scribe_id
    _append(data, "Sessions", rec)
    return rec["ID"]


# --------------------------------------------------------------------------- #
# Worksheet tree builders (Nodes -> Deviations -> Causes -> Consequences)
# --------------------------------------------------------------------------- #

def add_node(data, description, session_ids=None, drawing_ids=None, **extra):
    rec = new_record("Nodes", Node_Description=description, **extra)
    rec["Session_IDs"] = ref_list(session_ids)
    rec["Drawing_IDs"] = ref_list(drawing_ids)
    rec["Deviations"] = []
    _append(data, "Nodes", rec)
    return rec


def add_deviation(node, deviation="", guide_word="", parameter="", **extra):
    rec = new_record("Deviations", Deviation=deviation, Guide_Word=guide_word,
                     Parameter=parameter, **extra)
    rec["Causes"] = []
    node["Deviations"].append(rec)
    return rec


def add_cause(deviation, cause="", frequency="", **extra):
    rec = new_record("Causes", Cause=cause,
                     Frequency=str(frequency) if frequency != "" else "",
                     **extra)
    rec["Enabling_Events"] = []
    rec["Consequences"] = []
    deviation["Causes"].append(rec)
    return rec


def add_consequence(cause, consequence="", safeguard_ids=None,
                    pha_recommendation_ids=None, lopa_recommendation_ids=None,
                    pha_comment_ids=None, lopa_comment_ids=None, **extra):
    rec = new_record("Consequences", Consequence=consequence, **extra)
    rec["Safeguard_IDs"] = ref_list(safeguard_ids)
    rec["Pha_Recommendation_IDs"] = ref_list(pha_recommendation_ids)
    rec["Lopa_Recommendation_IDs"] = ref_list(lopa_recommendation_ids)
    rec["Pha_Comment_IDs"] = ref_list(pha_comment_ids)
    rec["Lopa_Comment_IDs"] = ref_list(lopa_comment_ids)
    rec["Alarp_Analysis"] = []
    rec["Conditional_Modifiers"] = []
    cause["Consequences"].append(rec)
    return rec


def add_enabling_event(cause, description="", probability=""):
    rec = new_record("Enabling_Events", EE_Description=description,
                     EE_Probability=str(probability) if probability != "" else "")
    cause["Enabling_Events"].append(rec)
    return rec


def add_conditional_modifier(consequence, description="", probability=""):
    rec = new_record("Conditional_Modifiers", CM_Description=description,
                     CM_Probability=str(probability) if probability != "" else "")
    consequence["Conditional_Modifiers"].append(rec)
    return rec


# --------------------------------------------------------------------------- #
# Reference maintenance
# --------------------------------------------------------------------------- #

def link_safeguards(consequence, safeguard_ids):
    consequence["Safeguard_IDs"] = ref_list(safeguard_ids)


def find_node(data, node_id):
    for n in data.get("Nodes", []):
        if n.get("ID") == node_id:
            return n
    return None


# --------------------------------------------------------------------------- #
# Bool helper
# --------------------------------------------------------------------------- #

def _bool_str(v):
    """Map a Python value to the string tri-state used on data fields."""
    if v is None:
        return NULL_SENTINEL
    if isinstance(v, str):
        return v  # allow passing "true"/"false"/"null" through
    return "true" if v else "false"


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def _issue(sev, path, msg):
    return {"severity": sev, "path": path, "message": msg}


def validate(data):
    """Structural + encoding validation. Returns a list of issue dicts with
    severity 'error' (blocks save) or 'warning' (advisory)."""
    issues = []

    # 1. Top-level keys present and correctly typed.
    for k in TOP_LEVEL_ORDER:
        if k not in data:
            issues.append(_issue("error", k, "missing top-level key"))
    for k in ("Overview", "Settings", "Risk_Criteria"):
        if k in data and not isinstance(data[k], dict):
            issues.append(_issue("error", k, "must be an object"))

    # 2. Settings semantic config.
    s = data.get("Settings", {})
    if not isinstance(s.get("Ds_Rev"), int):
        issues.append(_issue("error", "Settings.Ds_Rev", "must be an integer"))
    for k in ("Analysis_Mode", "Lopa_Mode"):
        if not isinstance(s.get(k), str) or not s.get(k):
            issues.append(_issue("error", f"Settings.{k}", "must be a non-empty string"))
    if s.get("Encrypt") is True:
        issues.append(_issue("error", "Settings.Encrypt",
                             "encrypted files are out of scope"))

    # 3. Collect all record IDs for reference integrity.
    def ids_of(coll):
        return {r.get("ID") for r in data.get(coll, []) if r.get("ID") not in (None, EMPTY_SENTINEL)}
    safeguard_ids = ids_of("Safeguards")
    pha_rec_ids = ids_of("Pha_Recommendations")
    lopa_rec_ids = ids_of("Lopa_Recommendations")

    # 4. Walk the worksheet: reference lists non-empty; refs resolve;
    #    severity family encoded as null/str (never the string "null").
    def check_ref(path, lst, universe, label):
        if not isinstance(lst, list) or len(lst) == 0:
            issues.append(_issue("error", path, "reference list must not be empty "
                                                "(use [{'ID':'empty'}])"))
            return
        for r in lst:
            rid = r.get("ID")
            if rid in (EMPTY_SENTINEL, None):
                continue
            if universe is not None and rid not in universe:
                issues.append(_issue("warning", path,
                                     f"{label} reference {rid!r} not found"))

    for ni, node in enumerate(data.get("Nodes", [])):
        np = f"Nodes[{ni}]"
        check_ref(np + ".Session_IDs", node.get("Session_IDs"), None, "session")
        check_ref(np + ".Drawing_IDs", node.get("Drawing_IDs"), None, "drawing")
        for di, dev in enumerate(node.get("Deviations", [])):
            for ci, cause in enumerate(dev.get("Causes", [])):
                for qi, con in enumerate(cause.get("Consequences", [])):
                    cp = f"{np}.Deviations[{di}].Causes[{ci}].Consequences[{qi}]"
                    check_ref(cp + ".Safeguard_IDs", con.get("Safeguard_IDs"),
                              safeguard_ids, "safeguard")
                    check_ref(cp + ".Pha_Recommendation_IDs",
                              con.get("Pha_Recommendation_IDs"), pha_rec_ids, "PHA rec")
                    check_ref(cp + ".Lopa_Recommendation_IDs",
                              con.get("Lopa_Recommendation_IDs"), lopa_rec_ids, "LOPA rec")
                    for f in ("Consequence_Severity_ID",
                              "Consequence_Severity_ID_Before_Safeguards",
                              "Consequence_Severity_ID_After_Recommendations"):
                        if con.get(f) == NULL_SENTINEL:
                            issues.append(_issue("error", f"{cp}.{f}",
                                "severity field must use JSON null (not the string \"null\")"))

    # 5. Seed collections should carry an empty seed when otherwise empty.
    for coll in SEED_COLLECTIONS:
        lst = data.get(coll, [])
        if len(lst) == 0:
            issues.append(_issue("warning", coll,
                                 "seed collection is empty; expected a single "
                                 "{'ID':'empty'} record"))

    return issues


# --------------------------------------------------------------------------- #
# CLI smoke test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import sys
    d = new_pha(study_name="Smoke Test Study", facility="Test Plant")
    sg = add_safeguard(d, "PSV-100 relieves to flare", safeguard_type="PSV",
                       ipl_tag="PSV-100", is_ipl=True, pfd="0.01")
    rec = add_pha_recommendation(d, "Perform LOPA on this scenario",
                                 priority="High", status="Under Review")
    node = add_node(d, "Feed section")
    dev = add_deviation(node, deviation="High Pressure", guide_word="High",
                        parameter="Pressure")
    cause = add_cause(dev, cause="Blocked outlet", frequency="1E-1")
    con = add_consequence(cause, consequence="Vessel overpressure/rupture",
                          safeguard_ids=[sg], pha_recommendation_ids=[rec])
    issues = validate(d)
    print("validation issues:", issues)
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/smoke.opha"
    save(d, out)
    print("round-trip:", verify_round_trip(out, d))
