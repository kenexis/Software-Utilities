"""Tests for the Open-PHA writer and reader skills. Run from the repo root:

    python -m unittest discover tests
"""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "kenexis-open-pha-writer"))
sys.path.insert(0, str(ROOT / "skills" / "kenexis-open-pha-reader"))

import read_opha  # noqa: E402
import write_opha as w  # noqa: E402

ALL_MODES = [(a, l) for a in w.ANALYSIS_MODES for l in w.LOPA_MODES]


def errors(data):
    return [i for i in w.validate(data) if i["severity"] == "error"]


def error_text(data):
    return " | ".join(f"{e['path']}: {e['message']}" for e in errors(data))


def build(analysis_mode, lopa_mode="Explicit"):
    """One node, two deviations, one full scenario."""
    d = w.new_pha("Study", analysis_mode=analysis_mode, lopa_mode=lopa_mode)
    sg = w.add_safeguard(d, "PSV-1", is_ipl=True, pfd="0.01", Ipl_Credit="2")
    rec = w.add_pha_recommendation(d, "Review relief sizing")
    node = w.add_node(d, "Feed section")
    w.add_deviation(node, deviation="Low Flow", guide_word="Low", parameter="Flow")
    dev = w.add_deviation(node, deviation="High Pressure", guide_word="High", parameter="Pressure")
    if analysis_mode == w.CAUSE_CONSEQUENCE:
        cause = w.add_cause(dev, cause="Blocked outlet", frequency="1E-1")
        con = w.add_consequence(cause, consequence="Rupture", safeguard_ids=[sg],
                                pha_recommendation_ids=[rec])
        w.add_conditional_modifier(con, "Occupancy", "0.5", "0.3")
    else:
        con = w.add_consequence(dev, consequence="Rupture", pha_recommendation_ids=[rec])
        cause = w.add_cause(con, cause="Blocked outlet", frequency="1E-1", safeguard_ids=[sg])
        w.add_conditional_modifier(con, "Occupancy", "0.5", "0.3", category="Safety")
    w.add_enabling_event(cause, "Winter", "0.25")
    return d


class Templates(unittest.TestCase):
    def test_every_template_carries_the_mode_it_is_filed_under(self):
        for a, l in ALL_MODES:
            s = w.new_pha(analysis_mode=a, lopa_mode=l)["Settings"]
            self.assertEqual((a, l, w.current_ds_rev()), (s["Analysis_Mode"], s["Lopa_Mode"], s["Ds_Rev"]))

    def test_a_blank_study_passes_validation(self):
        for a, l in ALL_MODES:
            self.assertEqual([], errors(w.new_pha(analysis_mode=a, lopa_mode=l)), (a, l))

    def test_a_new_study_shares_no_id_with_the_template_and_its_matrix_still_resolves(self):
        template = json.dumps(w._templates()["documents"]["CauseConsequence/Explicit"])
        d = w.new_pha()
        rc = d["Risk_Criteria"]
        self.assertNotIn(rc["Likelihoods"][0]["ID"], template)
        likelihoods = {r["ID"] for r in rc["Likelihoods"]}
        severities = {r["ID"] for r in rc["Severities"]}
        ranks = {r["ID"] for r in rc["Risk_Rankings"]}
        for cell in rc["Intersections"]:
            self.assertIn(cell["Likelihood_ID"], likelihoods)
            self.assertIn(cell["Severity_ID"], severities)
            self.assertIn(cell["Risk_Rank_ID"], ranks)

    def test_two_studies_do_not_share_ids(self):
        a, b = w.new_pha(), w.new_pha()
        self.assertNotEqual(a["Risk_Criteria"]["Likelihoods"][0]["ID"], b["Risk_Criteria"]["Likelihoods"][0]["ID"])

    def test_the_two_lopa_modes_share_one_record_shape(self):
        for a in w.ANALYSIS_MODES:
            docs = w._templates()["documents"]
            self.assertEqual(w._without_ids(docs[f"{a}/Explicit"]["Nodes"]),
                             w._without_ids(docs[f"{a}/Implicit"]["Nodes"]))

    def test_generated_ids_are_22_lowercase_base36(self):
        self.assertRegex(w.make_id(), r"^[0-9a-z]{22}$")


