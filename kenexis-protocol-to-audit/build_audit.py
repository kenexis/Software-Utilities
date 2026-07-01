#!/usr/bin/env python3
"""
build_audit.py — Build a Kenexis Open-Audit (.aud) file from a markdown protocol.

Parses an audit protocol written in markdown (H2 = module/Category, optional H3
= Element, GitHub-flavored tables of questions) and constructs a populated
.aud file via the kenexis-open-audit-writer skill.

CLI:
    python3 build_audit.py --protocol PROTOCOL.md --output OUT.aud \\
        --study-name "Study X" --facility "Plant 1" \\
        --coordinator "Pat Rivera" --coordinator-email "..." \\
        --project-number "2300.001"

The writer skill (write_aud.py) must be importable. Pass its directory via
PYTHONPATH or place the skills side-by-side.
"""
import argparse
import os
import re
import sys
from pathlib import Path

# Locate the writer skill — try common adjacent locations.
HERE = Path(__file__).resolve().parent
WRITER_CANDIDATES = [
    HERE.parent / "kenexis-open-audit-writer",
    HERE / "../kenexis-open-audit-writer",
    Path("/sessions/gallant-clever-dijkstra/mnt/outputs/kenexis-open-audit-writer"),
]
for cand in WRITER_CANDIDATES:
    if cand.exists():
        sys.path.insert(0, str(cand))
        break

try:
    import write_aud
    from write_aud import (
        new_audit, save, validate, add_question, find_category, find_element,
        verify_round_trip, NULL_SENTINEL, EMPTY_SENTINEL,
    )
except ImportError as e:
    print(
        f"ERROR: cannot import write_aud — make sure kenexis-open-audit-writer "
        f"is on PYTHONPATH. Tried: {[str(c) for c in WRITER_CANDIDATES]}",
        file=sys.stderr,
    )
    raise


# ----------------------------- Markdown parsing ---------------------------- #

H1_RE = re.compile(r"^#\s+(.+?)\s*$")
H2_RE = re.compile(r"^##\s+(.+?)\s*$")
H3_RE = re.compile(r"^###\s+(.+?)\s*$")
TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|(\s*[:\-]+\s*\|)+\s*$")
MODULE_PREFIX_RE = re.compile(r"^Module\s+\d+\s*[—–-]\s*", re.IGNORECASE)


def _split_row(line):
    """Split a markdown table row into trimmed cells."""
    m = TABLE_ROW_RE.match(line)
    if not m:
        return None
    inner = m.group(1)
    return [c.strip() for c in inner.split("|")]


def _is_separator_row(line):
    return bool(TABLE_SEP_RE.match(line))


def _consume_table(lines, start):
    """Starting at lines[start] (which must be the header row of a table),
    return (headers, data_rows, end_index) where end_index is the line after
    the last consumed row. Returns None if no valid table starts here."""
    headers = _split_row(lines[start])
    if headers is None:
        return None
    if start + 1 >= len(lines) or not _is_separator_row(lines[start + 1]):
        return None
    data = []
    i = start + 2
    while i < len(lines):
        row = _split_row(lines[i])
        if row is None:
            break
        data.append(row)
        i += 1
    return headers, data, i


def _normalize_header(h):
    """Lowercase, strip non-alphanumerics for fuzzy header matching."""
    return re.sub(r"[^a-z0-9]+", "", h.lower())


# Header tokens we recognize as evidence of a "question table." A table is
# considered a protocol-question table if it includes a Question column AND at
# least one of (ID / Weight / Assessor Guidance).
_QUESTION_HEADER_TOKENS = {
    "question", "questiontext", "criterion", "criteriaquestion",
}
_OTHER_HEADER_TOKENS = {
    "id",
    "weight", "wt", "priority",
    "assessorguidance", "guidance", "assessmentguidance",
}


def _is_question_table(headers):
    """Return True if the headers look like a protocol-question table."""
    norm = {_normalize_header(h) for h in headers}
    has_question_col = bool(norm & _QUESTION_HEADER_TOKENS)
    has_supporting_col = bool(norm & _OTHER_HEADER_TOKENS)
    return has_question_col and has_supporting_col


