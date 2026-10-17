"""Dataset-expert and lab-manager tests. No model or data repository is contacted."""

from datetime import date
import unittest

from sewall.agent import _action, default_registry
from sewall.experts import _months, consult, parse_question
from sewall.scripted import MAX_STEPS


TODAY = date(2026, 10, 14)
GROWTH = ("How do temperature, light and grazing affect growth of Margalefidinium polykrikoides, "
          "and which genotypes and pathways matter for a growth model?")
YORK = "Did satellite chlorophyll rise during the 2022 Margalefidinium polykrikoides bloom in the York River?"


class ExpertTests(unittest.TestCase):
    def test_parse_finds_taxon_site_and_default_season(self):
        parsed = parse_question(YORK, TODAY)
        self.assertEqual(parsed["taxa"], ["Margalefidinium polykrikoides"])
        self.assertEqual(parsed["site"], "york-river-lower")
        self.assertEqual(parsed["window"], ("2022-06-01", "2022-10-31"))

    def test_synonym_matches_accepted_name(self):
        self.assertEqual(parse_question("Cochlodinium polykrikoides strains", TODAY)["taxa"],
                         ["Margalefidinium polykrikoides"])

    def test_current_year_gives_no_window(self):
        self.assertIsNone(parse_question("bloom in the York River in 2026", TODAY)["window"])

    def test_months_split_inclusive_window(self):
        self.assertEqual(_months("2022-06-15", "2022-08-03"),
                         [("2022-06-15", "2022-06-30"), ("2022-07-01", "2022-07-31"), ("2022-08-01", "2022-08-03")])
        self.assertEqual(_months("2021-12-20", "2022-01-05")[-1], ("2022-01-01", "2022-01-05"))

    def test_lab_manager_script_validates_against_controller(self):
        memo = consult(YORK, TODAY)
        steps = memo["script"]["steps"]
        self.assertLessEqual(len(steps), MAX_STEPS)
        self.assertEqual(steps[0]["database"], "pubmed")
        self.assertIn("Cochlodinium polykrikoides", steps[0]["query"])
        registry = default_registry(earth_engine=True)
        for step in steps:
            _action(step, {}, registry)
        self.assertEqual(memo["suggested_limits"], {"max_actions": len(steps), "max_model_calls": len(steps) + 2})

    def test_overflow_is_deferred_and_reported(self):
        memo = consult("Satellite landscape context and sequencing strains of Margalefidinium polykrikoides "
                       "and Alexandrium monilatum in the York River, 2022-01-01 to 2022-12-31", TODAY)
        self.assertEqual(len(memo["script"]["steps"]), MAX_STEPS)
        self.assertTrue(memo["deferred"])
        self.assertTrue(any("deferred" in gap for gap in memo["script"]["gaps"]))

    def test_growth_and_community_plans_fit_without_deferral(self):
        community = ("How do competition, allelopathy and bacteria shape growth of Margalefidinium polykrikoides "
                     "and Alexandrium monilatum in a community interaction model?")
        for question in (GROWTH, community):
            memo = consult(question, TODAY)
            self.assertEqual(memo["deferred"], [])
            actions = [step["action"] for step in memo["script"]["steps"]]
            self.assertLess(actions.index("fetch_markers"), actions.index("assign_ribotype"))
            self.assertLess(actions.index("assign_ribotype"), actions.index("link_strains"))
            # Only A. monilatum lacks a ribotype panel, so only the community plan records that gap.
            self.assertEqual("assess_source" in actions, question == community)
        queries = [step.get("query", "") for step in consult(community, TODAY)["script"]["steps"]]
        self.assertTrue(any("microbiome" in query for query in queries))

    def test_eol_expert_records_missing_connector(self):
        memo = consult("What traits and life history are known for Alexandrium monilatum?", TODAY)
        eol = next(item for item in memo["consultations"] if item["expert"] == "eol")
        self.assertEqual(eol["actions"][0], {"action": "assess_source", "source": "eol", "reason": eol["actions"][0]["reason"]})
        self.assertTrue(eol["gaps"])

    def test_unknown_site_is_a_gap_not_a_guess(self):
        memo = consult("satellite chlorophyll bloom in Tampa Bay in 2022", TODAY)
        earth = next(item for item in memo["consultations"] if item["expert"] == "earthengine")
        self.assertEqual(earth["actions"], [])
        self.assertIn("No named Earth Engine site", earth["gaps"][0])

    def test_growth_expert_plans_genotype_pathway_and_community_searches(self):
        memo = consult(GROWTH, TODAY)
        growth = next(item for item in memo["consultations"] if item["expert"] == "growth")
        self.assertEqual([term["factor"] for term in growth["model_terms"]],
                         ["genotype", "temperature", "light", "community"])
        queries = [action.get("query", "") for action in growth["actions"]]
        self.assertTrue(any("ribotype" in query for query in queries))
        self.assertTrue(any("HSP70" in query and "rubisco" in query for query in queries))
        self.assertTrue(any("allelopathy" in query for query in queries))
        self.assertTrue(any("microbiome" in query for query in queries))
        registry = default_registry(earth_engine=True)
        for step in memo["script"]["steps"]:
            _action(step, {}, registry)

    def test_growth_expert_assigns_ribotypes_only_where_a_panel_exists(self):
        growth = next(item for item in consult(GROWTH, TODAY)["consultations"] if item["expert"] == "growth")
        steps = [(action["action"], action.get("taxon"), action.get("marker")) for action in growth["actions"]]
        self.assertIn(("fetch_markers", "Margalefidinium polykrikoides", "lsu"), steps)
        self.assertIn(("assign_ribotype", "Margalefidinium polykrikoides", "lsu"), steps)
        self.assertIn(("link_strains", "Margalefidinium polykrikoides", "lsu"), steps)
        amon = next(item for item in consult("Growth model for Alexandrium monilatum", TODAY)["consultations"]
                    if item["expert"] == "growth")
        self.assertNotIn("assign_ribotype", [action["action"] for action in amon["actions"]])
        self.assertIn("No curated ribotype panel exists for Alexandrium monilatum", amon["gaps"][0])
        self.assertEqual(amon["actions"][-1]["source"], "ncbi_genotype")

    def test_growth_without_factor_defaults_to_temperature_and_salinity(self):
        growth = next(item for item in consult("Growth model for Alexandrium monilatum", TODAY)["consultations"]
                      if item["expert"] == "growth")
        self.assertEqual([term["factor"] for term in growth["model_terms"]], ["genotype", "temperature", "salinity"])
        self.assertTrue(any("assumed" in caution for caution in growth["cautions"]))

    def test_satellite_question_does_not_wake_growth_expert(self):
        growth = next(item for item in consult(YORK, TODAY)["consultations"] if item["expert"] == "growth")
        self.assertFalse(growth["relevant"])

    def test_lab_manager_interleaves_and_drops_repeated_actions(self):
        memo = consult("Satellite chlorophyll and growth of Margalefidinium polykrikoides in the York River in 2022", TODAY)
        steps = memo["script"]["steps"]
        keys = [tuple(sorted((k, v) for k, v in step.items() if k != "reason")) for step in steps]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn("chlorophyll_timeseries", [step["action"] for step in steps[:3]])

    def test_unrelated_question_writes_no_script(self):
        self.assertIsNone(consult("What is the weather like?", TODAY)["script"])


if __name__ == "__main__":
    unittest.main()