class Worksheet(unittest.TestCase):
    def test_built_studies_validate_round_trip_and_carry_a_current_rev(self):
        for a, l in ALL_MODES:
            d = build(a, l)
            self.assertEqual([], errors(d), (a, l))
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "s.opha")
                w.save(d, path)
                result = w.verify_round_trip(path, d)
            self.assertTrue(result["ok"] and result["matches_data"], (a, l))

    def test_nothing_built_is_missing_a_field_the_application_writes(self):
        for a, l in ALL_MODES:
            missing = [i for i in w.validate(build(a, l)) if "missing fields" in i["message"]]
            self.assertEqual([], missing, (a, l))

    def test_the_first_real_row_replaces_the_seed_row_and_an_unused_parent_keeps_it(self):
        d = build(w.CAUSE_CONSEQUENCE)
        low_flow, high = d["Nodes"][0]["Deviations"]
        self.assertEqual("empty", low_flow["Causes"][0]["ID"])
        self.assertEqual(1, len(high["Causes"]))
        self.assertNotEqual("empty", high["Causes"][0]["ID"])
        self.assertEqual(1, len(high["Causes"][0]["Consequences"]))

    def test_a_blank_named_deviation_that_was_used_is_not_replaced(self):
        d = w.new_pha()
        node = w.add_node(d, "N")
        first = w.add_deviation(node, deviation="")
        second = w.add_deviation(node, deviation="High")
        w.add_cause(first, cause="C")
        self.assertEqual([first, second], node["Deviations"])

    def test_a_new_node_replaces_the_blank_one_and_keeps_a_deviation(self):
        d = w.new_pha()
        node = w.add_node(d, "N")
        self.assertEqual([node], d["Nodes"])
        self.assertEqual(1, len(node["Deviations"]))

    def test_consequence_cause_tree_links_safeguards_on_the_cause(self):
        d = build(w.CONSEQUENCE_CAUSE)
        con = d["Nodes"][0]["Deviations"][1]["Consequences"][0]
        self.assertNotIn("Safeguard_IDs", con)
        self.assertNotEqual("empty", con["Causes"][0]["Safeguard_IDs"][0]["ID"])
        self.assertEqual("Occupancy", con["Conditional_Modifiers_Safety"][0]["CM_S_Description"])

    def test_a_helper_called_for_the_wrong_tree_says_so(self):
        d = w.new_pha(analysis_mode=w.CONSEQUENCE_CAUSE)
        dev = w.add_deviation(w.add_node(d, "N"), deviation="D")
        with self.assertRaisesRegex(ValueError, "add_consequence"):
            w.add_cause(dev, cause="C")
        d = w.new_pha()
        dev = w.add_deviation(w.add_node(d, "N"), deviation="D")
        with self.assertRaisesRegex(ValueError, "add_consequence needs"):
            w.add_consequence(dev, consequence="C")

    def test_safeguards_given_on_the_wrong_row_are_refused(self):
        d = w.new_pha(analysis_mode=w.CONSEQUENCE_CAUSE)
        dev = w.add_deviation(w.add_node(d, "N"), deviation="D")
        with self.assertRaisesRegex(ValueError, "link to the cause"):
            w.add_consequence(dev, consequence="C", safeguard_ids=["x"])

    def test_a_field_the_schema_does_not_have_is_refused(self):
        with self.assertRaises(KeyError):
            w.new_record("Causes", Cause_Type_ID="x")

    def test_optional_library_fields_are_accepted(self):
        self.assertEqual("lib", w.new_record("Causes", Cause_Library_Id="lib")["Cause_Library_Id"])


