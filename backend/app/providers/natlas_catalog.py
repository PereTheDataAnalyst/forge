"""Facts about the official N-ATLAS models.

Every value below was taken from the official model cards published by NCAIR on Hugging Face
(version 1.0, September 2025) and the National AI Innovation Challenge page. Nothing here is
inferred. Fields that were not verified are left as None instead of guessed.
"""

from dataclasses import dataclass

LLM_MODEL_ID = "NCAIR1/N-ATLaS"
LLM_SOURCE_URL = "https://huggingface.co/NCAIR1/N-ATLaS"
LICENSE_NAME = "N-ATLaS Terms of Use (Open-Source Research and Innovation License)"

# Required attribution text published in the model card Terms of Use.
LLM_ATTRIBUTION = (
    "N-ATLaS is an initiative of the Federal Ministry of Communications, Innovation and "
    "Digital Economy, and powered by Awarri Technologies."
)

# Text languages named in the model card. ISO 639-1 codes.
LLM_LANGUAGES: tuple[str, ...] = ("en", "ha", "ig", "yo")

# The model card lists "Context Length: 8,092 tokens" and "Max Position Embeddings: 131,072".
LLM_CONTEXT_LENGTH_TOKENS = 8092
LLM_MAX_POSITION_EMBEDDINGS = 131072


@dataclass(frozen=True, slots=True)
class OfficialModel:
    id: str
    kind: str
    display_name: str
    base_model: str | None
    parameters: str | None
    languages: tuple[str, ...]
    source_url: str
    attribution: str | None
    notes: str


OFFICIAL_MODELS: tuple[OfficialModel, ...] = (
    OfficialModel(
        id=LLM_MODEL_ID,
        kind="llm",
        display_name="N-ATLaS LLM",
        base_model="Llama-3 8B (LlamaForCausalLM)",
        parameters="8B",
        languages=LLM_LANGUAGES,
        source_url=LLM_SOURCE_URL,
        attribution=LLM_ATTRIBUTION,
        notes="Gated repository. Accept the terms on Hugging Face before downloading.",
    ),
    OfficialModel(
        id="NCAIR1/NigerianAccentedEnglish",
        kind="asr",
        display_name="Nigerian Accented English ASR",
        base_model="openai/whisper-small",
        parameters="244M",
        languages=("en",),
        source_url="https://huggingface.co/NCAIR1/NigerianAccentedEnglish",
        attribution=(
            "NigerianAccentedEnglish is powered by Awarri Technologies and an initiative of "
            "the Federal Ministry of Communications, Innovation and Digital Economy."
        ),
        notes="16 kHz audio recommended. Maximum 30 seconds per inference.",
    ),
    OfficialModel(
        id="NCAIR1/Yoruba-ASR",
        kind="asr",
        display_name="Yoruba ASR",
        base_model="openai/whisper-small",
        parameters="244M",
        languages=("yo",),
        source_url="https://huggingface.co/NCAIR1/Yoruba-ASR",
        attribution=(
            "Yoruba-ASR is developed by Awarri Technologies in partnership with the Federal "
            "Government of Nigeria."
        ),
        notes="16 kHz audio recommended. Maximum 30 seconds per inference.",
    ),
    OfficialModel(
        id="NCAIR1/Hausa-ASR",
        kind="asr",
        display_name="Hausa ASR",
        base_model=None,
        parameters=None,
        languages=("ha",),
        source_url="https://huggingface.co/NCAIR1/Hausa-ASR",
        attribution=None,
        notes="Architecture and attribution text not yet verified. Read the model card first.",
    ),
    OfficialModel(
        id="NCAIR1/Igbo-ASR",
        kind="asr",
        display_name="Igbo ASR",
        base_model=None,
        parameters=None,
        languages=("ig",),
        source_url="https://huggingface.co/NCAIR1/Igbo-ASR",
        attribution=None,
        notes="Architecture and attribution text not yet verified. Read the model card first.",
    ),
)
