#!/usr/bin/env python3
"""
write_opha.py — Kenexis Open-PHA (.opha) writer.

Creates and modifies Open-PHA study files with byte-perfect round-trip
fidelity. Encodes every field the way the application does:

  * compact single-line JSON, separators=(',',':'), ensure_ascii=False
  * key insertion order preserved
  * string sentinels "null" / "empty" and empty-string "" kept distinct
  * numbers stored verbatim as strings (incl. scientific notation)
  * reference lists never empty ([{"ID":"empty"}] when unlinked)
  * base36 IDs, 22 chars

One companion data file lives next to this module and is required:
  * pha_templates.json — the application's own empty study for each of the
    four Analysis_Mode / Lopa_Mode pairs, at the current Ds_Rev. Every record
    shape in this module is read from these documents, so there is no separate
    skeleton to fall behind the application.

Two workflows: load() an existing file -> mutate with the add_* helpers ->
save(), or new_pha() -> add_* helpers -> save(). An unmodified load->save is
byte-identical. Do not hand-build Settings, Risk_Criteria or worksheet records:
the application needs keys a hand-built document leaves out, and files with
those gaps import cleanly and then fail later. See SKILL.md.
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
TEMPLATES_PATH = HERE / "pha_templates.json"

CAUSE_CONSEQUENCE = "CauseConsequence"    # Nodes > Deviations > Causes > Consequences
CONSEQUENCE_CAUSE = "ConsequenceCause"    # Nodes > Deviations > Consequences > Causes
ANALYSIS_MODES = (CAUSE_CONSEQUENCE, CONSEQUENCE_CAUSE)
LOPA_MODES = ("Explicit", "Implicit")
CATEGORIES = ("Safety", "Environment", "Asset", "Community", "Reputation")

# The 21 top-level keys, in canonical insertion order.
TOP_LEVEL_ORDER = [
    "Overview", "Settings", "Team_Members", "Sessions", "Team_Members_Sessions",
    "Revalidation_History", "Nodes", "Safeguards", "Pha_Recommendations",
    "Pha_Comments", "Lopa_Recommendations", "Lopa_Comments", "Parking_Lot",
    "Drawings", "Risk_Criteria", "Check_Lists", "Check_List_Recommendations",
    "Mocs", "Previous_Incidents", "Industry_Incidents", "Scais",
]

# Fields that may be unset as real JSON null in files the application has saved.
# New records use the application's own default, "".
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

# Optional fields the application adds to a record only when they apply.
OPTIONAL_FIELDS = {
    "Disabled", "Cause_Library_Id", "Cause_Library_Version",
    "Safeguard_Library_Id", "Safeguard_Library_Version", "CM_Library_Id",
}

# What the application and its reports cannot run without. All of these exist
# in every file from revision 39 on, so a gap at 39 or later is a damaged or
# hand-built file, not an old one.
MIN_CHECKED_DS_REV = 39
REQUIRED_NUMBERING = (
    "Nodes", "Deviations", "Consequences", "Causes", "Safeguards",
    "Pha_Recommendations", "Lopa_Recommendations", "Parking_Lot", "Check_Lists",
    "Check_List_Recommendations", "Check_List_Questions", "Pha_Comments", "Lopa_Comments",
)
REQUIRED_TABS = (
    "NodesTab", "DeviationsTab", "PhaWorksheetsTab", "LopaWorksheetsTab",
    "CheckListsTab", "RecommendationsTab", "SafeguardsTab", "MocsTab",
    "ParkingLotTab", "RiskCriteriaTab", "AlarpWorksheetsTab", "ScaisTab",
)
REQUIRED_GRIDS = ("Intersections", "Team_Members_Sessions", "Consequence_Intersections")
REQUIRED_RISK_POOLS = ("Likelihoods", "Severities", "Risk_Rankings", "Intersections")

# The application's LOPA calculation counts a factor only if its column is
# visible, so a hidden column silently changes MEL and RRF. Its empty study hides
# several of these. Both LOPA styles are listed so a study can switch style.
LOPA_FACTOR_COLUMNS = (
    "Enabling_Events", "EE_Probability", "EE_Credit",
    "Conditional_Modifiers", "CM_Probability", "CM_Credit",
    "Safeguard_IDs", "Pfd", "Ipl_Credit",
    "Lopa_Recommendation_IDs", "Lopa_Recommendation_Pfd", "Lopa_Recommendation_Ipl_Credit",
) + tuple(f"Conditional_Modifiers_{c}" for c in CATEGORIES) + tuple(
    f"CM_{c[0]}_{field}" for c in CATEGORIES for field in ("Probability", "Credit"))
# Not on the LOPA worksheet, so their columns do not count.
LOPA_IGNORED_TREES = ("Check_Lists_Children", "Scais_Children")


class _SeedDeviation(OrderedDict):
    """The blank deviation a new node starts with. Replaced by the first
    add_deviation(), as the application replaces it when a user types one."""


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


def save(data, path, validate_first=True, allow_hidden_factors=False):
    """Write an .opha file as compact single-line JSON (no trailing newline).

    If validate_first, raises ValueError on any validation error, and returns
    the warnings (report these to the user). allow_hidden_factors is for a study
    whose owner hid a LOPA factor on purpose, so that it does not count."""
    warnings = []
    if validate_first:
        issues = validate(data, allow_hidden_factors)
        errs = [i for i in issues if i["severity"] == "error"]
        if errs:
            msg = "; ".join(f"{e['path']}: {e['message']}" for e in errs[:10])
            raise ValueError(f"Refusing to save — {len(errs)} validation error(s): {msg}")
        warnings = [i for i in issues if i["severity"] == "warning"]
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(data))
    return warnings


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
# Templates & record shapes
# --------------------------------------------------------------------------- #

_TEMPLATES = None
_SKELETONS = {}


def _templates():
    global _TEMPLATES
    if _TEMPLATES is None:
        with open(TEMPLATES_PATH, "r", encoding="utf-8") as f:
            _TEMPLATES = json.loads(f.read(), object_pairs_hook=OrderedDict)
    return _TEMPLATES


def current_ds_rev():
    """The schema revision of the shipped templates: what new_pha() writes."""
    return _templates()["dsRev"]


def _template(analysis_mode, lopa_mode):
    """A private copy of the application's empty study for a mode pair."""
    if analysis_mode not in ANALYSIS_MODES:
        raise ValueError(f"analysis_mode must be one of {ANALYSIS_MODES}, got {analysis_mode!r}")
    if lopa_mode not in LOPA_MODES:
        raise ValueError(f"lopa_mode must be one of {LOPA_MODES}, got {lopa_mode!r}")
    return copy.deepcopy(_templates()["documents"][f"{analysis_mode}/{lopa_mode}"])


