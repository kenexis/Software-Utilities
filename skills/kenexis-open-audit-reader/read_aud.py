#!/usr/bin/env python3
"""
read_aud.py — Kenexis Open-Audit (.aud) reader

Loads a .aud file as JSON, preserving every byte verbatim for round-trip writes
elsewhere, and exposes a resolved, foreign-key-linked view of the contents for
downstream skills (HAZOP audit, SIS audit, report generators, etc.).

Sentinel conventions in the file:
    - JSON null does not appear; the string "null" is used for "no value selected."
    - The string "empty" on an ID field marks a placeholder/seed record.
    - Empty string "" is its own thing — "user has not filled this in yet."

Round-trip rule: never modify the underlying data. The reader translates
sentinels to Python None inside its *resolved* views only; .data and the .raw
field on each resolved item retain the original bytes.

CLI usage:
    python3 read_aud.py FILE.aud --summary
    python3 read_aud.py FILE.aud --questions
    python3 read_aud.py FILE.aud --findings
    python3 read_aud.py FILE.aud --orphans
    python3 read_aud.py FILE.aud --raw

Programmatic usage:
    from read_aud import load, Audit, is_unset, is_null_sentinel, is_empty_id
    audit = load("study.aud")
    for q in audit.questions():
        ...
"""
import argparse
import json
import sys
from pathlib import Path


# ----------------------------- Sentinel predicates ------------------------- #

NULL_SENTINEL = "null"
EMPTY_SENTINEL = "empty"


def is_null_sentinel(value):
    """True only for the string 'null' that the format uses as a no-value marker."""
    return value == NULL_SENTINEL


def is_empty_id(value):
    """True only for the string 'empty' that the format uses as an ID placeholder."""
    return value == EMPTY_SENTINEL


def is_unset(value):
    """True when a field is unset by any of the format's conventions.

    Catches: None, '', 'null', 'empty'. Use this for 'is this field meaningfully
    populated?' tests in consumer code.
    """
    if value is None:
        return True
    if value == "":
        return True
    if value == NULL_SENTINEL:
        return True
    if value == EMPTY_SENTINEL:
        return True
    return False


def _denull(value):
    """Internal: translate the string 'null' to Python None for resolved views.
    Leaves all other values (including '' and 'empty') alone."""
    return None if value == NULL_SENTINEL else value


# ------------------------------- File loading ------------------------------ #

def load(path):
    """Load a .aud file and return an Audit wrapper.

    Preserves key insertion order. Verifies that the file round-trips byte-for-byte
    when re-serialized with the canonical encoder; if it doesn't, prints a
    warning to stderr (this either means the file is from a newer datastore
    revision or has been hand-edited)."""
    p = Path(path)
    raw_bytes = p.read_bytes()
    text = raw_bytes.decode("utf-8")
    data = json.loads(text)

    # Round-trip self-check — best-effort, non-fatal.
    canonical = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    if canonical != text:
        print(
            f"WARNING: {path} does not round-trip with the canonical encoder. "
            "This may indicate a new datastore revision, hand-edits, or unusual "
            "key ordering. Proceeding, but write-back may produce diffs.",
            file=sys.stderr,
        )

    return Audit(data, source_path=str(p))


# -------------------------------- Audit class ------------------------------ #