class Validation(unittest.TestCase):
    """Each failure here is one a real study reached KISS with."""

    def test_empty_settings_objects_are_refused(self):
        # All reports 500 on the missing Numbering keys; the browser throws on the rest.
        for key in ("Numbering", "Grid_Settings", "Tab_Visibility", "Consequence_Category_Names"):
            d = build(w.CAUSE_CONSEQUENCE)
            d["Settings"][key] = {}
            self.assertIn(f"Settings.{key}", error_text(d))

    def test_the_stub_is_refused_at_the_old_revision_too(self):
        d = build(w.CAUSE_CONSEQUENCE)
        d["Settings"]["Ds_Rev"] = 39
        d["Settings"]["Numbering"] = {}
        self.assertIn("Settings.Numbering", error_text(d))

    def test_an_empty_column_tree_is_refused(self):
        for key in ("Column_Visibility", "Column_Headers"):
            d = build(w.CAUSE_CONSEQUENCE)
            d["Settings"][key] = {}
            self.assertIn(f"Settings.{key}", error_text(d))

    def test_an_empty_risk_matrix_is_refused(self):
        d = build(w.CAUSE_CONSEQUENCE)
        d["Risk_Criteria"]["Likelihoods"] = []
        self.assertIn("Risk_Criteria.Likelihoods", error_text(d))

    def test_a_consequence_without_alarp_keys_is_refused(self):
        # The ALARP report section 500s on a missing Alarp_Analysis.
        for a in w.ANALYSIS_MODES:
            d = build(a)
            dev = d["Nodes"][0]["Deviations"][1]
            con = dev["Causes"][0]["Consequences"][0] if a == w.CAUSE_CONSEQUENCE else dev["Consequences"][0]
            del con["Alarp_Analysis"], con["Alarp_Required"]
            self.assertIn("Alarp_Analysis", error_text(d))

    def test_changing_analysis_mode_by_hand_is_refused(self):
        # The tree and the column settings still say the other mode.
        d = build(w.CAUSE_CONSEQUENCE)
        d["Settings"]["Analysis_Mode"] = w.CONSEQUENCE_CAUSE
        text = error_text(d)
        self.assertIn("Settings.Column_Visibility", text)
        self.assertIn("deviation has no Consequences list", text)

    def test_a_bare_string_reference_is_refused(self):
        d = build(w.CAUSE_CONSEQUENCE)
        d["Nodes"][0]["Drawing_IDs"] = ["abc"]
        self.assertIn("{'ID': ...} objects", error_text(d))

    def test_an_empty_reference_list_is_refused(self):
        d = build(w.CAUSE_CONSEQUENCE)
        d["Nodes"][0]["Deviations"][1]["Causes"][0]["Consequences"][0]["Safeguard_IDs"] = []
        self.assertIn("reference list must not be empty", error_text(d))

    def test_save_refuses_and_writes_nothing(self):
        d = build(w.CAUSE_CONSEQUENCE)
        d["Settings"]["Numbering"] = {}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.opha")
            with self.assertRaisesRegex(ValueError, "Refusing to save"):
                w.save(d, path)
            self.assertFalse(os.path.exists(path))

    def test_row_issues_are_grouped_not_repeated(self):
        d = build(w.CAUSE_CONSEQUENCE)
        node = d["Nodes"][0]
        for dev in node["Deviations"]:
            for cause in dev["Causes"]:
                for con in cause["Consequences"]:
                    del con["Alarp_Required"]
        self.assertEqual(1, len([e for e in errors(d) if "Alarp_Required" in e["path"]]))

    def test_unrated_rows_are_reported(self):
        d = build(w.CAUSE_CONSEQUENCE)
        d["Nodes"][0]["Deviations"][1]["Causes"][0]["Frequency"] = ""
        messages = [i["message"] for i in w.validate(d)]
        self.assertIn("1 of 1 causes have no frequency, which Explicit LOPA reads", messages)
        self.assertIn("1 of 1 consequences have no severity", messages)

    def test_implicit_lopa_reports_likelihood_instead(self):
        messages = [i["message"] for i in w.validate(build(w.CAUSE_CONSEQUENCE, "Implicit"))]
        self.assertIn("1 of 1 causes have no likelihood, which Implicit LOPA reads", messages)

    def test_an_older_file_saves_with_a_warning(self):
        d = build(w.CAUSE_CONSEQUENCE)
        d["Settings"]["Ds_Rev"] = 40
        issues = w.validate(d)
        self.assertEqual([], [i for i in issues if i["severity"] == "error"])
        self.assertTrue(any("open and save it in Open-PHA" in i["message"] for i in issues))

    def test_a_file_from_a_newer_application_is_refused(self):
        d = build(w.CAUSE_CONSEQUENCE)
        d["Settings"]["Ds_Rev"] = w.current_ds_rev() + 1
        self.assertIn("newer than this writer", error_text(d))

    def test_unmodified_load_and_save_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = os.path.join(tmp, "a.opha"), os.path.join(tmp, "b.opha")
            w.save(build(w.CONSEQUENCE_CAUSE), a)
            w.save(w.load(a), b)
            self.assertEqual(Path(a).read_bytes(), Path(b).read_bytes())