def _bare(row):
    rec = copy.deepcopy(row)
    if "ID" in rec:
        rec["ID"] = ""
    return rec


def _skeletons(analysis_mode):
    """Every record shape for an analysis mode, taken from the empty study.
    The two LOPA modes share one shape: each record carries both styles' fields."""
    if analysis_mode in _SKELETONS:
        return _SKELETONS[analysis_mode]
    doc = _template(analysis_mode, "Explicit")
    s = OrderedDict()
    for key in TOP_LEVEL_ORDER:
        if isinstance(doc[key], list) and doc[key]:
            s[key] = _bare(doc[key][0])
    s["Team_Members_Sessions"] = OrderedDict(
        [("ID", ""), ("Team_Member_ID", ""), ("Session_ID", ""), ("Value", NULL_SENTINEL)])

    dev = doc["Nodes"][0]["Deviations"][0]
    if analysis_mode == CAUSE_CONSEQUENCE:
        cause = dev["Causes"][0]
        consequence = cause["Consequences"][0]
        s["Causes"], s["Consequences"] = _bare(cause), _bare(consequence)
        s["Causes"]["Consequences"] = []
        s["Deviations"] = _bare(dev)
        s["Deviations"]["Causes"] = []
        s["Conditional_Modifiers"] = _bare(consequence["Conditional_Modifiers"][0])
    else:
        consequence = dev["Consequences"][0]
        cause = consequence["Causes"][0]
        s["Causes"], s["Consequences"] = _bare(cause), _bare(consequence)
        s["Consequences"]["Causes"] = []
        s["Deviations"] = _bare(dev)
        s["Deviations"]["Consequences"] = []
        for category in CATEGORIES:
            key = f"Conditional_Modifiers_{category}"
            s[key] = _bare(consequence[key][0])
    s["Nodes"]["Deviations"] = []
    s["Enabling_Events"] = _bare(cause["Enabling_Events"][0])
    s["Alarp_Analysis"] = _bare(consequence["Alarp_Analysis"][0])
    s["Check_List_Questions"] = _bare(doc["Check_Lists"][0]["Check_List_Questions"][0])
    for key, rows in doc["Risk_Criteria"].items():
        if isinstance(rows, list) and rows:
            s["RC:" + key] = _bare(rows[0])

    _SKELETONS[analysis_mode] = s
    return s


def new_record(record_type, assign_id=True, analysis_mode=CAUSE_CONSEQUENCE, **fields):
    """Build a canonical record of the given type.

    Every field is present in canonical order with the application's default.
    Provided **fields override defaults; a field the record does not have is an
    error. ID is auto-generated unless assign_id=False or an 'ID' override is
    supplied. analysis_mode matters for Causes, Consequences and Deviations,
    which differ between the two trees.

    record_type is a record key, e.g. 'Safeguards', 'Nodes', 'Deviations',
    'Consequences', 'Check_List_Questions', 'RC:Severities'.
    """
    skels = _skeletons(analysis_mode)
    if record_type not in skels:
        raise KeyError(f"Unknown record type {record_type!r}. Known: {list(skels)}")
    rec = copy.deepcopy(skels[record_type])
    if assign_id and "ID" in rec and "ID" not in fields:
        rec["ID"] = make_id()
    for k, v in fields.items():
        if k not in rec and k not in OPTIONAL_FIELDS:
            raise KeyError(f"Field {k!r} not in {record_type} ({analysis_mode})")
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