class Audit:
    """Wrapper around the parsed .aud document.

    `self.data` is the verbatim parsed object — pass it to the writer skill for
    round-trip writes. The methods on this class expose resolved, denormalized
    views suitable for analysis; they do not mutate `self.data`."""

    def __init__(self, data, source_path=None):
        self.data = data
        self.source_path = source_path

    # ------------------- Top-level pass-through accessors ------------------ #

    @property
    def overview(self):
        return dict(self.data.get("Overview", {}))

    @property
    def settings(self):
        return self.data.get("Settings", {})

    @property
    def ds_rev(self):
        return self.settings.get("Ds_Rev")

    def team_members(self):
        return [tm for tm in self.data.get("Team_Members", []) if not is_empty_id(tm.get("ID"))]

    def sessions(self):
        return [s for s in self.data.get("Sessions", []) if not is_empty_id(s.get("ID"))]

    def scoring_criteria(self):
        return list(self.data.get("Scoring_Criterias", []))

    def weighting_factors(self):
        return list(self.data.get("Weighting_Factors", []))

    def parking_lot(self):
        return [r for r in self.data.get("Parking_Lot", []) if not is_empty_id(r.get("ID"))]

    def drawings(self):
        return [r for r in self.data.get("Drawings", []) if not is_empty_id(r.get("ID"))]

    def evidences(self):
        return [r for r in self.data.get("Evidences", []) if not is_empty_id(r.get("ID"))]

    def revalidation_history(self):
        return [r for r in self.data.get("Revalidation_History", []) if not is_empty_id(r.get("ID"))]

    # ------------------------- ID resolution helpers ----------------------- #

    def _index(self, collection_key, id_key="ID"):
        """Build a dict from ID -> record for a collection."""
        return {r[id_key]: r for r in self.data.get(collection_key, []) if id_key in r}

    def resolve_scoring_criterion(self, sc_id):
        if is_unset(sc_id):
            return None
        idx = self._index("Scoring_Criterias")
        rec = idx.get(sc_id)
        if not rec:
            return None
        return {
            "id": rec.get("ID"),
            "description": rec.get("Scoring_Criteria_Description", ""),
            "level": rec.get("Scoring_Criteria_Level", ""),
            "notes": rec.get("Scoring_Criteria_Assessor_Notes", ""),
        }

    def resolve_weighting_factor(self, wf_id):
        if is_unset(wf_id):
            return None
        idx = self._index("Weighting_Factors")
        rec = idx.get(wf_id)
        if not rec:
            return None
        return {
            "id": rec.get("ID"),
            "code": rec.get("Weighting_Factor_Code", ""),
            "description": rec.get("Weighting_Factor_Description", ""),
            "score": rec.get("Weighting_Factor_Score", ""),
        }

    def resolve_evidence(self, ev_id):
        if is_unset(ev_id):
            return None
        idx = self._index("Evidences")
        rec = idx.get(ev_id)
        return rec.get("Evidence", "") if rec else None

    def resolve_recommendation(self, rec_id):
        if is_unset(rec_id):
            return None
        idx = self._index("Assessor_Recommendations")
        rec = idx.get(rec_id)
        if not rec:
            return None
        return {
            "id": rec.get("ID"),
            "text": rec.get("Assessor_Recommendation", ""),
            "priority": _denull(rec.get("Assessor_Recommendation_Priority")),
            "responsible_party": rec.get("Assessor_Recommendation_Responsible_Party", ""),
            "status": _denull(rec.get("Assessor_Recommendation_Status")),
            "comments": rec.get("Assessor_Recommendation_Comments", ""),
        }

    def resolve_team_member(self, tm_id):
        if is_unset(tm_id):
            return None
        idx = self._index("Team_Members")
        rec = idx.get(tm_id)
        return rec

    def resolve_session(self, s_id):
        if is_unset(s_id):
            return None
        idx = self._index("Sessions")
        rec = idx.get(s_id)
        return rec

    # ------------------------- Hierarchical views -------------------------- #

    def categories(self):
        """Return categories with elements and questions kept in tree shape.
        Each question is left in raw form here — use questions() for resolved view."""
        out = []
        for cat in self.data.get("Categories", []):
            if is_empty_id(cat.get("ID")):
                continue
            out.append({
                "id": cat["ID"],
                "description": cat.get("Category_Description", ""),
                "session_ids": [s.get("ID") for s in cat.get("Session_IDs", []) if not is_empty_id(s.get("ID"))],
                "elements": [
                    {
                        "id": el["ID"],
                        "description": el.get("Element_Description", ""),
                        "questions": list(el.get("Questions", [])),
                    }
                    for el in cat.get("Elements", [])
                    if not is_empty_id(el.get("ID"))
                ],
            })
        return out

    def questions(self):
        """Flat, fully resolved iterator over every question in the document.

        Each entry is a dict with the question text, category and element labels,
        scoring criterion record, weighting factor record, list of evidence text,
        list of recommendation records, list of reference document/clauses, and
        the verbatim original record under 'raw'."""
        results = []
        for cat in self.data.get("Categories", []):
            if is_empty_id(cat.get("ID")):
                continue
            cat_label = cat.get("Category_Description", "")
            for el in cat.get("Elements", []):
                if is_empty_id(el.get("ID")):
                    continue
                el_label = el.get("Element_Description", "")
                for q in el.get("Questions", []):
                    if is_empty_id(q.get("ID")):
                        continue
                    evidence_ids = [e.get("ID") for e in q.get("Evidence_IDs", [])]
                    rec_ids = [r.get("ID") for r in q.get("Assessor_Recommendation_IDs", [])]
                    refs = []
                    for rd in q.get("Reference_Documents", []):
                        if is_empty_id(rd.get("ID")):
                            continue
                        refs.append({
                            "name": rd.get("Reference_Document_Description", ""),
                            "clauses": [c.get("Clause_Description", "") for c in rd.get("Clauses", []) if not is_empty_id(c.get("ID"))],
                        })
                    resolved = {
                        "id": q.get("ID"),
                        "category": cat_label,
                        "element": el_label,
                        "question_text": q.get("Question_Description", ""),
                        "assessor_notes": q.get("Assessor_Notes", ""),
                        "assessor_guidance": q.get("Assessor_Guidance", ""),
                        "scoring_criterion": self.resolve_scoring_criterion(q.get("Scoring_Criteria_ID")),
                        "selected_score": q.get("Selected_Score", ""),
                        "weighting_factor": self.resolve_weighting_factor(q.get("Weighting_Factor_ID")),
                        "selected_weighting_factor": q.get("Selected_Weighting_Factor", ""),
                        "weighted_score": q.get("Weighted_Score", ""),
                        "evidence": [self.resolve_evidence(eid) for eid in evidence_ids if not is_unset(eid)],
                        "recommendations": [self.resolve_recommendation(rid) for rid in rec_ids if not is_unset(rid)],
                        "reference_documents": refs,
                        "findings": q.get("Findings", ""),
                        "raw": q,
                    }
                    # Filter Nones from resolution failures (orphan FKs) but keep informational.
                    resolved["evidence"] = [e for e in resolved["evidence"] if e is not None]
                    resolved["recommendations"] = [r for r in resolved["recommendations"] if r is not None]
                    results.append(resolved)
        return results

    # ----------------------- Attendance / orphans -------------------------- #

    def attendance(self, include_orphans=False):
        """Resolved attendance rows. Each entry has team_member and session
        labels; orphans (foreign key not in roster/calendar) are omitted by
        default."""
        members = self._index("Team_Members")
        sessions = self._index("Sessions")
        out = []
        for row in self.data.get("Team_Members_Sessions", []):
            tm_id = row.get("Team_Member_ID")
            s_id = row.get("Session_ID")
            tm = members.get(tm_id)
            s = sessions.get(s_id)
            is_orphan = (tm is None) or (s is None)
            if is_orphan and not include_orphans:
                continue
            out.append({
                "id": row.get("ID"),
                "team_member_id": tm_id,
                "team_member_name": tm.get("Name") if tm else None,
                "session_id": s_id,
                "session_label": s.get("Session") if s else None,
                "value": _denull(row.get("Value")),
                "is_orphan": is_orphan,
                "raw": row,
            })
        return out

    def orphans(self):
        """Return the orphan attendance rows for review."""
        return [a for a in self.attendance(include_orphans=True) if a["is_orphan"]]

    # ------------------- Recommendations ----------------------------------- #

    def assessor_recommendations(self):
        out = []
        for rec in self.data.get("Assessor_Recommendations", []):
            if is_empty_id(rec.get("ID")):
                continue
            out.append({
                "id": rec.get("ID"),
                "text": rec.get("Assessor_Recommendation", ""),
                "priority": _denull(rec.get("Assessor_Recommendation_Priority")),
                "responsible_party": rec.get("Assessor_Recommendation_Responsible_Party", ""),
                "status": _denull(rec.get("Assessor_Recommendation_Status")),
                "comments": rec.get("Assessor_Recommendation_Comments", ""),
                "raw": rec,
            })
        return out

    # ----------------------------- Visibility ------------------------------ #

    def hidden_fields(self):
        """Return a dict mapping (collection_path, field_name) -> True for
        fields the GUI is currently hiding. The collection_path is dotted for
        nested cases (e.g. 'Categories.Elements.Questions'). Informational
        only — the reader has loaded all fields regardless of visibility."""
        cv = self.settings.get("Column_Visibility", {})
        hidden = {}

        def walk(path_segments, node):
            if not isinstance(node, dict):
                return
            for k, v in node.items():
                if isinstance(v, dict):
                    # A nested *_Children entry. Strip the suffix for the path label.
                    if k.endswith("_Children"):
                        next_seg = k[: -len("_Children")]
                        walk(path_segments + [next_seg], v)
                    else:
                        # Unusual shape — descend with the literal key.
                        walk(path_segments + [k], v)
                elif v is False:
                    hidden[(".".join(path_segments), k)] = True

        for top_key, top_val in cv.items():
            if top_key.endswith("_Children") and isinstance(top_val, dict):
                base = top_key[: -len("_Children")]
                walk([base], top_val)

        return hidden

    def visible(self, collection, field):
        """True if the GUI currently shows this field. Defaults to True when
        the visibility tree does not mention the field at all (those fields are
        always-on structural columns)."""
        cv = self.settings.get("Column_Visibility", {})
        children = cv.get(f"{collection}_Children")
        if not isinstance(children, dict):
            return True
        if field not in children:
            return True
        return bool(children[field])