class LopaColumns(unittest.TestCase):
    """The application's calculation ignores a factor whose column is hidden."""

    def visible(self, d, key):
        tree = d["Settings"]["Column_Visibility"]
        return w._column_visibility(tree, tree, key)

    def hide(self, d, key):
        def walk(node):
            for k, v in node.items():
                if isinstance(v, dict):
                    walk(v)
                elif k == key:
                    node[k] = False
        walk(d["Settings"]["Column_Visibility"])

    def test_the_applications_defaults_hide_factors_and_new_pha_shows_them(self):
        template = w._templates()["documents"]["CauseConsequence/Explicit"]["Settings"]["Column_Visibility"]
        self.assertIs(False, w._column_visibility(template, template, "Conditional_Modifiers"))
        for a, l in ALL_MODES:
            d = w.new_pha(analysis_mode=a, lopa_mode=l)
            names = ["Enabling_Events", "EE_Probability", "EE_Credit", "Safeguard_IDs", "Pfd", "Ipl_Credit"]
            names += (["Conditional_Modifiers", "CM_Probability", "CM_Credit"] if a == w.CAUSE_CONSEQUENCE
                      else ["Conditional_Modifiers_Safety", "CM_S_Probability", "CM_S_Credit"])
            for key in names:
                self.assertIs(True, self.visible(d, key), (a, l, key))

    def test_a_built_study_has_no_hidden_factors(self):
        for a, l in ALL_MODES:
            self.assertEqual([], w._hidden_lopa_factors(build(a, l), a), (a, l))

    def test_hidden_modifiers_with_data_are_refused(self):
        d = build(w.CAUSE_CONSEQUENCE)
        self.hide(d, "Conditional_Modifiers")
        self.assertIn("conditional modifiers have hidden columns", error_text(d))

    def test_hidden_factors_in_the_other_tree_are_refused(self):
        d = build(w.CONSEQUENCE_CAUSE)
        self.hide(d, "Conditional_Modifiers_Safety")
        self.assertIn("Safety conditional modifiers", error_text(d))

    def test_a_hidden_value_column_counts_as_hidden(self):
        d = build(w.CAUSE_CONSEQUENCE)
        self.hide(d, "CM_Probability")
        self.assertIn("conditional modifiers", error_text(d))

    def test_a_list_with_no_visible_columns_counts_as_hidden(self):
        d = build(w.CAUSE_CONSEQUENCE)
        children = d["Settings"]["Column_Visibility"]["Nodes_Children"]["Deviations_Children"]["Causes_Children"]["Consequences_Children"]["Conditional_Modifiers_Children"]
        for key in children:
            children[key] = False
        self.assertIn("conditional modifiers", error_text(d))

    def test_hidden_columns_with_no_data_in_them_are_fine(self):
        d = w.new_pha()
        self.hide(d, "Conditional_Modifiers")
        self.assertEqual([], errors(d))

    def test_the_other_lopa_styles_column_does_not_count(self):
        d = build(w.CAUSE_CONSEQUENCE, "Explicit")
        self.hide(d, "CM_Credit")
        self.assertEqual([], errors(d))

    def test_hidden_credited_ipls_are_refused(self):
        d = build(w.CAUSE_CONSEQUENCE)
        self.hide(d, "Pfd")
        self.assertIn("credited IPLs", error_text(d))

    def test_a_deliberately_hidden_factor_can_be_saved_with_a_warning(self):
        d = build(w.CAUSE_CONSEQUENCE)
        self.hide(d, "Conditional_Modifiers")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.opha")
            with self.assertRaisesRegex(ValueError, "show_lopa_columns"):
                w.save(d, path)
            warnings = w.save(d, path, allow_hidden_factors=True)
        self.assertTrue(any("hidden columns" in i["message"] for i in warnings))

    def test_show_lopa_columns_clears_the_refusal(self):
        d = build(w.CAUSE_CONSEQUENCE)
        self.hide(d, "Conditional_Modifiers")
        w.show_lopa_columns(d)
        self.assertEqual([], errors(d))

    def test_a_template_with_hidden_factors_is_corrected(self):
        source = w.new_pha()
        self.hide(source, "Conditional_Modifiers")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.opha")
            w.save(source, path)
            d = w.new_pha(template_path=path)
        self.assertIs(True, self.visible(d, "Conditional_Modifiers"))