def _reidentify(doc):
    """Give every record a fresh ID and repoint what references it, so two
    studies never share IDs. Settings holds no record IDs."""
    ids = {}

    def collect(v):
        if isinstance(v, dict):
            rid = v.get("ID")
            if isinstance(rid, str) and rid not in ("", EMPTY_SENTINEL):
                ids.setdefault(rid, make_id())
            for x in v.values():
                collect(x)
        elif isinstance(v, list):
            for x in v:
                collect(x)

    def rewrite(v):
        if isinstance(v, dict):
            for k, x in v.items():
                v[k] = rewrite(x)
        elif isinstance(v, list):
            for i, x in enumerate(v):
                v[i] = rewrite(x)
        elif isinstance(v, str):
            return ids.get(v, v)
        return v

    bodies = [k for k in doc if k != "Settings"]
    for key in bodies:
        collect(doc[key])
    for key in bodies:
        doc[key] = rewrite(doc[key])
    return doc


def show_lopa_columns(data):
    """Turn on every column the LOPA calculation depends on. new_pha() does this.
    Use it on a loaded study only if its owner did not hide a factor on purpose."""
    def show(node):
        for key, value in node.items():
            if key in LOPA_IGNORED_TREES:
                continue
            if isinstance(value, dict):
                show(value)
            elif key in LOPA_FACTOR_COLUMNS:
                node[key] = True

    show(data["Settings"]["Column_Visibility"])


def _column_visibility(root, node, key):
    """The application's getColumnVisibility(obj, key, "Lopa"): True, False, or
    None when the tree does not mention the column. A later match wins."""
    result = None
    for name, value in node.items():
        if name in LOPA_IGNORED_TREES:
            continue
        if name == key:
            result = value
        elif isinstance(value, dict):
            found = _column_visibility(root, value, key)
            if found is None:
                continue
            if found is True:
                # A list column also needs a visible column of its own.
                if "_IDs" in key:
                    children = root.get(key.replace("_ID", "", 1) + "_Children") or {}
                    found = any(v is True for v in children.values())
                elif f"{key}_Children" in value:
                    found = any(v is True for v in value[f"{key}_Children"].values())
            result = found
    return result


def _hidden_lopa_factors(data, mode):
    """The LOPA factors this study holds whose columns are hidden, so that the
    application would leave them out of MEL and RRF."""
    tree = data.get("Settings", {}).get("Column_Visibility")
    if not isinstance(tree, dict):
        return []
    explicit = data["Settings"].get("Lopa_Mode") != "Implicit"

    def hidden(*columns):
        return any(_column_visibility(tree, tree, c) is False for c in columns)

    def rows(record, key):
        return [r for r in record.get(key) or [] if r.get("ID") != EMPTY_SENTINEL]

    credited = {s.get("ID") for s in data.get("Safeguards", []) if s.get("Is_Ipl") == "true"}
    field = "Lopa_Recommendation_Pfd" if explicit else "Lopa_Recommendation_Ipl_Credit"
    reduced = {r.get("ID") for r in data.get("Lopa_Recommendations", []) if r.get(field) not in (None, "")}
    out = []

    def note(label, *columns):
        if hidden(*columns) and label not in out:
            out.append(label)

    for kind, _, rec in _worksheet_rows(data, mode):
        if kind == "Causes" and rows(rec, "Enabling_Events"):
            note("enabling events", "Enabling_Events", "EE_Probability" if explicit else "EE_Credit")
        if kind == "Consequences":
            if rows(rec, "Conditional_Modifiers"):
                note("conditional modifiers", "Conditional_Modifiers", "CM_Probability" if explicit else "CM_Credit")
            for c in CATEGORIES:
                if rows(rec, f"Conditional_Modifiers_{c}"):
                    note(f"{c} conditional modifiers", f"Conditional_Modifiers_{c}",
                         f"CM_{c[0]}_{'Probability' if explicit else 'Credit'}")
            if {r.get("ID") for r in rows(rec, "Lopa_Recommendation_IDs")} & reduced:
                note("LOPA recommendation credits", "Lopa_Recommendation_IDs", field)
        if {r.get("ID") for r in rows(rec, "Safeguard_IDs")} & credited:
            note("credited IPLs", "Safeguard_IDs", "Pfd" if explicit else "Ipl_Credit")
    return out


def _load_current(path, what):
    doc = load(path)
    rev = (doc.get("Settings") or {}).get("Ds_Rev")
    if rev != current_ds_rev():
        raise ValueError(
            f"{what} {path} is at schema revision {rev}; this writer starts studies at "
            f"{current_ds_rev()}. Open it in Open-PHA, save it, export it again, and retry.")
    return doc