# ------------------------------ CLI entrypoint ----------------------------- #

def _print_summary(audit):
    ov = audit.overview
    qs = audit.questions()
    findings = [q for q in qs if q["findings"].strip()]
    scored = [q for q in qs if not is_unset(q["selected_score"])]
    print("=" * 72)
    print(f"Study: {ov.get('Study_Name', '(unnamed)')}")
    print(f"Facility: {ov.get('Facility', '')} ({ov.get('Facility_Owner', '')})")
    print(f"Project: {ov.get('Project_Number', '')} — {ov.get('Project_Description', '')}")
    print(f"Coordinator: {ov.get('Study_Coordinator', '')}  <{ov.get('Study_Coordinator_Contact_Info', '')}>")
    print(f"Datastore revision (Ds_Rev): {audit.ds_rev}")
    print("-" * 72)
    print(f"Categories:        {len(audit.categories())}")
    print(f"Questions:         {len(qs)}")
    print(f"  scored:          {len(scored)}")
    print(f"  with findings:   {len(findings)}")
    print(f"Team members:      {len(audit.team_members())}")
    print(f"Sessions:          {len(audit.sessions())}")
    print(f"Recommendations:   {len(audit.assessor_recommendations())}")
    print(f"Parking lot items: {len(audit.parking_lot())}")
    print(f"Drawings:          {len(audit.drawings())}")
    print(f"Evidence items:    {len(audit.evidences())}")
    print(f"Orphan attendance: {len(audit.orphans())}")
    print("-" * 72)
    hidden = audit.hidden_fields()
    if hidden:
        print(f"Currently hidden GUI fields ({len(hidden)}):")
        for (path, field) in sorted(hidden.keys()):
            print(f"  - {path}.{field}")
    else:
        print("All fields visible in GUI.")
    print("=" * 72)