class Severities(unittest.TestCase):
    """The application drops a severity that is not in its category's list."""

    def severity(self, d, category="Safety"):
        return next(s for s in d["Risk_Criteria"]["Severities"] if s["Severity_Type"] == category)

    def study(self, **fields):
        d = w.new_pha()
        cause = w.add_cause(w.add_deviation(w.add_node(d, "N"), deviation="D"), cause="C")
        w.add_consequence(cause, consequence="Q", Lopa_Required="true", **fields)
        return d

    def test_severity_fields_set_the_category_in_both_trees(self):
        d = w.new_pha()
        s = self.severity(d, "Asset")
        self.assertEqual({"Consequence_Severity_ID": s["ID"], "Consequence_Type_ID": "Asset",
                          "Tmel": s["RM_Tmel"]}, w.severity_fields(d, s["ID"]))
        d = w.new_pha(analysis_mode=w.CONSEQUENCE_CAUSE)
        s = self.severity(d, "Asset")
        self.assertEqual({"Asset_Severity_ID": s["ID"], "Tmel_Asset": s["RM_Tmel"]},
                         w.severity_fields(d, s["ID"]))

    def test_an_unknown_severity_is_an_error(self):
        with self.assertRaises(KeyError):
            w.severity_fields(w.new_pha(), "nope")

    def test_a_severity_with_no_category_is_refused(self):
        d = self.study()
        q = d["Nodes"][0]["Deviations"][0]["Causes"][0]["Consequences"][0]
        q["Consequence_Severity_ID"] = self.severity(d)["ID"]
        self.assertIn("Severity_Type", error_text(d))

    def test_a_severity_from_another_category_is_refused(self):
        d = self.study()
        q = d["Nodes"][0]["Deviations"][0]["Causes"][0]["Consequences"][0]
        q["Consequence_Severity_ID"] = self.severity(d)["ID"]
        q["Consequence_Type_ID"] = "Asset"
        self.assertIn("Severity_Type", error_text(d))

    def test_the_before_and_after_severities_are_checked_too(self):
        d = self.study()
        q = d["Nodes"][0]["Deviations"][0]["Causes"][0]["Consequences"][0]
        q["Consequence_Severity_ID_After_Recommendations"] = self.severity(d)["ID"]
        self.assertIn("Severity_Type", error_text(d))

    def test_severity_fields_pass(self):
        d = w.new_pha()
        s = self.severity(d)
        cause = w.add_cause(w.add_deviation(w.add_node(d, "N"), deviation="D"), cause="C")
        w.add_consequence(cause, consequence="Q", **w.severity_fields(d, s["ID"]))
        self.assertEqual([], errors(d))

    def test_a_row_left_off_the_lopa_worksheet_is_reported(self):
        d = w.new_pha()
        s = self.severity(d)
        cause = w.add_cause(w.add_deviation(w.add_node(d, "N"), deviation="D"), cause="C")
        con = w.add_consequence(cause, consequence="Q", **w.severity_fields(d, s["ID"]))
        off = lambda: any("Lopa_Required" in i["message"] for i in w.validate(d))
        self.assertTrue(off())
        con["Lopa_Required"] = "true"
        self.assertFalse(off())


