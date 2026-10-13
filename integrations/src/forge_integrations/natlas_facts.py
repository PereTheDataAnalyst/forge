"""Verified N-ATLAS facts.

Every value here was read from an official source on ``VERIFIED_ON``. Nothing is inferred.
When the official sources change, update this file and ``docs/n-atlas-verified-facts.md``
together.

Official sources:
  * LLM model card:  https://huggingface.co/NCAIR1/N-ATLaS
  * ASR model cards: https://huggingface.co/NCAIR1/Hausa-ASR
                     https://huggingface.co/NCAIR1/Igbo-ASR
                     https://huggingface.co/NCAIR1/Yoruba-ASR
                     https://huggingface.co/NCAIR1/NigerianAccentedEnglish
  * Challenge page:  https://ncair.nitda.gov.ng/naic/
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VERIFIED_ON = "2026-10-09"

LLM_MODEL_CARD_URL = "https://huggingface.co/NCAIR1/N-ATLaS"
CHALLENGE_URL = "https://ncair.nitda.gov.ng/naic/"

#: Language codes used by Forge. These are the languages the official sources list.
SUPPORTED_LANGUAGE_CODES: tuple[str, ...] = ("en", "ha", "ig", "yo")

LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "ha": "Hausa",
    "ig": "Igbo",
    "yo": "Yoruba",
}


@dataclass(frozen=True)
class LlmFacts:
    repo_id: str = "NCAIR1/N-ATLaS"
    architecture: str = "LlamaForCausalLM"
    base_model: str = "Llama-3 8B"
    hidden_size: int = 4096
    num_layers: int = 32
    attention_heads: int = 32
    key_value_heads: int = 8
    vocab_size: int = 128_256
    max_position_embeddings: int = 131_072
    #: The card's "Context Length" row and its Limitations section both say 8,092 tokens.
    context_length_tokens: int = 8_092
    training_tokens: int = 391_956_264  # dataset section of the card; the card text also says "over 400 million"
    languages: tuple[str, ...] = ("en", "ha", "ig", "yo")
    chat_template_requires_date_string: bool = True  # the official snippet passes date_string
    #: Values used in the official example snippet. They are an example, not a tuned recommendation.
    example_generation: tuple[tuple[str, float | int], ...] = (
        ("temperature", 0.1),
        ("repetition_penalty", 1.12),
        ("max_new_tokens", 1000),
    )
    gated: bool = True
    source: str = LLM_MODEL_CARD_URL


@dataclass(frozen=True)
class AsrFacts:
    language: str
    repo_id: str
    card_model_name: str
    architecture: str = "Whisper Small"
    parameters: str = "244M"
    base_model: str = "openai/whisper-small"
    max_seconds_per_inference: int = 30  # "Context Length: 30 seconds maximum per inference"
    recommended_sample_rate_hz: int = 16_000  # "Audio waveform (16kHz recommended)"
    gated: bool = True
    required_attribution: str = ""
    source: str = ""


LLM = LlmFacts()

ASR_MODELS: dict[str, AsrFacts] = {
    "ha": AsrFacts(
        language="ha",
        repo_id="NCAIR1/Hausa-ASR",
        card_model_name="Hausa-ASR-v1.0",
        required_attribution=(
            "Hausa-ASR is powered by Awarri Technologies in an initiative of the Federal "
            "Ministry of Communications, Innovation and Digital Economy"
        ),
        source="https://huggingface.co/NCAIR1/Hausa-ASR",
    ),
    "ig": AsrFacts(
        language="ig",
        repo_id="NCAIR1/Igbo-ASR",
        card_model_name="Igbo-ASR-v1.0",
        required_attribution=(
            "Igbo-ASR is powered by Awarri Technologies in an initiative of the Federal "
            "Ministry of Communications, Innovation and Digital Economy."
        ),
        source="https://huggingface.co/NCAIR1/Igbo-ASR",
    ),
    "yo": AsrFacts(
        language="yo",
        repo_id="NCAIR1/Yoruba-ASR",
        card_model_name="Yoruba-ASR-v1.0",
        required_attribution=(
            "Yoruba-ASR is developed by Awarri Technologies in partnership with the "
            "Federal Government of Nigeria."
        ),
        source="https://huggingface.co/NCAIR1/Yoruba-ASR",
    ),
    "en": AsrFacts(
        language="en",
        repo_id="NCAIR1/NigerianAccentedEnglish",
        card_model_name="NaijaEnglish-ASR-v1.0",
        required_attribution=(
            "NigerianAccentedEnglish is powered by Awarri Technologies and an initiative of "
            "the Federal Ministry of Communications, Innovation and Digital Economy."
        ),
        source="https://huggingface.co/NCAIR1/NigerianAccentedEnglish",
    ),
}

LLM_REQUIRED_ATTRIBUTION = (
    "N-ATLaS is an initiative of the Federal Ministry of Communications, Innovation and "
    "Digital Economy, and powered by Awarri Technologies."
)

#: Terms of Use summary. The LLM card and the four ASR cards carry the same terms.
LICENCE_SUMMARY: dict[str, Any] = {
    "name": (
        "Custom Terms of Use: an open-source research and innovation licence inspired by "
        "Apache 2.0 and MIT, with added restrictions. The Hub licence field is 'other'."
    ),
    "not_enterprise_or_commercial_system": True,
    "active_end_user_cap": 1000,
    "active_end_user_definition": (
        "An individual who directly interacts with model outputs within a rolling 30-day period."
    ),
    "above_cap": "A commercial licence from Awarri Technologies is required.",
    "attribution_required": True,
    "renamed_derivatives_suffix": "Powered by Awarri.",
    "derivatives_same_terms": True,
    "prohibited_uses": (
        "Surveillance or unlawful monitoring",
        "Discriminatory profiling or exclusionary practices",
        "Disinformation, impersonation, or synthetic fraud",
        "Military, intelligence, or weaponized deployment",
        "Exploitative, harmful, or unlawful applications",
    ),
    "governing_law": "Nigeria",
    "source": "Terms of Use section of each model card",
}

#: Facts about access that matter for integration design.
ACCESS_NOTES: tuple[str, ...] = (
    "The LLM and all four ASR repositories are gated on Hugging Face: the files can only be "
    "accessed after logging in and accepting the conditions.",
    "Programmatic downloads of gated Hugging Face repositories use an access token from an account "
    "that accepted the conditions. This is standard Hub behaviour; the model cards do not describe "
    "token usage.",
    "At verification time the LLM card states that no Hugging Face Inference Provider serves the model.",
    "The official model cards document local inference with the transformers library. They do not "
    "document a public hosted API.",
    "The challenge page lists N-ATLAS API credentials as support for shortlisted teams.",
)

#: The acronym is expanded differently by two official sources, so Forge copy should not expand it.
ACRONYM_NOTE = (
    "The challenge page expands N-ATLAS as 'Nigerian Atlas for Languages and AI at Scale'. "
    "The Terms of Use on the model cards expand N-ATLaS as 'Nigeria - Automatic Transcription "
    "and Language Systems'. Avoid expanding the acronym in product copy."
)

#: What the cards say about output quality. Forge adds no measurements of its own.
QUALITY_NOTES: tuple[str, ...] = (
    "The LLM card lists content translation between English and African languages as a use case. It "
    "does not claim measured translation quality.",
    "The LLM card reports a human-evaluation average of 2.69 out of 5 for Yoruba and says Yoruba has "
    "room for improvement in fluency and relevance.",
)

#: Differences between the model cards that integrators should know about.
CARD_NOTES: tuple[str, ...] = (
    "The Hausa-ASR card's explicit-processor snippet loads the processor from NCAIR-NG/Hausa while "
    "its pipeline snippet loads everything from NCAIR1/Hausa-ASR. Forge loads the processor from the "
    "same repository as the model unless an operator overrides it.",
    "None of the four ASR cards set a language token, task or generation parameter. Forge calls "
    "generate with the model's own generation configuration, as the cards do.",
    "The LLM card states a context length of 8,092 tokens while max_position_embeddings is 131,072.",
    *QUALITY_NOTES,
)


def asr_language_codes() -> tuple[str, ...]:
    return tuple(ASR_MODELS.keys())


def model_info_payload() -> dict[str, Any]:
    """Static, verified model information. Safe to return to any authenticated caller."""
    return {
        "verified_on": VERIFIED_ON,
        "llm": {
            "repo_id": LLM.repo_id,
            "architecture": LLM.architecture,
            "base_model": LLM.base_model,
            "context_length_tokens": LLM.context_length_tokens,
            "max_position_embeddings": LLM.max_position_embeddings,
            "languages": [{"code": c, "name": LANGUAGE_NAMES[c]} for c in LLM.languages],
            "source": LLM.source,
        },
        "asr": [
            {
                "language": a.language,
                "name": LANGUAGE_NAMES[a.language],
                "repo_id": a.repo_id,
                "architecture": a.architecture,
                "max_seconds_per_inference": a.max_seconds_per_inference,
                "recommended_sample_rate_hz": a.recommended_sample_rate_hz,
                "source": a.source,
            }
            for a in ASR_MODELS.values()
        ],
        "licence": {
            "active_end_user_cap": LICENCE_SUMMARY["active_end_user_cap"],
            "not_enterprise_or_commercial_system": True,
            "attribution": LLM_REQUIRED_ATTRIBUTION,
        },
    }