def _strip_module_prefix(text):
    return MODULE_PREFIX_RE.sub("", text).strip()


def parse_protocol(path):
    """Parse a protocol markdown file into a structured dict.

    Returns:
        {
            'title': str,           # from the first H1 if present, else basename
            'categories': [
                {
                    'name': str,
                    'elements': [
                        {
                            'name': str,
                            'questions': [
                                {
                                    'protocol_id': str,
                                    'question': str,
                                    'eval': str,           # may be ''
                                    'weight': str,         # raw value as string
                                    'assessor_guidance': str,
                                    'extra': dict,         # any additional columns
                                },
                                ...
                            ],
                        },
                        ...
                    ],
                },
                ...
            ],
        }
    """
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    lines = text.splitlines()

    title = Path(path).stem
    categories = []

    i = 0
    current_cat = None       # dict
    current_h3 = None        # the active H3 element if any
    pending_h2_no_table = False

    def _new_category(name):
        return {
            "name": _strip_module_prefix(name),
            "elements": [],   # populated lazily
        }

    while i < len(lines):
        line = lines[i]

        m = H1_RE.match(line)
        if m and not categories:  # only first H1 counts as title
            title = m.group(1).strip()
            i += 1
            continue

        m = H2_RE.match(line)
        if m:
            # Look ahead: does a *question* table follow before the next H2?
            j = i + 1
            has_question_table = False
            while j < len(lines) and not H2_RE.match(lines[j]):
                if TABLE_ROW_RE.match(lines[j]) and not _is_separator_row(lines[j]):
                    headers = _split_row(lines[j])
                    if headers and j + 1 < len(lines) and _is_separator_row(lines[j + 1]):
                        if _is_question_table(headers):
                            has_question_table = True
                            break
                j += 1
            if has_question_table:
                current_cat = _new_category(m.group(1))
                categories.append(current_cat)
                current_h3 = None
            else:
                current_cat = None
            i += 1
            continue

        m = H3_RE.match(line)
        if m and current_cat is not None:
            current_h3 = {"name": m.group(1).strip(), "questions": []}
            current_cat["elements"].append(current_h3)
            i += 1
            continue

        # Try to consume a table starting here
        if current_cat is not None and TABLE_ROW_RE.match(line) and not _is_separator_row(line):
            tbl = _consume_table(lines, i)
            if tbl:
                headers, rows, end = tbl
                # Only consume the table if it really is a question table.
                # Non-question tables (e.g., disposition rules in a trailer
                # section) are skipped to avoid creating empty rows.
                if not _is_question_table(headers):
                    i = end
                    continue
                # If there's no active H3, attach questions to a default element.
                if current_h3 is None:
                    if not current_cat["elements"]:
                        current_cat["elements"].append({"name": None, "questions": []})
                    target = current_cat["elements"][0]
                else:
                    target = current_h3
                for row in rows:
                    q = _row_to_question(headers, row)
                    if q:
                        target["questions"].append(q)
                i = end
                continue
        i += 1

    return {"title": title, "categories": categories}


def _row_to_question(headers, row):
    """Convert a markdown table row + headers into a question dict.
    Returns None if the row is empty/blank."""
    if not any(c.strip() for c in row):
        return None
    norm = [_normalize_header(h) for h in headers]
    # Pad row to match header length
    if len(row) < len(headers):
        row = row + [""] * (len(headers) - len(row))
    elif len(row) > len(headers):
        row = row[: len(headers)]

    by = dict(zip(norm, row))

    def get_any(*keys, default=""):
        for k in keys:
            if k in by and by[k]:
                return by[k].strip()
        return default

    q = {
        "protocol_id": get_any("id"),
        "question": get_any("question", "questiontext", "criterion", "criteriaquestion"),
        "eval": get_any("eval", "evaluation", "evaltype"),
        "weight": get_any("weight", "wt", "priority"),
        "assessor_guidance": get_any("assessorguidance", "guidance", "audit", "assessmentguidance"),
        "extra": {},
    }
    # Keep any columns we didn't recognize as extras (informational).
    consumed = {"id", "question", "questiontext", "criterion", "criteriaquestion",
                "eval", "evaluation", "evaltype", "weight", "wt", "priority",
                "assessorguidance", "guidance", "audit", "assessmentguidance"}
    for k, v in by.items():
        if k not in consumed and v:
            q["extra"][k] = v.strip()
    if not q["question"]:
        return None
    return q


