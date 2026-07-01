#!/usr/bin/env python3
"""
read_opha.py — Kenexis Open-PHA (.opha) reader.

Loads an Open-PHA study file WITHOUT mutating any of its content, and exposes
typed, convenient views over it: the worksheet tree (Nodes -> Deviations ->
Causes -> Consequences), the flat safeguard/recommendation libraries, the team
roster, and the risk matrix. Reference-ID lists are resolved back to the flat
records they point at.

Design contract (see open-pha-format-review.md §13):
  * Parse preserving key insertion order.
  * Do NOT decode entities, strip whitespace, or coerce sentinels.
  * String "null"/"empty"/"true"/"false" and JSON null/true/false are all kept
    exactly as stored. Interpretation is offered through predicates, never by
    mutating the data.
  * Quality bar: study.round_trip_ok() is True — re-serializing the parse is
    byte-identical to the file on disk.

Typical use:
    from read_opha import load
    study = load("some.opha")
    assert study.round_trip_ok()
    for ctx in study.iter_consequences():
        print(ctx.node["Node_Description"], "->", ctx.consequence["Consequence"])
        for sg in study.resolve_safeguards(ctx.consequence):
            print("   safeguard:", sg["Safeguard"])
"""
import json
from collections import OrderedDict, namedtuple

NULL_SENTINEL = "null"
EMPTY_SENTINEL = "empty"

# A flattened worksheet row: the four containment levels plus their indices.
ConsequenceContext = namedtuple(
    "ConsequenceContext",
    ["node", "deviation", "cause", "consequence",
     "node_idx", "deviation_idx", "cause_idx", "consequence_idx"],
)


# --------------------------------------------------------------------------- #
# Sentinel predicates (interpret without mutating)
# --------------------------------------------------------------------------- #

def is_null_sentinel(v):
    """True for the string "null" OR real JSON null — both mean 'no value'."""
    return v is None or v == NULL_SENTINEL


def is_empty_sentinel(v):
    """True when v is (or wraps) the "empty" record sentinel."""
    if v == EMPTY_SENTINEL:
        return True
    if isinstance(v, dict):
        return v.get("ID") == EMPTY_SENTINEL
    return False


def is_unset(v):
    """True when a field carries no meaningful value: '', 'null', JSON null,
    or the 'empty' sentinel."""
    return v in ("", EMPTY_SENTINEL) or is_null_sentinel(v)


def as_tristate(v):
    """Interpret a string tri-state boolean ('true'/'false'/'null'/'') as
    True / False / None. Also accepts real JSON booleans."""
    if isinstance(v, bool):
        return v
    if v == "true":
        return True
    if v == "false":
        return False
    return None  # "null", "", or anything else -> unset


def ids_in(ref_list):
    """Extract the real IDs from a wrapped reference list, dropping 'empty'."""
    if not isinstance(ref_list, list):
        return []
    return [r.get("ID") for r in ref_list
            if isinstance(r, dict) and r.get("ID") not in (EMPTY_SENTINEL, None)]


# --------------------------------------------------------------------------- #
# Loader
# --------------------------------------------------------------------------- #

def load(path):
    """Load an .opha file and return a PhaStudy wrapper."""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    data = json.loads(raw, object_pairs_hook=OrderedDict)
    return PhaStudy(data, raw)


def loads(raw):
    """Parse .opha content from a string."""
    data = json.loads(raw, object_pairs_hook=OrderedDict)
    return PhaStudy(data, raw)


