import unittest

from forge_integrations.evaluation.evaluators import CheckContext, evaluate_check
from forge_integrations.evaluation.models import Case, Check
from forge_integrations.evaluation.validation import validate_dataset
from forge_integrations.security.tokens import generate_token

CASE = Case(id="c1", name="n", prompt="p", checks=())


def run_check(ctype, output, *, outputs=None, latencies=None, **params):
    # Build the check through the validator so defaults match production behaviour.
    doc = {"name": "d", "cases": [{"name": "n", "prompt": "p", "repeat": 3, "checks": [{"type": ctype, **params}]}]}
    check = validate_dataset(doc).cases[0].checks[0]
    outs = tuple(outputs) if outputs is not None else (output,)
    lats = tuple(latencies) if latencies is not None else tuple(100.0 for _ in outs)
    return evaluate_check(check, CheckContext(case=CASE, outputs=outs, latencies_ms=lats))


class CheckTests(unittest.TestCase):
    def test_exact_match_normalises_case_and_whitespace(self):
        self.assertTrue(run_check("exact_match", "  ABUJA \n", expected="abuja").passed)
        self.assertFalse(run_check("exact_match", "Abuja city", expected="abuja").passed)
        self.assertFalse(run_check("exact_match", "ABUJA", expected="abuja", ignore_case=False).passed)

    def test_exact_match_ignore_diacritics_for_yoruba_and_hausa(self):
        self.assertTrue(run_check("exact_match", "ọ̀rọ̀", expected="oro", ignore_diacritics=True).passed)
        self.assertFalse(run_check("exact_match", "ọ̀rọ̀", expected="oro").passed)
        self.assertTrue(run_check("exact_match", "ƙasa", expected="kasa", ignore_diacritics=True).passed)

    def test_contains_variants_and_evidence(self):
        out = "The capital is Abuja, in the centre."
        self.assertTrue(run_check("contains_all", out, values=["abuja", "centre"]).passed)
        r = run_check("contains_all", out, values=["abuja", "Lagos"])
        self.assertFalse(r.passed)
        self.assertEqual(r.evidence["missing"], ["Lagos"])
        self.assertTrue(run_check("contains_any", out, values=["Lagos", "Abuja"]).passed)
        self.assertFalse(run_check("contains_any", out, values=["Lagos"]).passed)
        bad = run_check("not_contains", out, values=["abuja", "kano"])
        self.assertFalse(bad.passed)
        self.assertEqual(bad.evidence["found"], ["abuja"])
        self.assertTrue(run_check("not_contains", out, values=["kano"]).passed)

    def test_strip_punctuation(self):
        self.assertTrue(run_check("contains_all", "It is Abuja.", values=["abuja"], strip_punctuation=True).passed)
        self.assertTrue(run_check("exact_match", "Abuja!", expected="abuja", strip_punctuation=True).passed)

    def test_starts_and_ends_with(self):
        self.assertTrue(run_check("starts_with", "Sannu, ina kwana", value="sannu").passed)
        self.assertFalse(run_check("starts_with", "Ina kwana, sannu", value="sannu").passed)
        self.assertTrue(run_check("ends_with", "Na gode.", value="na gode.").passed)

    def test_length_and_word_count(self):
        self.assertTrue(run_check("length_between", "abcde", min_chars=3, max_chars=5).passed)
        self.assertFalse(run_check("length_between", "abcdef", max_chars=5).passed)
        self.assertTrue(run_check("word_count_between", "one two three", min_words=3, max_words=3).passed)
        self.assertFalse(run_check("word_count_between", "one two", min_words=3).passed)

    def test_json_valid(self):
        self.assertTrue(run_check("json_valid", '{"a": 1}').passed)
        self.assertTrue(run_check("json_valid", '```json\n{"a": 1}\n```').passed)
        self.assertFalse(run_check("json_valid", '```json\n{"a": 1}\n```', allow_code_fence=False).passed)
        self.assertFalse(run_check("json_valid", "{'a': 1}").passed)
        self.assertFalse(run_check("json_valid", "NaN").passed)
        self.assertFalse(run_check("json_valid", "[" * 5000).passed)  # deep nesting never crashes
        r = run_check("json_valid", '{"a": 1}', required_keys=["a", "b"])
        self.assertFalse(r.passed)
        self.assertEqual(r.evidence["missing"], ["b"])
        self.assertFalse(run_check("json_valid", "[1,2]", required_keys=["a"]).passed)

    def test_numeric_within(self):
        self.assertTrue(run_check("numeric_within", "There are 1,234 items.", expected=1234).passed)
        self.assertTrue(run_check("numeric_within", "About 9.8 m/s", expected=9.81, tolerance=0.05).passed)
        self.assertFalse(run_check("numeric_within", "About 12", expected=9.81, tolerance=0.05).passed)
        self.assertFalse(run_check("numeric_within", "no digits here", expected=1).passed)
        self.assertTrue(run_check("numeric_within", "1 then 2 then 3", expected=3, which="last").passed)

    def test_language_match_reports_review_when_unsure(self):
        r = run_check("language_match", "Sannu", language="ha")
        self.assertIsNone(r.passed)
        self.assertTrue(r.evidence["heuristic"])
        ok = run_check("language_match", "Abuja ita ce babban birnin Najeriya, kuma tana tsakiyar ƙasar.", language="ha")
        self.assertTrue(ok.passed)
        collapse = run_check("language_match", "The capital of Nigeria is Abuja, and it is located in the centre of the country.", language="yo")
        self.assertFalse(collapse.passed)
        self.assertEqual(collapse.evidence["detected"], "en")

    def test_no_secrets(self):
        token = generate_token().token
        self.assertFalse(run_check("no_secrets", f"use {token}").passed)
        self.assertFalse(run_check("no_secrets", "key: hf_" + "a" * 30).passed)
        self.assertFalse(run_check("no_secrets", "-----BEGIN RSA PRIVATE KEY-----").passed)
        self.assertTrue(run_check("no_secrets", "Nothing sensitive here.").passed)

    def test_no_contact_details(self):
        self.assertFalse(run_check("no_contact_details", "mail ade@example.com").passed)
        self.assertFalse(run_check("no_contact_details", "call 08031234567 now").passed)
        self.assertFalse(run_check("no_contact_details", "call +2348031234567 now").passed)
        self.assertFalse(run_check("no_contact_details", "call +14155550123 now").passed)
        self.assertTrue(run_check("no_contact_details", "Order 12345 shipped.").passed)

    def test_consistency(self):
        same = run_check("consistency", "", outputs=["Abuja is the capital", "abuja is the capital", "Abuja is the capital"], min_similarity=0.9)
        self.assertTrue(same.passed)
        self.assertEqual(same.evidence["mean_similarity"], 1.0)
        diff = run_check("consistency", "", outputs=["Abuja is the capital", "Lagos is the largest city", "Kano is in the north"], min_similarity=0.8)
        self.assertFalse(diff.passed)
        self.assertLess(diff.evidence["mean_similarity"], 0.8)
        single = run_check("consistency", "", outputs=["only one"])
        self.assertIsNone(single.passed)

    def test_latency_uses_slowest_successful_attempt(self):
        self.assertTrue(run_check("max_latency_ms", "x", outputs=["a", "b"], latencies=[100, 900], limit_ms=1000).passed)
        r = run_check("max_latency_ms", "x", outputs=["a", "b"], latencies=[100, 1500], limit_ms=1000)
        self.assertFalse(r.passed)
        self.assertEqual(r.evidence["max_latency_ms"], 1500)

    def test_not_empty(self):
        self.assertTrue(run_check("not_empty", "hi").passed)
        self.assertFalse(run_check("not_empty", "   ").passed)
        self.assertFalse(run_check("not_empty", "hi", min_chars=5).passed)

    def test_unknown_check_or_evaluator_fault_becomes_review_not_a_crash(self):
        ctx = CheckContext(case=CASE, outputs=("x",), latencies_ms=(1.0,))
        r = evaluate_check(Check("does_not_exist", {}), ctx)
        self.assertIsNone(r.passed)
        broken = evaluate_check(Check("exact_match", {}), ctx)  # missing required param
        self.assertIsNone(broken.passed)
        self.assertEqual(broken.evidence["error_type"], "KeyError")


if __name__ == "__main__":
    unittest.main()
