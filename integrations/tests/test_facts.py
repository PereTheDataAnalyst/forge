import json
import unittest

from forge_integrations import natlas_facts as f


class FactsTests(unittest.TestCase):
    def test_four_asr_models_cover_exactly_the_official_languages(self):
        self.assertEqual(set(f.ASR_MODELS), {"ha", "ig", "yo", "en"})
        self.assertEqual(set(f.SUPPORTED_LANGUAGE_CODES), set(f.ASR_MODELS))
        self.assertEqual(set(f.LLM.languages), set(f.SUPPORTED_LANGUAGE_CODES))

    def test_repo_ids_match_the_official_cards(self):
        self.assertEqual(f.LLM.repo_id, "NCAIR1/N-ATLaS")
        self.assertEqual(
            {k: v.repo_id for k, v in f.ASR_MODELS.items()},
            {
                "ha": "NCAIR1/Hausa-ASR",
                "ig": "NCAIR1/Igbo-ASR",
                "yo": "NCAIR1/Yoruba-ASR",
                "en": "NCAIR1/NigerianAccentedEnglish",
            },
        )

    def test_window_and_context_numbers(self):
        self.assertTrue(all(a.max_seconds_per_inference == 30 for a in f.ASR_MODELS.values()))
        self.assertTrue(all(a.recommended_sample_rate_hz == 16_000 for a in f.ASR_MODELS.values()))
        self.assertEqual(f.LLM.context_length_tokens, 8092)

    def test_every_model_has_attribution_and_source(self):
        for a in f.ASR_MODELS.values():
            self.assertTrue(a.required_attribution)
            self.assertTrue(a.source.startswith("https://huggingface.co/NCAIR1/"))
        self.assertIn("Awarri Technologies", f.LLM_REQUIRED_ATTRIBUTION)

    def test_payload_is_json_serialisable_and_has_no_secret_like_keys(self):
        payload = f.model_info_payload()
        text = json.dumps(payload)
        for banned in ("token", "secret", "password", "api_key"):
            self.assertNotIn(banned, text.lower().replace("tokens", ""))
        self.assertEqual(len(payload["asr"]), 4)
        self.assertEqual(payload["licence"]["active_end_user_cap"], 1000)


if __name__ == "__main__":
    unittest.main()