class PhaStudy:
    """A non-mutating accessor over a parsed .opha document."""

    def __init__(self, data, raw=None):
        self.data = data
        self.raw = raw

    # -- integrity --------------------------------------------------------- #

    def round_trip_ok(self):
        """True when compact re-serialization equals the original bytes.

        This is the format invariant: a correct reader never loses fidelity."""
        if self.raw is None:
            return None
        compact = json.dumps(self.data, separators=(",", ":"), ensure_ascii=False)
        return compact == self.raw

    # -- top-level views --------------------------------------------------- #

    @property
    def overview(self):
        return self.data.get("Overview", {})

    @property
    def settings(self):
        return self.data.get("Settings", {})

    @property
    def analysis_mode(self):
        return self.settings.get("Analysis_Mode")

    @property
    def lopa_mode(self):
        return self.settings.get("Lopa_Mode")

    @property
    def ds_rev(self):
        return self.settings.get("Ds_Rev")

    def _real_records(self, collection):
        """Records of a collection excluding the 'empty' seed placeholder."""
        return [r for r in self.data.get(collection, [])
                if r.get("ID") != EMPTY_SENTINEL]

    def team_members(self):
        return self._real_records("Team_Members")

    def sessions(self):
        return self._real_records("Sessions")

    def drawings(self):
        return self._real_records("Drawings")

    def nodes(self):
        return self._real_records("Nodes")

    def safeguards(self):
        return self._real_records("Safeguards")

    def pha_recommendations(self):
        return self._real_records("Pha_Recommendations")

    def lopa_recommendations(self):
        return self._real_records("Lopa_Recommendations")

    def pha_comments(self):
        return self._real_records("Pha_Comments")

    def lopa_comments(self):
        return self._real_records("Lopa_Comments")

    def check_lists(self):
        return self._real_records("Check_Lists")

    def scais(self):
        return self._real_records("Scais")

    # -- indexes ----------------------------------------------------------- #

    def _index(self, collection):
        return {r["ID"]: r for r in self.data.get(collection, [])
                if r.get("ID") not in (EMPTY_SENTINEL, None)}

    def safeguard_index(self):
        return self._index("Safeguards")

    def pha_recommendation_index(self):
        return self._index("Pha_Recommendations")

    def lopa_recommendation_index(self):
        return self._index("Lopa_Recommendations")

    def team_member_index(self):
        return self._index("Team_Members")

    # -- worksheet traversal ---------------------------------------------- #

    def iter_consequences(self):
        """Yield a ConsequenceContext for every consequence in the worksheet,
        flattening the Nodes -> Deviations -> Causes -> Consequences tree."""
        for ni, node in enumerate(self.nodes()):
            for di, dev in enumerate(node.get("Deviations", [])):
                for ci, cause in enumerate(dev.get("Causes", [])):
                    for qi, con in enumerate(cause.get("Consequences", [])):
                        yield ConsequenceContext(node, dev, cause, con,
                                                 ni, di, ci, qi)

    def resolve_safeguards(self, consequence):
        idx = self.safeguard_index()
        return [idx[i] for i in ids_in(consequence.get("Safeguard_IDs")) if i in idx]

    def resolve_pha_recommendations(self, consequence):
        idx = self.pha_recommendation_index()
        return [idx[i] for i in ids_in(consequence.get("Pha_Recommendation_IDs")) if i in idx]

    def resolve_lopa_recommendations(self, consequence):
        idx = self.lopa_recommendation_index()
        return [idx[i] for i in ids_in(consequence.get("Lopa_Recommendation_IDs")) if i in idx]

    # -- risk matrix ------------------------------------------------------- #

    @property
    def risk_criteria(self):
        return self.data.get("Risk_Criteria", {})

    def severity_index(self):
        return {r["ID"]: r for r in self.risk_criteria.get("Severities", [])}

    def likelihood_index(self):
        return {r["ID"]: r for r in self.risk_criteria.get("Likelihoods", [])}

    def risk_rank_index(self):
        return {r["ID"]: r for r in self.risk_criteria.get("Risk_Rankings", [])}

    def risk_rank_of(self, consequence, stage=""):
        """Look up the Risk_Rankings record for a consequence at a stage.
        stage in {'', '_Before_Safeguards', '_After_Recommendations'}."""
        rid = consequence.get("Risk_Rank_ID" + stage)
        return self.risk_rank_index().get(rid)

    # -- summary ----------------------------------------------------------- #

    def summary(self):
        n_dev = sum(len(n.get("Deviations", [])) for n in self.nodes())
        n_cause = sum(len(c.get("Causes", []))
                      for n in self.nodes() for c in n.get("Deviations", []))
        n_con = sum(1 for _ in self.iter_consequences())
        return OrderedDict([
            ("study_name", self.overview.get("Study_Name")),
            ("analysis_mode", self.analysis_mode),
            ("lopa_mode", self.lopa_mode),
            ("ds_rev", self.ds_rev),
            ("nodes", len(self.nodes())),
            ("deviations", n_dev),
            ("causes", n_cause),
            ("consequences", n_con),
            ("safeguards", len(self.safeguards())),
            ("pha_recommendations", len(self.pha_recommendations())),
            ("drawings", len(self.drawings())),
            ("team_members", len(self.team_members())),
            ("round_trip_ok", self.round_trip_ok()),
        ])


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import sys
    study = load(sys.argv[1])
    for k, v in study.summary().items():
        print(f"{k:22s}: {v}")