# --------------------------- Weight mapping -------------------------------- #

DEFAULT_WEIGHT_MAP = {
    "5": "Critical",
    "4": "Important",
    "3": "Helpful",
    "2": "Optional",
    "1": "Optional",
}


def parse_weight_map(spec):
    """Parse '5=Critical,4=Important,...' into a dict."""
    if not spec:
        return DEFAULT_WEIGHT_MAP
    out = {}
    for part in spec.split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


# --------------------------- Audit construction --------------------------- #

def build_audit_data(protocol, *, study_name=None, facility="", facility_owner="",
                     facility_location="", project_number="", project_description="",
                     report_number="", unit="", coordinator_name="",
                     coordinator_email="", general_notes="", ds_rev=6,
                     single_element_name="Audit Questions",
                     weight_map=None):
    """Construct an .aud data dict from a parsed protocol structure."""
    if study_name is None:
        study_name = protocol.get("title", "Untitled Audit")

    weight_map = weight_map or DEFAULT_WEIGHT_MAP
    data = new_audit(
        study_name=study_name,
        facility=facility,
        facility_owner=facility_owner,
        facility_location=facility_location,
        project_number=project_number,
        project_description=project_description,
        report_number=report_number,
        unit=unit,
        coordinator_name=coordinator_name,
        coordinator_email=coordinator_email,
        general_notes=general_notes,
        ds_rev=ds_rev,
    )

    # Build a code -> ID map for weighting factors so we can reference by code.
    wf_by_code = {wf["Weighting_Factor_Code"]: wf["ID"] for wf in data["Weighting_Factors"]}

    # For each category in the protocol, create a Category in the data.
    from collections import OrderedDict
    for cat in protocol["categories"]:
        cat_id = write_aud.make_id()
        category_record = OrderedDict([
            ("ID", cat_id),
            ("Category_Description", cat["name"]),
            ("Session_IDs", [OrderedDict([("ID", EMPTY_SENTINEL)])]),
            ("Elements", []),
        ])
        data["Categories"].append(category_record)

        # Decide on element shape: if every element in protocol has a name (i.e.
        # H3 sub-sections were used), respect them. If the only element is a
        # default-unnamed one, use single_element_name.
        elements = cat["elements"]
        if not elements:
            elements = [{"name": None, "questions": []}]

        for elem in elements:
            elem_id = write_aud.make_id()
            elem_name = elem["name"] if elem["name"] else single_element_name
            element_record = OrderedDict([
                ("ID", elem_id),
                ("Element_Description", elem_name),
                ("Questions", []),
            ])
            category_record["Elements"].append(element_record)

            # Add each question via the writer's add_question helper for safety.
            for q in elem["questions"]:
                # Prefix the protocol ID for traceability.
                qtext = q["question"]
                if q["protocol_id"]:
                    qtext = f"[{q['protocol_id']}] {qtext}"

                # Compose Assessor_Guidance: optional [Evaluation: ...] tag,
                # then the protocol's guidance text, then any extra columns.
                # Assessor_Notes stays EMPTY at scaffold time — that field is
                # reserved for the assessor to fill in during the audit.
                guidance_parts = []
                if q["eval"]:
                    guidance_parts.append(f"[Evaluation: {q['eval']}]")
                if q["assessor_guidance"]:
                    guidance_parts.append(q["assessor_guidance"])
                if q["extra"]:
                    extras = "; ".join(f"{k}: {v}" for k, v in q["extra"].items())
                    guidance_parts.append(f"[Additional protocol metadata — {extras}]")
                assessor_guidance = " ".join(guidance_parts).strip()

                # Weight → Weighting_Factor_ID
                weight_raw = q["weight"]
                wf_code = weight_map.get(weight_raw)
                if wf_code is None and weight_raw:
                    raise ValueError(
                        f"Weight {weight_raw!r} on question {q['protocol_id'] or q['question'][:40]!r} "
                        f"not in weight map {list(weight_map.keys())}. "
                        f"Pass --weight-map to override."
                    )
                wf_id = wf_by_code.get(wf_code) if wf_code else None

                add_question(
                    data,
                    category_id=cat_id,
                    element_id=elem_id,
                    question_text=qtext,
                    assessor_guidance=assessor_guidance,
                    assessor_notes="",   # Reserved for assessor — never populated here.
                    scoring_criterion_id=None,  # left unset; assessor selects
                    weighting_factor_id=wf_id,
                    reference_documents=None,
                )

    return data