def _print_questions(audit):
    for q in audit.questions():
        print(json.dumps({
            "id": q["id"],
            "category": q["category"],
            "element": q["element"],
            "question": q["question_text"],
            "score": q["selected_score"],
            "criterion": q["scoring_criterion"]["description"] if q["scoring_criterion"] else None,
            "findings": q["findings"],
        }, ensure_ascii=False))


def _print_findings(audit):
    for q in audit.questions():
        if q["findings"].strip():
            print(f"[{q['category']} / {q['element']}]")
            print(f"  Q: {q['question_text']}")
            print(f"  Findings: {q['findings']}")
            print()


def _print_orphans(audit):
    for o in audit.orphans():
        print(json.dumps(o["raw"], ensure_ascii=False))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("path", help="Path to the .aud file")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--summary", action="store_true")
    g.add_argument("--questions", action="store_true")
    g.add_argument("--findings", action="store_true")
    g.add_argument("--orphans", action="store_true")
    g.add_argument("--raw", action="store_true", help="Pretty-print full data (debug only)")
    args = p.parse_args(argv)

    audit = load(args.path)
    if args.questions:
        _print_questions(audit)
    elif args.findings:
        _print_findings(audit)
    elif args.orphans:
        _print_orphans(audit)
    elif args.raw:
        print(json.dumps(audit.data, indent=2, ensure_ascii=False))
    else:
        _print_summary(audit)


if __name__ == "__main__":
    main()