def new_pha(study_name="", template_path=None, analysis_mode=None, lopa_mode=None,
            risk_criteria_from=None, **overview):
    """Create a new study dict from the application's empty study.

    analysis_mode / lopa_mode pick the tree and LOPA style (default
    CauseConsequence / Explicit). risk_criteria_from is a path to a current
    .opha whose risk matrix and category names to use (the customer's criteria;
    its LOPA mode must match). template_path instead takes a whole current .opha
    as the base, modes included. overview kwargs (e.g. facility='...',
    project_number='...') are written to matching Overview fields.
    """
    if template_path:
        data = _load_current(template_path, "Template")
        settings = data["Settings"]
        if analysis_mode not in (None, settings.get("Analysis_Mode")):
            raise ValueError(f"analysis_mode {analysis_mode!r} does not match the template's "
                             f"{settings.get('Analysis_Mode')!r}")
        if lopa_mode not in (None, settings.get("Lopa_Mode")):
            raise ValueError(f"lopa_mode {lopa_mode!r} does not match the template's "
                             f"{settings.get('Lopa_Mode')!r}")
    else:
        data = _template(analysis_mode or CAUSE_CONSEQUENCE, lopa_mode or "Explicit")

    if risk_criteria_from:
        source = _load_current(risk_criteria_from, "Risk criteria source")
        if source["Settings"].get("Lopa_Mode") != data["Settings"]["Lopa_Mode"]:
            raise ValueError(
                f"{risk_criteria_from} is {source['Settings'].get('Lopa_Mode')} LOPA and this "
                f"study is {data['Settings']['Lopa_Mode']}; a matrix cannot change style.")
        data["Risk_Criteria"] = source["Risk_Criteria"]
        data["Settings"]["Consequence_Category_Names"] = source["Settings"]["Consequence_Category_Names"]

    _reidentify(data)
    show_lopa_columns(data)
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
# Collection helpers (append a record, dropping an untouched seed row)
# --------------------------------------------------------------------------- #

def _mode_of(data):
    mode = data.get("Settings", {}).get("Analysis_Mode")
    if mode not in ANALYSIS_MODES:
        raise ValueError(f"Settings.Analysis_Mode is {mode!r}; expected one of {ANALYSIS_MODES}")
    return mode


def _without_ids(v):
    if isinstance(v, dict):
        return {k: _without_ids(x) for k, x in v.items() if k != "ID"}
    if isinstance(v, list):
        return [_without_ids(x) for x in v]
    return v


def _is_untouched_seed(data, collection, row):
    if row.get("ID") == EMPTY_SENTINEL:
        return True
    seed = _templates()["documents"][f"{_mode_of(data)}/Explicit"][collection]
    return bool(seed) and _without_ids(row) == _without_ids(seed[0])


def _append(data, collection, record):
    """Append record to a top-level collection, replacing a lone blank seed."""
    lst = data.setdefault(collection, [])
    if len(lst) == 1 and _is_untouched_seed(data, collection, lst[0]):
        lst.clear()
    lst.append(record)
    return record