class Text(unittest.TestCase):
    def test_encoding_matches_the_application(self):
        self.assertEqual("P &lt; 5 &amp; T &gt; 9", w.encode_text("P < 5 & T > 9"))

    def test_encoding_twice_changes_nothing(self):
        once = w.encode_text("A & B <C>")
        self.assertEqual(once, w.encode_text(once))

    def test_helpers_encode_text(self):
        d = w.new_pha()
        w.add_safeguard(d, "Trip if P > 5")
        node = w.add_node(d, "Feed & <drain>")
        self.assertEqual("Trip if P &gt; 5", d["Safeguards"][-1]["Safeguard"])
        self.assertEqual("Feed &amp; &lt;drain&gt;", node["Node_Description"])

    def test_modifiers_and_enabling_events_encode_text(self):
        for mode in w.ANALYSIS_MODES:
            d = w.new_pha(analysis_mode=mode)
            dev = w.add_deviation(w.add_node(d, "N"), deviation="D")
            if mode == w.CAUSE_CONSEQUENCE:
                cause = w.add_cause(dev, cause="C")
                con = w.add_consequence(cause, consequence="Q")
                cm = w.add_conditional_modifier(con, "P > 5")
                self.assertEqual("P &gt; 5", cm["CM_Description"])
            else:
                con = w.add_consequence(dev, consequence="Q")
                cause = w.add_cause(con, cause="C")
                cm = w.add_conditional_modifier(con, "P > 5", category="Safety")
                self.assertEqual("P &gt; 5", cm["CM_S_Description"])
            self.assertEqual("T &lt; 3", w.add_enabling_event(cause, "T < 3")["EE_Description"])

    def test_ids_and_numbers_are_untouched(self):
        d = w.new_pha()
        w.add_safeguard(d, "S", pfd="1E-1")
        self.assertEqual("1E-1", d["Safeguards"][-1]["Pfd"])


class Columns(unittest.TestCase):
    def test_show_columns(self):
        d = w.new_pha()
        tree = d["Settings"]["Column_Visibility"]
        self.assertIs(False, w._column_visibility(tree, tree, "Ipl_Tag"))
        w.show_columns(d, "Ipl_Tag", "Safeguard_Type")
        self.assertIs(True, w._column_visibility(tree, tree, "Ipl_Tag"))

    def test_an_unknown_column_is_an_error(self):
        with self.assertRaises(KeyError):
            w.show_columns(w.new_pha(), "Nonsense")


class ReaderSummary(unittest.TestCase):
    def test_summary_counts_lopa_recommendations(self):
        d = build(w.CAUSE_CONSEQUENCE)
        w.add_lopa_recommendation(d, "Add a trip")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.opha")
            w.save(d, path)
            self.assertEqual(1, read_opha.load(path).summary()["lopa_recommendations"])


