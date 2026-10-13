import unittest

from forge_integrations.errors import ValidationFailed
from forge_integrations.evaluation.language import detect_language
from forge_integrations.evaluation.textnorm import fold_diacritics, normalise, similarity, words
from forge_integrations.evaluation.validation import EvaluationLimits, validate_dataset


def minimal(**overrides):
    case = {"name": "capital", "prompt": "What is the capital of Nigeria?", "checks": [{"type": "contains_any", "values": ["Abuja"]}]}
    case.update(overrides)
    return {"name": "smoke", "cases": [case]}


def issues_of(doc, **kw):
    with unittest.TestCase().assertRaises(ValidationFailed) as ctx:
        validate_dataset(doc, **kw)
    return {i.path: i.message for i in ctx.exception.issues}


class ValidationTests(unittest.TestCase):
    def test_valid_dataset_gets_defaults_and_stable_ids(self):
        ds = validate_dataset(minimal())
        case = ds.cases[0]
        self.assertEqual(case.id, "c001")
        self.assertEqual(case.category, "general")
        self.assertEqual(case.repeat, 1)
        self.assertEqual(ds.version, "1")
        self.assertEqual(case.checks[0].params["ignore_case"], True)
        self.assertEqual(case.checks[0].params["ignore_diacritics"], False)

    def test_all_problems_reported_with_paths(self):
        doc = {
            "name": "",
            "extra": 1,
            "cases": [
                {"name": "a", "prompt": "p", "checks": [{"type": "exact_match"}], "category": "nope", "repeat": 99},
                {"name": "b", "prompt": "p", "checks": [{"type": "consistency"}]},
                {"name": "c", "prompt": "x\x00y", "checks": []},
            ],
        }
        found = issues_of(doc)
        self.assertIn("name", found)
        self.assertIn("extra", found)
        self.assertIn("cases[0].category", found)
        self.assertIn("cases[0].repeat", found)
        self.assertIn("cases[0].checks[0].expected", found)
        self.assertIn("cases[1].checks[0]", found)  # consistency needs repeat >= 2
        self.assertIn("cases[2].prompt", found)
        self.assertIn("cases[2].checks", found)

    def test_unknown_check_type_and_params_rejected(self):
        found = issues_of(minimal(checks=[{"type": "regex", "pattern": "a+"}]))
        self.assertIn("cases[0].checks[0].type", found)
        found = issues_of(minimal(checks=[{"type": "contains_any", "values": ["a"], "pattern": "x"}]))
        self.assertIn("cases[0].checks[0].pattern", found)

    def test_type_strictness(self):
        self.assertIn("cases[0].repeat", issues_of(minimal(repeat=True)))
        self.assertIn("cases[0].repeat", issues_of(minimal(repeat="2")))
        self.assertIn("cases[0].temperature", issues_of(minimal(temperature=float("nan"))))
        self.assertIn("cases[0].temperature", issues_of(minimal(temperature=5)))
        self.assertIn("cases[0].checks[0].ignore_case", issues_of(minimal(checks=[{"type": "contains_any", "values": ["a"], "ignore_case": 1}])))
        self.assertIn("cases[0].language", issues_of(minimal(language="fr")))
        self.assertIn("cases[0].prompt", issues_of(minimal(prompt="   ")))

    def test_consistency_requires_repeat(self):
        ok = validate_dataset(minimal(repeat=3, checks=[{"type": "consistency", "min_similarity": 0.5}]))
        self.assertEqual(ok.cases[0].checks[0].params["min_similarity"], 0.5)

    def test_between_checks_need_a_bound_and_ordered_bounds(self):
        self.assertIn("cases[0].checks[0]", issues_of(minimal(checks=[{"type": "length_between"}])))
        self.assertIn("cases[0].checks[0]", issues_of(minimal(checks=[{"type": "word_count_between", "min_words": 5, "max_words": 2}])))
        validate_dataset(minimal(checks=[{"type": "length_between", "max_chars": 100}]))

    def test_limits(self):
        lim = EvaluationLimits(max_cases=2, max_total_attempts=3)
        many = {"name": "d", "cases": [minimal()["cases"][0] for _ in range(3)]}
        self.assertIn("cases", issues_of(many, limits=lim))
        heavy = {"name": "d", "cases": [minimal(repeat=2, checks=[{"type": "not_empty"}])["cases"][0] for _ in range(2)]}
        self.assertIn("cases", issues_of(heavy, limits=lim))
        self.assertIn("cases[0].prompt", issues_of(minimal(prompt="x" * 9000)))

    def test_duplicate_case_ids_and_bad_ids(self):
        doc = {"name": "d", "cases": [minimal(id="same")["cases"][0], minimal(id="same")["cases"][0]]}
        self.assertIn("cases[1].id", issues_of(doc))
        self.assertIn("cases[0].id", issues_of(minimal(id="has space")))
        self.assertIn("cases[0].tags[0]", issues_of(minimal(tags=["bad tag!"])))

    def test_non_object_inputs(self):
        for bad in (None, [], "x", 5):
            with self.assertRaises(ValidationFailed):
                validate_dataset(bad)