def add_safeguard(data, safeguard, safeguard_type="", ipl_tag="",
                  is_safeguard=True, is_ipl=False, pfd="", selected_sil=None,
                  **extra):
    """Add to the flat safeguard library. Pass Ipl_Credit=... as well as pfd when
    both are known: Lopa_Mode decides which one the calculation reads. A record's
    Is_Ipl and Pfd apply to every scenario linked to it, so a safeguard credited
    in one scenario and not in another needs two records."""
    rec = new_record("Safeguards", analysis_mode=_mode_of(data), Safeguard=safeguard,
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
    rec = new_record("Pha_Recommendations", analysis_mode=_mode_of(data),
                     Pha_Recommendation=text,
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
    rec = new_record("Lopa_Recommendations", analysis_mode=_mode_of(data),
                     Lopa_Recommendation=text, **extra)
    _append(data, "Lopa_Recommendations", rec)
    return rec["ID"]


def add_pha_comment(data, text):
    rec = new_record("Pha_Comments", analysis_mode=_mode_of(data), Pha_Comment=text)
    _append(data, "Pha_Comments", rec)
    return rec["ID"]


def add_lopa_comment(data, text):
    rec = new_record("Lopa_Comments", analysis_mode=_mode_of(data), Lopa_Comment=text)
    _append(data, "Lopa_Comments", rec)
    return rec["ID"]


def add_drawing(data, drawing, revision="", document_type="",
                description="", link=""):
    rec = new_record("Drawings", analysis_mode=_mode_of(data), Drawing=drawing,
                     Revision=revision, Document_Type=document_type,
                     Drawing_Description=description, Link=link)
    _append(data, "Drawings", rec)
    return rec["ID"]


def add_team_member(data, name, company="", title="", **extra):
    rec = new_record("Team_Members", analysis_mode=_mode_of(data), Name=name,
                     Company=company, Title=title, **extra)
    _append(data, "Team_Members", rec)
    return rec["ID"]


def add_session(data, session, date="", facilitator_id=None, scribe_id=None,
                **extra):
    rec = new_record("Sessions", analysis_mode=_mode_of(data), Session=session,
                     Date=date, **extra)
    if facilitator_id:
        rec["Facilitator_ID"] = facilitator_id
    if scribe_id:
        rec["Scribe_ID"] = scribe_id
    _append(data, "Sessions", rec)
    return rec["ID"]


# --------------------------------------------------------------------------- #
# Worksheet tree builders
#
#   CauseConsequence:  add_node -> add_deviation -> add_cause(deviation)
#                                                -> add_consequence(cause)
#   ConsequenceCause:  add_node -> add_deviation -> add_consequence(deviation)
#                                                -> add_cause(consequence)
#
# A parent starts with the same blank "empty" child row the application gives
# it, and the first real child replaces that row.
# --------------------------------------------------------------------------- #

def _seed_consequence(analysis_mode):
    rec = new_record("Consequences", assign_id=False, analysis_mode=analysis_mode, ID=EMPTY_SENTINEL)
    if analysis_mode == CONSEQUENCE_CAUSE:
        rec["Causes"] = [_seed_cause(analysis_mode)]
    return rec


def _seed_cause(analysis_mode):
    rec = new_record("Causes", assign_id=False, analysis_mode=analysis_mode, ID=EMPTY_SENTINEL)
    if analysis_mode == CAUSE_CONSEQUENCE:
        rec["Consequences"] = [_seed_consequence(analysis_mode)]
    return rec


def _new_deviation(analysis_mode, **fields):
    rec = new_record("Deviations", analysis_mode=analysis_mode, **fields)
    if analysis_mode == CAUSE_CONSEQUENCE:
        rec["Causes"] = [_seed_cause(analysis_mode)]
    else:
        rec["Consequences"] = [_seed_consequence(analysis_mode)]
    return rec


def _add_child(parent, key, record):
    lst = parent[key]
    if len(lst) == 1 and lst[0].get("ID") == EMPTY_SENTINEL:
        lst.clear()
    lst.append(record)
    return record


def add_node(data, description, session_ids=None, drawing_ids=None, **extra):
    mode = _mode_of(data)
    rec = new_record("Nodes", analysis_mode=mode, Node_Description=description, **extra)
    rec["Session_IDs"] = ref_list(session_ids)
    rec["Drawing_IDs"] = ref_list(drawing_ids)
    rec["Deviations"] = [_SeedDeviation(_new_deviation(mode))]
    _append(data, "Nodes", rec)
    return rec


def _node_mode(node):
    for dev in node.get("Deviations", []):
        if "Consequences" in dev:
            return CONSEQUENCE_CAUSE
        if "Causes" in dev:
            return CAUSE_CONSEQUENCE
    raise ValueError("Node has no deviations to show its analysis mode; build it with add_node().")


def add_deviation(node, deviation="", guide_word="", parameter="", **extra):
    rec = _new_deviation(_node_mode(node), Deviation=deviation, Guide_Word=guide_word,
                         Parameter=parameter, **extra)
    if len(node["Deviations"]) == 1 and isinstance(node["Deviations"][0], _SeedDeviation):
        node["Deviations"].clear()
    node["Deviations"].append(rec)
    return rec


def add_cause(parent, cause="", frequency="", safeguard_ids=None, **extra):
    """Add a cause. parent is a deviation in a CauseConsequence study, or a
    consequence in a ConsequenceCause study (where safeguards link to the cause,
    so safeguard_ids belongs here)."""
    if "Deviation" in parent and "Causes" in parent:
        mode = CAUSE_CONSEQUENCE
    elif "Consequence" in parent and "Causes" in parent:
        mode = CONSEQUENCE_CAUSE
    else:
        raise ValueError("add_cause needs a deviation in a CauseConsequence study or a consequence "
                         "in a ConsequenceCause study. Check Settings.Analysis_Mode: in a "
                         "ConsequenceCause study call add_consequence(deviation) first.")
    if safeguard_ids is not None and mode == CAUSE_CONSEQUENCE:
        raise ValueError("In a CauseConsequence study safeguards link to the consequence; "
                         "pass safeguard_ids to add_consequence().")
    rec = new_record("Causes", analysis_mode=mode, Cause=cause,
                     Frequency=str(frequency) if frequency != "" else "", **extra)
    if mode == CAUSE_CONSEQUENCE:
        rec["Consequences"] = [_seed_consequence(mode)]
    else:
        rec["Safeguard_IDs"] = ref_list(safeguard_ids)
    return _add_child(parent, "Causes", rec)


def add_consequence(parent, consequence="", safeguard_ids=None,
                    pha_recommendation_ids=None, lopa_recommendation_ids=None,
                    pha_comment_ids=None, lopa_comment_ids=None, **extra):
    """Add a consequence. parent is a cause in a CauseConsequence study, or a
    deviation in a ConsequenceCause study."""
    if "Cause" in parent and "Consequences" in parent:
        mode = CAUSE_CONSEQUENCE
    elif "Deviation" in parent and "Consequences" in parent:
        mode = CONSEQUENCE_CAUSE
    else:
        raise ValueError("add_consequence needs a cause in a CauseConsequence study or a deviation "
                         "in a ConsequenceCause study. Check Settings.Analysis_Mode.")
    if safeguard_ids is not None and mode == CONSEQUENCE_CAUSE:
        raise ValueError("In a ConsequenceCause study safeguards link to the cause; "
                         "pass safeguard_ids to add_cause().")
    rec = new_record("Consequences", analysis_mode=mode, Consequence=consequence, **extra)
    rec["Pha_Recommendation_IDs"] = ref_list(pha_recommendation_ids)
    rec["Lopa_Recommendation_IDs"] = ref_list(lopa_recommendation_ids)
    rec["Pha_Comment_IDs"] = ref_list(pha_comment_ids)
    rec["Lopa_Comment_IDs"] = ref_list(lopa_comment_ids)
    if mode == CAUSE_CONSEQUENCE:
        rec["Safeguard_IDs"] = ref_list(safeguard_ids)
    else:
        rec["Causes"] = [_seed_cause(mode)]
    return _add_child(parent, "Consequences", rec)


def add_enabling_event(cause, description="", probability="", credit=""):
    rec = OrderedDict(cause["Enabling_Events"][0])
    rec.update(ID=make_id(), EE_Description=description,
               EE_Probability=str(probability) if probability != "" else "",
               EE_Credit=str(credit) if credit != "" else "")
    return _add_child(cause, "Enabling_Events", rec)


def add_conditional_modifier(consequence, description="", probability="", credit="", category=None):
    """Add a conditional modifier. A ConsequenceCause consequence keeps one list
    per severity category, so category ('Safety', ...) is required there."""
    if "Conditional_Modifiers" in consequence:
        if category is not None:
            raise ValueError("A CauseConsequence consequence has one modifier list; omit category.")
        key, prefix = "Conditional_Modifiers", "CM"
    else:
        if category not in CATEGORIES:
            raise ValueError(f"A ConsequenceCause consequence needs category in {CATEGORIES}.")
        key, prefix = f"Conditional_Modifiers_{category}", f"CM_{category[0]}"
    rec = copy.deepcopy(consequence[key][0])
    rec["ID"] = make_id()
    rec[f"{prefix}_Description"] = description
    rec[f"{prefix}_Probability"] = str(probability) if probability != "" else ""
    rec[f"{prefix}_Credit"] = str(credit) if credit != "" else ""
    return _add_child(consequence, key, rec)


# --------------------------------------------------------------------------- #
# Reference maintenance
# --------------------------------------------------------------------------- #

def link_safeguards(record, safeguard_ids):
    """Set the safeguards of a consequence (CauseConsequence) or cause (ConsequenceCause)."""
    if "Safeguard_IDs" not in record:
        raise ValueError("This record has no Safeguard_IDs: in a ConsequenceCause study "
                         "safeguards link to the cause, not the consequence.")
    record["Safeguard_IDs"] = ref_list(safeguard_ids)


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


def _is_set(v):
    return v not in (None, "", NULL_SENTINEL, EMPTY_SENTINEL)


def _generic(path):
    """Nodes[3].Deviations[0] -> Nodes[].Deviations[], to group row-level issues."""
    out, skip = [], False
    for ch in path:
        if ch == "[":
            skip = True
            out.append("[")
        elif ch == "]":
            skip = False
            out.append("]")
        elif not skip:
            out.append(ch)
    return "".join(out)


class _Grouped:
    """Collects a row-level issue once, however many rows it hits."""

    def __init__(self):
        self.seen = OrderedDict()

    def add(self, severity, path, message):
        key = (severity, _generic(path), message)
        self.seen[key] = self.seen.get(key, 0) + 1

    def issues(self):
        return [_issue(sev, path, f"{msg} ({n} rows)" if n > 1 else msg)
                for (sev, path, msg), n in self.seen.items()]


def _check_settings(s, mode, issues):
    def require(path, cond, message):
        if not cond:
            issues.append(_issue("error", path, message))

    def object_with(key, names):
        v = s.get(key)
        if not isinstance(v, dict):
            issues.append(_issue("error", f"Settings.{key}", "must be an object"))
            return {}
        missing = [n for n in names if n not in v]
        require(f"Settings.{key}", not missing,
                f"missing {', '.join(missing)}; start from new_pha() and do not stub Settings")
        return v

    object_with("Numbering", REQUIRED_NUMBERING)
    object_with("Tab_Visibility", REQUIRED_TABS)
    grids = object_with("Grid_Settings", REQUIRED_GRIDS)
    for name in REQUIRED_GRIDS:
        grid = grids.get(name)
        if isinstance(grid, dict):
            require(f"Settings.Grid_Settings.{name}",
                    all(k in grid for k in ("headerHlocation", "headerVlocation", "invertAxis")),
                    "needs headerHlocation, headerVlocation and invertAxis")
    cats = object_with("Consequence_Category_Names", CATEGORIES)
    for name in CATEGORIES:
        cat = cats.get(name)
        if name in cats:
            require(f"Settings.Consequence_Category_Names.{name}",
                    isinstance(cat, dict) and "name" in cat and "code" in cat,
                    "needs name and code")

    wanted = "Causes" if mode == CAUSE_CONSEQUENCE else "Consequences"
    for key in ("Column_Visibility", "Column_Headers"):
        tree = s.get(key)
        tree = tree if isinstance(tree, dict) else {}
        dev = (tree.get("Nodes_Children") or {}).get("Deviations_Children") or {}
        require(f"Settings.{key}", wanted in dev,
                f"does not match Analysis_Mode {mode}: its deviation columns have no {wanted}. "
                "Analysis_Mode cannot be changed by editing Settings; start from "
                f"new_pha(analysis_mode={mode!r}).")


def _worksheet_rows(data, mode):
    """Yield (kind, path, record) for each Deviation, Cause and Consequence."""
    outer, inner = (("Causes", "Consequences") if mode == CAUSE_CONSEQUENCE
                    else ("Consequences", "Causes"))
    for ni, node in enumerate(data.get("Nodes", [])):
        np = f"Nodes[{ni}]"
        for di, dev in enumerate(node.get("Deviations", [])):
            dp = f"{np}.Deviations[{di}]"
            yield "Deviations", dp, dev
            for oi, o in enumerate(dev.get(outer) or []):
                op = f"{dp}.{outer}[{oi}]"
                yield outer, op, o
                for ii, i in enumerate(o.get(inner) or []):
                    yield inner, f"{op}.{inner}[{ii}]", i


def _check_worksheet(data, mode, grouped, strict_shape):
    outer = "Causes" if mode == CAUSE_CONSEQUENCE else "Consequences"
    inner = "Consequences" if mode == CAUSE_CONSEQUENCE else "Causes"
    skels = _skeletons(mode)
    ids = {key: {r.get("ID") for r in data.get(key, [])
                 if r.get("ID") not in (None, EMPTY_SENTINEL)}
           for key in ("Safeguards", "Pha_Recommendations", "Lopa_Recommendations")}

    def check_ref(path, lst, universe, label):
        if not isinstance(lst, list) or len(lst) == 0:
            grouped.add("error", path, "reference list must not be empty (use [{'ID':'empty'}])")
            return
        for r in lst:
            rid = r.get("ID") if isinstance(r, dict) else r
            if not isinstance(r, dict):
                grouped.add("error", path, "reference list entries must be {'ID': ...} objects")
            elif rid not in (EMPTY_SENTINEL, None) and universe is not None and rid not in universe:
                grouped.add("warning", path, f"{label} reference not found")

    for ni, node in enumerate(data.get("Nodes", [])):
        np = f"Nodes[{ni}]"
        check_ref(np + ".Session_IDs", node.get("Session_IDs"), None, "session")
        check_ref(np + ".Drawing_IDs", node.get("Drawing_IDs"), None, "drawing")
        if not node.get("Deviations"):
            grouped.add("warning", np + ".Deviations", "node has no deviations; the application "
                                                       "always keeps at least one")

    rated = {"causes": 0, "frequency": 0, "likelihood": 0, "consequences": 0, "severity": 0,
             "tmel": 0, "above_one": 0, "not_numeric": 0}
    for kind, path, rec in _worksheet_rows(data, mode):
        if kind == "Deviations":
            if not isinstance(rec.get(outer), list):
                grouped.add("error", path, f"deviation has no {outer} list, which {mode} requires")
            elif not rec[outer]:
                grouped.add("warning", path, f"empty {outer} list; use add_deviation(), which "
                                             "keeps the application's blank row")
            continue

        if kind == outer and not isinstance(rec.get(inner), list):
            grouped.add("error", path, f"{kind} row has no {inner} list, which {mode} requires")
        if strict_shape:
            absent = [k for k in skels[kind] if k not in rec]
            if absent:
                grouped.add("warning", path, f"missing fields: {', '.join(sorted(absent))}")
        real = rec.get("ID") != EMPTY_SENTINEL

        if kind == "Consequences":
            for key in ("Alarp_Analysis", "Alarp_Required"):
                if key not in rec:
                    grouped.add("error", f"{path}.{key}", "required on every consequence; "
                                                          "reports fail without it")
            check_ref(path + ".Pha_Recommendation_IDs", rec.get("Pha_Recommendation_IDs"),
                      ids["Pha_Recommendations"], "PHA rec")
            check_ref(path + ".Lopa_Recommendation_IDs", rec.get("Lopa_Recommendation_IDs"),
                      ids["Lopa_Recommendations"], "LOPA rec")
            for f in ("Consequence_Severity_ID", "Consequence_Severity_ID_Before_Safeguards",
                      "Consequence_Severity_ID_After_Recommendations"):
                if rec.get(f) == NULL_SENTINEL:
                    grouped.add("error", f"{path}.{f}",
                                "severity must be unset as \"\" or JSON null, not the string \"null\"")
            if real:
                rated["consequences"] += 1
                severity_keys = (["Consequence_Severity_ID"] if mode == CAUSE_CONSEQUENCE
                                 else [f"{c}_Severity_ID" for c in CATEGORIES])
                if any(_is_set(rec.get(k)) for k in severity_keys):
                    rated["severity"] += 1
                tmel_keys = ["Tmel"] if mode == CAUSE_CONSEQUENCE else [f"Tmel_{c}" for c in CATEGORIES]
                if any(_is_set(rec.get(k)) for k in tmel_keys):
                    rated["tmel"] += 1
        if kind == "Causes" and real:
            rated["causes"] += 1
            frequency = rec.get("Frequency")
            rated["frequency"] += 1 if _is_set(frequency) else 0
            rated["likelihood"] += 1 if _is_set(rec.get("Likelihood_ID")) else 0
            if _is_set(frequency):
                try:
                    rated["above_one"] += 1 if float(frequency) > 1 else 0
                except (TypeError, ValueError):
                    rated["not_numeric"] += 1
        # Safeguards sit on the lower row of whichever tree this is.
        if kind == inner:
            check_ref(path + ".Safeguard_IDs", rec.get("Safeguard_IDs"), ids["Safeguards"], "safeguard")
    return rated


def validate(data, allow_hidden_factors=False):
    """Structural + encoding validation. Returns a list of issue dicts with
    severity 'error' (blocks save) or 'warning' (advisory; tell the user).

    A clean result means the application can run the file, not that the study is
    right: it does not check engineering values or recalculate LOPA results."""
    issues = []

    # 1. Top-level keys present and correctly typed.
    for k in TOP_LEVEL_ORDER:
        if k not in data:
            issues.append(_issue("error", k, "missing top-level key"))
    for k in ("Overview", "Settings", "Risk_Criteria"):
        if k in data and not isinstance(data[k], dict):
            issues.append(_issue("error", k, "must be an object"))

    # 2. Settings semantic config.
    s = data.get("Settings")
    s = s if isinstance(s, dict) else {}
    rev = s.get("Ds_Rev")
    rev_ok = isinstance(rev, int) and not isinstance(rev, bool)
    if not rev_ok:
        issues.append(_issue("error", "Settings.Ds_Rev", "must be an integer"))
    elif rev > current_ds_rev():
        issues.append(_issue("error", "Settings.Ds_Rev",
                             f"{rev} is newer than this writer's {current_ds_rev()}"))
    mode = s.get("Analysis_Mode")
    mode_ok = mode in ANALYSIS_MODES
    if not mode_ok:
        issues.append(_issue("error", "Settings.Analysis_Mode", f"must be one of {ANALYSIS_MODES}"))
    if s.get("Lopa_Mode") not in LOPA_MODES:
        issues.append(_issue("error", "Settings.Lopa_Mode", f"must be one of {LOPA_MODES}"))
    if s.get("Encrypt") is True:
        issues.append(_issue("error", "Settings.Encrypt",
                             "encrypted files are out of scope"))

    checked = rev_ok and rev >= MIN_CHECKED_DS_REV
    if rev_ok and rev < current_ds_rev():
        issues.append(_issue("warning", "Settings.Ds_Rev",
                             f"file is at revision {rev}; open and save it in Open-PHA to upgrade it "
                             f"to {current_ds_rev()} before editing it here"))
    if checked and mode_ok:
        _check_settings(s, mode, issues)

    # 3. Risk matrix. The application adds these only to a study that lacks
    #    them, so an empty pool is never repaired on open.
    if checked and isinstance(data.get("Risk_Criteria"), dict):
        for pool in REQUIRED_RISK_POOLS:
            rows = data["Risk_Criteria"].get(pool)
            if not isinstance(rows, list) or not [r for r in rows if r.get("ID") != EMPTY_SENTINEL]:
                issues.append(_issue("error", f"Risk_Criteria.{pool}",
                                     "must have at least one row; start from new_pha() or "
                                     "new_pha(risk_criteria_from=...)"))

    # 4. Worksheet: shape for the analysis mode, reference lists, severity
    #    encoding, and how much of it carries a rating.
    if mode_ok:
        grouped = _Grouped()
        rated = _check_worksheet(data, mode, grouped, rev_ok and rev == current_ds_rev())
        issues.extend(_issue(i["severity"], i["path"], i["message"]) for i in grouped.issues())
        if rev_ok and rev == current_ds_rev():
            skel = _skeletons(mode)["Safeguards"]
            absent = sorted({k for r in data.get("Safeguards", []) for k in skel if k not in r})
            if absent:
                issues.append(_issue("warning", "Safeguards[]", f"missing fields: {', '.join(absent)}"))
        issues.extend(_rating_warnings(rated, s.get("Lopa_Mode")))
        if checked:
            hidden = _hidden_lopa_factors(data, mode)
            if hidden:
                severity = "warning" if allow_hidden_factors else "error"
                issues.append(_issue(severity, "Settings.Column_Visibility",
                                     f"{', '.join(hidden)} have hidden columns, so Open-PHA leaves them out "
                                     "of MEL and RRF. Call show_lopa_columns(data), or pass "
                                     "allow_hidden_factors=True if the study's owner hid them on purpose."))

    # 5. Seed collections should carry an empty seed when otherwise empty.
    for coll in SEED_COLLECTIONS:
        lst = data.get(coll, [])
        if len(lst) == 0:
            issues.append(_issue("warning", coll,
                                 "seed collection is empty; expected a single "
                                 "{'ID':'empty'} record"))

    return issues


def _rating_warnings(rated, lopa_mode):
    """A worksheet with no ratings imports and opens but has no risk results,
    and that is easy to miss, so say so rather than saving silently."""
    out = []
    causes, consequences = rated["causes"], rated["consequences"]
    field = "likelihood" if lopa_mode == "Implicit" else "frequency"
    if causes and rated[field] < causes:
        out.append(_issue("warning", "Nodes[]", f"{causes - rated[field]} of {causes} causes have no "
                          f"{field}, which {lopa_mode} LOPA reads"))
    if consequences and rated["severity"] < consequences:
        out.append(_issue("warning", "Nodes[]", f"{consequences - rated['severity']} of {consequences} "
                          "consequences have no severity"))
    if lopa_mode != "Implicit" and consequences and rated["tmel"] < consequences:
        out.append(_issue("warning", "Nodes[]", f"{consequences - rated['tmel']} of {consequences} "
                          "consequences have no TMEL, so Open-PHA cannot work out their RRF"))
    if rated["above_one"]:
        out.append(_issue("warning", "Nodes[]", f"{rated['above_one']} causes have a frequency above 1 per "
                          "year. Check that a \"1 in N years\" source value was converted to 1/N"))
    if rated["not_numeric"]:
        out.append(_issue("warning", "Nodes[]", f"{rated['not_numeric']} causes have a frequency that is "
                          "not a number"))
    return out


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
    print("validation issues:", validate(d))
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/smoke.opha"
    save(d, out)
    print("round-trip:", verify_round_trip(out, d))