class RatingWarnings(unittest.TestCase):
    def messages(self, d):
        return [i["message"] for i in w.validate(d)]

    def test_a_frequency_above_one_per_year_is_questioned(self):
        d = build(w.CAUSE_CONSEQUENCE)
        d["Nodes"][0]["Deviations"][1]["Causes"][0]["Frequency"] = "10"
        self.assertTrue(any("above 1 per year" in m for m in self.messages(d)))

    def test_a_frequency_that_is_not_a_number_is_reported(self):
        d = build(w.CAUSE_CONSEQUENCE)
        d["Nodes"][0]["Deviations"][1]["Causes"][0]["Frequency"] = "often"
        self.assertTrue(any("not a number" in m for m in self.messages(d)))

    def test_a_missing_tmel_is_reported_in_explicit_lopa(self):
        self.assertTrue(any("no TMEL" in m for m in self.messages(build(w.CAUSE_CONSEQUENCE, "Explicit"))))
        self.assertFalse(any("no TMEL" in m for m in self.messages(build(w.CAUSE_CONSEQUENCE, "Implicit"))))


class SourcesOfCriteria(unittest.TestCase):
    def write(self, tmp, d):
        path = os.path.join(tmp, "source.opha")
        w.save(d, path)
        return path

    def test_risk_criteria_come_from_a_current_study(self):
        source = w.new_pha()
        source["Settings"]["Consequence_Category_Names"]["Safety"]["name"] = "S and E"
        source["Risk_Criteria"]["Likelihoods"][0]["RM_Description"] = "Customer band"
        with tempfile.TemporaryDirectory() as tmp:
            d = w.new_pha(analysis_mode=w.CONSEQUENCE_CAUSE, risk_criteria_from=self.write(tmp, source))
        self.assertEqual("Customer band", d["Risk_Criteria"]["Likelihoods"][0]["RM_Description"])
        self.assertEqual("S and E", d["Settings"]["Consequence_Category_Names"]["Safety"]["name"])
        self.assertNotEqual(source["Risk_Criteria"]["Likelihoods"][0]["ID"], d["Risk_Criteria"]["Likelihoods"][0]["ID"])
        self.assertEqual([], errors(d))

    def test_an_old_source_is_refused_with_the_way_forward(self):
        source = w.new_pha()
        source["Settings"]["Ds_Rev"] = 39
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, source)
            with self.assertRaisesRegex(ValueError, "Open it in Open-PHA, save it"):
                w.new_pha(risk_criteria_from=path)
            with self.assertRaisesRegex(ValueError, "Open it in Open-PHA, save it"):
                w.new_pha(template_path=path)

    def test_a_matrix_cannot_change_lopa_style(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, w.new_pha(lopa_mode="Implicit"))
            with self.assertRaisesRegex(ValueError, "cannot change style"):
                w.new_pha(lopa_mode="Explicit", risk_criteria_from=path)

    def test_a_template_refuses_a_mode_it_does_not_have(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, w.new_pha())
            with self.assertRaisesRegex(ValueError, "does not match"):
                w.new_pha(template_path=path, analysis_mode=w.CONSEQUENCE_CAUSE)


class Reader(unittest.TestCase):
    def load(self, d):
        return read_opha.loads(w.dumps(d))

    def test_both_trees_read_the_same_scenario(self):
        for a in w.ANALYSIS_MODES:
            study = self.load(build(a))
            rows = list(study.iter_consequences())
            self.assertEqual(1, len(rows), a)
            self.assertEqual("Blocked outlet", rows[0].cause["Cause"])
            self.assertEqual("Rupture", rows[0].consequence["Consequence"])
            self.assertEqual(["PSV-1"], [s["Safeguard"] for s in study.safeguards_of(rows[0])])
            self.assertEqual(1, study.summary()["causes"], a)
            self.assertEqual(1, study.summary()["consequences"], a)

    def test_seed_rows_are_not_data(self):
        study = self.load(w.new_pha())
        self.assertEqual([], list(study.iter_consequences()))
        self.assertEqual(0, study.summary()["causes"])


if __name__ == "__main__":
    unittest.main()