class NormalisationTests(unittest.TestCase):
    def test_diacritic_folding_for_nigerian_languages(self):
        self.assertEqual(fold_diacritics("ọ̀rọ̀"), "oro")
        self.assertEqual(fold_diacritics("ịụṅ ɓɗƙƴ"), "iun bdky")
        self.assertEqual(normalise("Ọ̀rọ̀  Yorùbá", ignore_diacritics=True), "oro yoruba")
        self.assertEqual(normalise("ƙasa", ignore_diacritics=True), normalise("kasa", ignore_diacritics=True))
        self.assertNotEqual(normalise("ọjọ"), normalise("ojo"))

    def test_apostrophes_folded_only_when_asked(self):
        self.assertEqual(normalise("n\u2019ihi", ignore_diacritics=True), "nihi")
        self.assertNotEqual(normalise("n\u2019ihi"), normalise("nihi"))

    def test_nfc_equivalence(self):
        composed = "\u1ecd\u0300"  # ọ + grave
        decomposed = "o\u0323\u0300"
        self.assertEqual(normalise(composed), normalise(decomposed))

    def test_words_keep_combining_marks(self):
        self.assertEqual(words("ọ\u0300rọ\u0300 yìí, n'ihi"), ["ọ\u0300rọ\u0300", "yìí", "n", "ihi"])

    def test_similarity(self):
        self.assertEqual(similarity("a b c", "A B C"), 1.0)
        self.assertEqual(similarity("", ""), 1.0)
        self.assertEqual(similarity("a", ""), 0.0)
        self.assertLess(similarity("the cat sat", "dogs run far"), 0.2)


# Illustrative fixtures for exercising the heuristic. They are not a validated corpus.
EN = "The capital of Nigeria is Abuja, and it is located in the centre of the country."
HA = ["Abuja ita ce babban birnin Najeriya, kuma tana tsakiyar ƙasar.", "Wannan littafi yana da amfani sosai, kuma ina so in karanta shi a yau."]
YO = ["Abuja ni olú ìlú orílẹ̀-èdè Nàìjíríà, ó sì wà ní àárín orílẹ̀-èdè náà.", "Mo fẹ́ lọ sí ọjà lónìí nítorí pé mo nílò àwọn ohun èlò fún ilé wa."]
IG = ["Anyị na-aga ahịa taa n'ihi na anyị chọrọ nri.", "Ndị mmadụ niile bụ ụmụ nke otu Chineke, ma ha nwere ihe dị iche iche."]


class LanguageHeuristicTests(unittest.TestCase):
    def test_english(self):
        self.assertEqual(detect_language(EN).language, "en")

    def test_hausa(self):
        for text in HA:
            g = detect_language(text)
            self.assertEqual(g.language, "ha", (text, g))

    def test_yoruba(self):
        for text in YO:
            g = detect_language(text)
            self.assertEqual(g.language, "yo", (text, g))

    def test_igbo(self):
        for text in IG:
            g = detect_language(text)
            self.assertEqual(g.language, "ig", (text, g))

    def test_short_or_meaningless_text_is_inconclusive_not_wrong(self):
        for text in ("Sannu", "", "qzx vbn mmk lop wrt yui", "1234 5678 9012 3456"):
            g = detect_language(text)
            self.assertIsNone(g.language, (text, g))
            self.assertTrue(g.reason)

    def test_evidence_is_transparent(self):
        g = detect_language(YO[0])
        self.assertIn("strong_marker_counts", g.evidence)
        self.assertGreater(g.evidence["tone_mark_count"], 0)
        self.assertEqual(set(g.scores), {"en", "ha", "yo", "ig"})


if __name__ == "__main__":
    unittest.main()