# --------------------------------- CLI ------------------------------------- #

def _summarize(protocol, data):
    cats = len(data["Categories"])
    qs = sum(
        len(el.get("Questions", []))
        for cat in data["Categories"]
        for el in cat.get("Elements", [])
    )
    # Weight distribution
    from collections import Counter
    wf_lookup = {wf["ID"]: wf["Weighting_Factor_Code"] for wf in data["Weighting_Factors"]}
    dist = Counter()
    for cat in data["Categories"]:
        for el in cat.get("Elements", []):
            for q in el.get("Questions", []):
                wid = q.get("Weighting_Factor_ID")
                if wid and wid != NULL_SENTINEL:
                    dist[wf_lookup.get(wid, "?")] += 1
                else:
                    dist["unset"] += 1
    return cats, qs, dict(dist)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--protocol", required=True, help="Path to the protocol markdown")
    p.add_argument("--output", required=True, help="Path for the new .aud file")
    p.add_argument("--study-name")
    p.add_argument("--facility", default="")
    p.add_argument("--facility-owner", default="")
    p.add_argument("--facility-location", default="")
    p.add_argument("--project-number", default="")
    p.add_argument("--project-description", default="")
    p.add_argument("--report-number", default="")
    p.add_argument("--unit", default="")
    p.add_argument("--coordinator", default="")
    p.add_argument("--coordinator-email", default="")
    p.add_argument("--general-notes", default="")
    p.add_argument("--ds-rev", type=int, default=6)
    p.add_argument("--single-element-name", default="Audit Questions")
    p.add_argument("--weight-map", default="")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    protocol = parse_protocol(args.protocol)
    weight_map = parse_weight_map(args.weight_map)

    data = build_audit_data(
        protocol,
        study_name=args.study_name,
        facility=args.facility,
        facility_owner=args.facility_owner,
        facility_location=args.facility_location,
        project_number=args.project_number,
        project_description=args.project_description,
        report_number=args.report_number,
        unit=args.unit,
        coordinator_name=args.coordinator,
        coordinator_email=args.coordinator_email,
        general_notes=args.general_notes,
        ds_rev=args.ds_rev,
        single_element_name=args.single_element_name,
        weight_map=weight_map,
    )

    cats, qs, dist = _summarize(protocol, data)
    print(f"Parsed protocol: {protocol['title']}")
    print(f"  Categories: {cats}")
    print(f"  Questions:  {qs}")
    print(f"  Weight distribution: {dist}")

    issues = validate(data)
    errors = [i for i in issues if i["severity"] == "error"]
    if errors:
        print(f"\nValidation FAILED with {len(errors)} error(s):")
        for e in errors:
            print(f"  - {e['path']}: {e['message']}")
        sys.exit(1)

    if args.dry_run:
        print("\n--dry-run: not writing file.")
        return

    save(data, args.output)
    rt = verify_round_trip(args.output, data)
    print(f"\nSaved {args.output} ({rt['file_size']} bytes)")
    print(f"Round-trip OK: {rt['ok']}")


if __name__ == "__main__":
    main()
