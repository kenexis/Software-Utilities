"""Tests for the Open-Audit writer and protocol builder skills. Run from the repo root:

    python -m unittest discover tests
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "kenexis-open-audit-writer"))
sys.path.insert(0, str(ROOT / "skills" / "kenexis-protocol-to-audit"))
sys.path.insert(0, str(ROOT / "skills" / "kenexis-open-audit-reader"))

import build_audit as b  # noqa: E402
import read_aud  # noqa: E402
import write_aud as w  # noqa: E402


def errors(data):
    return [i for i in w.validate(data) if i["severity"] == "error"]


def warnings(data):
    return [i["message"] for i in w.validate(data) if i["severity"] == "warning"]


def text_of(issues):
    return " | ".join(f"{i['path']}: {i['message']}" for i in issues)


def build():
    """Two scored-ready questions in one element."""
    d = w.new_audit(study_name="Study")
    cat = w.add_category(d, "Team")
    el = w.add_element(d, cat["ID"], "Roster")
    q1 = w.add_question(d, cat["ID"], el["ID"], "Chair named?")
    q2 = w.add_question(d, cat["ID"], el["ID"], "Scribe named?")
    return d, cat, q1, q2


def parse(tmp, text):
    path = os.path.join(tmp, "p.md")
    Path(path).write_text(text, encoding="utf-8")
    return b.parse_protocol(path)


class NewAudit(unittest.TestCase):
    def test_a_new_study_is_at_the_current_revision_and_valid(self):
        d = w.new_audit(study_name="S")
        self.assertEqual(w.current_ds_rev(), d["Settings"]["Ds_Rev"])
        self.assertIn("Column_Headers", d["Settings"])
        self.assertEqual([], errors(d))

    def test_two_studies_do_not_share_scoring_ids(self):
        a, b_ = w.new_audit(), w.new_audit()
        self.assertNotEqual(a["Scoring_Criterias"][0]["ID"], b_["Scoring_Criterias"][0]["ID"])

    def test_the_template_is_not_changed_by_use(self):
        before = json.dumps(w._template())
        d, _, q1, _ = build()
        w.score_question(d, q1, "Compliant")
        self.assertEqual(before, json.dumps(w._template()))

    def test_overview_text_is_encoded_like_the_application(self):
        d = w.new_audit(study_name="P&ID Review", facility="A <B>")
        self.assertEqual("P&amp;ID Review", d["Overview"]["Study_Name"])
        self.assertEqual("A &lt;B&gt;", d["Overview"]["Facility"])


class Scoring(unittest.TestCase):
    def test_a_score_sets_everything_that_depends_on_it(self):
        d, cat, q1, _ = build()
        q = w.score_question(d, q1, "Compliant", "Critical")
        self.assertEqual(("10", "10", "100"), (q["Selected_Score"], q["Selected_Weighting_Factor"], q["Weighted_Score"]))
        self.assertEqual(d["Scoring_Criterias"][0]["ID"], q["Scoring_Criteria_ID"])
        self.assertEqual(("1", "2", "50%", "10", "100"), tuple(cat[k] for k in w.CATEGORY_ROLLUPS))

    def test_an_unweighted_score_uses_the_level_as_the_weighted_score(self):
        d, _, q1, _ = build()
        self.assertEqual("3", w.score_question(d, q1, "Partially Compliant")["Weighted_Score"])

    def test_a_null_factor_leaves_the_weighted_score_blank_as_the_application_does(self):
        d, _, q1, _ = build()
        w.find_question(d, q1)["Weighting_Factor_ID"] = "null"
        self.assertEqual("", w.score_question(d, q1, "Compliant")["Weighted_Score"])

    def test_a_score_can_be_cleared(self):
        d, cat, q1, _ = build()
        w.score_question(d, q1, "Compliant")
        q = w.score_question(d, q1, "")
        self.assertEqual("", q["Selected_Score"])
        self.assertEqual(("0", "NaN"), (cat["Completed_Questions"], cat["Average_Score"]))

    def test_an_id_works_as_well_as_a_label(self):
        d, _, q1, _ = build()
        self.assertEqual("0", w.score_question(d, q1, d["Scoring_Criterias"][2]["ID"])["Selected_Score"])

    def test_an_unknown_label_names_the_choices(self):
        d, _, q1, _ = build()
        with self.assertRaisesRegex(ValueError, "Partially Compliant"):
            w.score_question(d, q1, "Mostly")

    def test_averages_round_halves_up_like_javascript(self):
        d, cat, q1, q2 = build()
        d["Scoring_Criterias"][0]["Scoring_Criteria_Level"] = "1"
        d["Scoring_Criterias"][1]["Scoring_Criteria_Level"] = "2"
        w.score_question(d, q1, "Compliant")
        w.score_question(d, q2, "Partially Compliant")
        self.assertEqual("2", cat["Average_Score"])   # 1.5 rounds to 2
        d["Scoring_Criterias"][0]["Scoring_Criteria_Level"] = "2"
        d["Scoring_Criterias"][1]["Scoring_Criteria_Level"] = "3"
        w.recalculate(d)
        self.assertEqual("3", cat["Average_Score"])   # 2.5 rounds to 3, not to even

    def test_a_visible_weighting_column_counts_completion_by_weighted_score(self):
        d, cat, q1, _ = build()
        columns = d["Settings"]["Column_Visibility"]["Categories_Children"]["Elements_Children"]["Questions_Children"]
        columns["Weighting_Factor_ID"] = True
        w.find_question(d, q1)["Weighting_Factor_ID"] = "null"
        w.score_question(d, q1, "Compliant")
        self.assertEqual("0", cat["Completed_Questions"])

    def test_a_non_numeric_level_scores_nan(self):
        d, _, q1, _ = build()
        d["Scoring_Criterias"][0]["Scoring_Criteria_Level"] = "n/a"
        self.assertEqual("NaN", w.score_question(d, q1, "Compliant")["Weighted_Score"])

    def test_a_level_with_trailing_text_averages_to_nan_not_a_crash(self):
        # The application's numeric test accepts "10abc", which then evaluates to NaN.
        d, cat, q1, _ = build()
        d["Scoring_Criterias"][0]["Scoring_Criteria_Level"] = "10abc"
        w.score_question(d, q1, "Compliant")
        self.assertEqual("NaN", cat["Average_Score"])

    def test_add_question_scores_what_it_is_given(self):
        d, cat, _, _ = build()
        el = cat["Elements"][0]
        q = w.add_question(d, cat["ID"], el["ID"], "Q", scoring_criterion_id="Compliant", weighting_factor_id="Important")
        self.assertEqual(("10", "80"), (w.find_question(d, q)["Selected_Score"], w.find_question(d, q)["Weighted_Score"]))
        self.assertEqual("3", cat["Total_Questions"])

    def test_set_field_refuses_every_score_field(self):
        d, cat, q1, _ = build()
        q = w.find_question(d, q1)
        for key in w.SCORE_INPUT_FIELDS + w.DERIVED_QUESTION_FIELDS:
            with self.assertRaisesRegex(ValueError, "score_question"):
                w.set_field(q, key, "10")
        with self.assertRaisesRegex(ValueError, "score_question"):
            w.set_field(cat, "Average_Score", "10")

    def test_recalculate_follows_a_changed_level(self):
        d, cat, q1, _ = build()
        w.score_question(d, q1, "Compliant")
        d["Scoring_Criterias"][0]["Scoring_Criteria_Level"] = "9"
        w.recalculate(d)
        self.assertEqual(("9", "9"), (w.find_question(d, q1)["Selected_Score"], cat["Average_Score"]))


class Seeds(unittest.TestCase):
    def test_the_first_real_row_replaces_the_seed(self):
        d = w.new_audit()
        ev = w.add_evidence(d, "E")
        rec = w.add_recommendation(d, "R")
        tm = w.add_team_member(d, "T")
        s = w.add_session(d, "S")
        self.assertEqual([ev], [r["ID"] for r in d["Evidences"]])
        self.assertEqual([rec], [r["ID"] for r in d["Assessor_Recommendations"]])
        self.assertEqual([tm], [r["ID"] for r in d["Team_Members"]])
        self.assertEqual([s], [r["ID"] for r in d["Sessions"]])

    def test_the_first_category_element_and_question_replace_their_seeds(self):
        d, cat, q1, q2 = build()
        self.assertEqual([cat["ID"]], [c["ID"] for c in d["Categories"]])
        self.assertEqual(1, len(cat["Elements"]))
        self.assertEqual([q1, q2], [q["ID"] for q in cat["Elements"][0]["Questions"]])

    def test_a_link_list_drops_empty_and_does_not_repeat(self):
        d, _, q1, _ = build()
        ev = w.add_evidence(d, "E")
        w.link_evidence_to_question(d, q1, ev)
        w.link_evidence_to_question(d, q1, ev)
        self.assertEqual([{"ID": ev}], [dict(r) for r in w.find_question(d, q1)["Evidence_IDs"]])

    def test_an_unused_collection_keeps_its_seed(self):
        d, _, _, _ = build()
        self.assertEqual("empty", d["Parking_Lot"][0]["ID"])

    def test_a_new_category_has_a_blank_element_and_its_totals(self):
        d = w.new_audit()
        cat = w.add_category(d, "C")
        self.assertEqual("empty", cat["Elements"][0]["ID"])
        self.assertTrue(all(k in cat for k in w.CATEGORY_ROLLUPS))


class Encoding(unittest.TestCase):
    def test_raw_text_is_encoded(self):
        self.assertEqual("P&amp;ID &lt;x&gt;", w._encode_html("P&ID <x>"))

    def test_encoded_text_is_left_alone(self):
        self.assertEqual("P&amp;ID &lt;x&gt;", w._encode_html("P&amp;ID &lt;x&gt;"))

    def test_a_raw_ampersand_beside_an_encoded_one_is_still_escaped(self):
        self.assertEqual("A &amp; B &amp; C &lt; D", w._encode_html("A & B &amp; C < D"))

    def test_category_element_and_question_text_are_encoded(self):
        d = w.new_audit()
        cat = w.add_category(d, "P&ID")
        el = w.add_element(d, cat["ID"], "A&B")
        q = w.add_question(d, cat["ID"], el["ID"], "C&D?", assessor_guidance="E&F")
        self.assertEqual("P&amp;ID", cat["Category_Description"])
        self.assertEqual("A&amp;B", el["Element_Description"])
        self.assertEqual("C&amp;D?", w.find_question(d, q)["Question_Description"])


class Validation(unittest.TestCase):
    """Each failure here is one the skill used to write without complaint."""

    def test_a_score_with_no_criterion_is_refused(self):
        d, _, q1, _ = build()
        w.find_question(d, q1)["Selected_Score"] = "10"
        self.assertIn("use score_question", text_of(errors(d)))

    def test_a_criterion_with_no_score_is_refused(self):
        d, _, q1, _ = build()
        w.find_question(d, q1)["Scoring_Criteria_ID"] = d["Scoring_Criterias"][0]["ID"]
        self.assertIn("no Selected_Score", text_of(errors(d)))

    def test_a_category_missing_its_totals_is_refused(self):
        d, cat, _, _ = build()
        del cat["Percent_Complete"]
        self.assertIn("Percent_Complete", text_of(errors(d)))

    def test_a_question_missing_a_field_is_refused(self):
        d, _, q1, _ = build()
        del w.find_question(d, q1)["Findings"]
        self.assertIn("missing Findings", text_of(errors(d)))

    def test_stubbed_settings_are_refused(self):
        d, _, _, _ = build()
        d["Settings"]["Column_Visibility"] = {}
        self.assertIn("Settings.Column_Visibility", text_of(errors(d)))

    def test_missing_column_headers_at_the_current_revision_are_refused(self):
        d, _, _, _ = build()
        del d["Settings"]["Column_Headers"]
        self.assertIn("Settings.Column_Headers", text_of(errors(d)))

    def test_an_unknown_top_level_key_is_refused(self):
        d, _, _, _ = build()
        d["Extras"] = []
        self.assertIn("Unknown top-level key", text_of(errors(d)))

    def test_a_bare_string_link_is_refused(self):
        d, _, q1, _ = build()
        w.find_question(d, q1)["Evidence_IDs"] = ["abc"]
        self.assertIn("{'ID': ...} objects", text_of(errors(d)))

    def test_a_newer_file_is_refused(self):
        d, _, _, _ = build()
        d["Settings"]["Ds_Rev"] = w.current_ds_rev() + 1
        self.assertIn("newer than this writer", text_of(errors(d)))

    def test_an_older_file_saves_with_a_warning(self):
        d, _, _, _ = build()
        d["Settings"]["Ds_Rev"] = 6
        del d["Settings"]["Column_Headers"]
        self.assertEqual([], errors(d))
        self.assertTrue(any("open and save it in Open-Audit" in m for m in warnings(d)))

    def test_stale_scores_and_totals_warn(self):
        d, cat, q1, _ = build()
        w.score_question(d, q1, "Compliant")
        d["Scoring_Criterias"][0]["Scoring_Criteria_Level"] = "9"
        self.assertTrue(any("run recalculate" in m for m in warnings(d)))
        cat["Average_Score"] = "1"
        w.recalculate(d)
        self.assertEqual([], [m for m in warnings(d) if "recalculate" in m])

    def test_a_dangling_link_warns(self):
        d, _, q1, _ = build()
        w.find_question(d, q1)["Evidence_IDs"] = [{"ID": "abcdefghijklmnopqrstuv"}]
        self.assertTrue(any("not found" in m for m in warnings(d)))

    def test_save_refuses_and_writes_nothing(self):
        d, _, q1, _ = build()
        w.find_question(d, q1)["Selected_Score"] = "10"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.aud")
            with self.assertRaisesRegex(ValueError, "refusing to write"):
                w.save(d, path)
            self.assertFalse(os.path.exists(path))

    def test_a_built_study_saves_round_trips_and_has_no_warnings(self):
        d, _, q1, _ = build()
        w.score_question(d, q1, "Compliant")
        self.assertEqual([], [m for m in warnings(d)])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.aud")
            w.save(d, path)
            result = w.verify_round_trip(path, d)
            again = os.path.join(tmp, "again.aud")
            w.save(read_aud.load(path).data, again)
            self.assertEqual(Path(path).read_bytes(), Path(again).read_bytes())
        self.assertTrue(result["ok"] and result["byte_identical"], result)


class ProtocolBuilder(unittest.TestCase):
    TABLE = "| ID | Question | Weight | Assessor Guidance |\n|---|---|---|---|\n"

    def test_an_escaped_pipe_is_text_in_any_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = parse(tmp, "## Mod\n\n" + self.TABLE + "| A | Is a \\| b set? | 5 | Compare a \\| b here |\n")
        q = p["categories"][0]["elements"][0]["questions"][0]
        self.assertEqual(("Is a | b set?", "5", "Compare a | b here"),
                         (q["question"], q["weight"], q["assessor_guidance"]))

    def test_a_row_with_too_many_cells_stops_with_its_line_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "Line 5"):
                parse(tmp, "## Mod\n\n" + self.TABLE + "| A | Q | 5 | one | two |\n")

    def test_a_short_row_is_padded(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = parse(tmp, "## Mod\n\n" + self.TABLE + "| A | Q |\n")
        self.assertEqual("", p["categories"][0]["elements"][0]["questions"][0]["assessor_guidance"])

    def test_an_h3_with_no_table_is_not_an_element(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = parse(tmp, "## Mod\n\n### Notes\n\nProse.\n\n### Team\n\n" + self.TABLE + "| A | Q | 5 | G |\n")
        d = b.build_audit_data(p)
        self.assertEqual(["Team"], [e["Element_Description"] for e in d["Categories"][0]["Elements"]])

    def test_a_built_protocol_is_valid_with_totals_and_encoded_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = parse(tmp, "# Title\n\n## Module 1 — P&ID Review\n\n" + self.TABLE + "| A | Q1 | 5 | G |\n| B | Q2 | 3 | G |\n")
        d = b.build_audit_data(p)
        cat = d["Categories"][0]
        self.assertEqual([], errors(d))
        self.assertEqual([], warnings(d))
        self.assertEqual("P&amp;ID Review", cat["Category_Description"])
        self.assertEqual(("0", "2", "0%"), (cat["Completed_Questions"], cat["Total_Questions"], cat["Percent_Complete"]))
        q = cat["Elements"][0]["Questions"][0]
        self.assertEqual("[A] Q1", q["Question_Description"])
        self.assertEqual("10", q["Selected_Weighting_Factor"])

    def test_scoring_a_built_protocol_needs_nothing_else(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = parse(tmp, "## Mod\n\n" + self.TABLE + "| A | Q1 | 5 | G |\n")
        d = b.build_audit_data(p)
        cat = d["Categories"][0]
        q = w.score_question(d, cat["Elements"][0]["Questions"][0]["ID"], "Partially Compliant")
        self.assertEqual("30", q["Weighted_Score"])
        self.assertEqual(("1", "100%", "3"), (cat["Completed_Questions"], cat["Percent_Complete"], cat["Average_Score"]))

    def test_an_unmapped_weight_still_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = parse(tmp, "## Mod\n\n" + self.TABLE + "| A | Q | 9 | G |\n")
        with self.assertRaisesRegex(ValueError, "not in weight map"):
            b.build_audit_data(p)


if __name__ == "__main__":
    unittest.main()
