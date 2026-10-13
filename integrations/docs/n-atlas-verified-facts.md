# N-ATLAS facts used by Forge (verified 2026-10-09)

Everything on this page was read from the official sources on the date above. Nothing is inferred.
The same values live in `src/forge_integrations/natlas_facts.py`. A test (`tests/test_facts.py`) pins the
key values there; this page is kept in step by hand. When the official sources change, update both
together and change the verification date.

Official sources:

- Language model card: <https://huggingface.co/NCAIR1/N-ATLaS>
- Speech model cards: <https://huggingface.co/NCAIR1/Hausa-ASR>, <https://huggingface.co/NCAIR1/Igbo-ASR>,
  <https://huggingface.co/NCAIR1/Yoruba-ASR>, <https://huggingface.co/NCAIR1/NigerianAccentedEnglish>
- Challenge page: <https://ncair.nitda.gov.ng/naic/>

If a fact you need is not on this page, it is not verified. Do not add it from memory: fetch the card,
quote it, and update `natlas_facts.py`.

## Language model

| Item | Value |
| --- | --- |
| Repository | `NCAIR1/N-ATLaS` |
| Architecture | `LlamaForCausalLM`, based on Llama-3 8B |
| Languages | English (`en`), Hausa (`ha`), Igbo (`ig`), Yoruba (`yo`) |
| Context length | 8,092 tokens (the card's table and its Limitations section agree) |
| `max_position_embeddings` | 131,072 (differs from the stated context length; the card does not explain the gap) |
| Access | Gated on Hugging Face: log in and accept the conditions |
| Hosted inference | At verification time the card says no Hugging Face Inference Provider serves it |
| How the card shows use | Local inference with the `transformers` library |
| Chat template | The official snippet passes a `date_string` |
| Example generation settings | temperature 0.1, repetition penalty 1.12, 1000 new tokens (an example from the card, not a tuned recommendation) |

## Speech recognition (four separate models)

| Language | Repository | Card model name |
| --- | --- | --- |
| Hausa | `NCAIR1/Hausa-ASR` | Hausa-ASR-v1.0 |
| Igbo | `NCAIR1/Igbo-ASR` | Igbo-ASR-v1.0 |
| Yoruba | `NCAIR1/Yoruba-ASR` | Yoruba-ASR-v1.0 |
| Nigerian-accented English | `NCAIR1/NigerianAccentedEnglish` | NaijaEnglish-ASR-v1.0 |

All four: Whisper Small (244M parameters, base `openai/whisper-small`), audio at 16 kHz, at most
30 seconds per inference, gated on Hugging Face. They are four models, one per language. There is no
single multilingual speech model, so Forge routes by the language the caller states and never guesses.

## Required attribution

Each card carries its own wording. Use it exactly where the model's output is shown or the model is named.

- Language model: "N-ATLaS is an initiative of the Federal Ministry of Communications, Innovation and Digital Economy, and powered by Awarri Technologies."
- Hausa-ASR: "Hausa-ASR is powered by Awarri Technologies in an initiative of the Federal Ministry of Communications, Innovation and Digital Economy"
- Igbo-ASR: "Igbo-ASR is powered by Awarri Technologies in an initiative of the Federal Ministry of Communications, Innovation and Digital Economy."
- Yoruba-ASR: "Yoruba-ASR is developed by Awarri Technologies in partnership with the Federal Government of Nigeria."
- NigerianAccentedEnglish: "NigerianAccentedEnglish is powered by Awarri Technologies and an initiative of the Federal Ministry of Communications, Innovation and Digital Economy."

## Terms of use (summary, read the card for the full text)

- A custom licence described as an open-source research and innovation licence inspired by Apache 2.0 and MIT, with added restrictions. The Hub licence field is `other`.
- Not for an enterprise or commercial system. Up to 1,000 active end users, meaning individuals who directly interact with model outputs within a rolling 30-day period. Above that, a commercial licence from Awarri Technologies is required.
- Attribution is required. A derivative that is renamed must carry the suffix "Powered by Awarri."
- Derivatives carry the same terms.
- Prohibited: surveillance or unlawful monitoring; discriminatory profiling or exclusionary practices; disinformation, impersonation or synthetic fraud; military, intelligence or weaponised deployment; exploitative, harmful or unlawful applications.
- Governing law: Nigeria.

Forge's own policy follows from this: it shows the attribution, it states the 1,000 user cap where it matters
(for example in `get_model_info`), and it does not claim the platform is an enterprise or commercial product.

## What the cards say about quality

- The language model card lists content translation between English and African languages as a use case. It does not claim a measured translation quality, and Forge does not either. The MCP `translate` tool says so in every result.
- The card reports a human-evaluation average of 2.69 out of 5 for Yoruba and says Yoruba has room for improvement in fluency and relevance.
- Forge adds no accuracy, BLEU, WER or other quality figure of its own. The Evaluation Lab reports how many developer-written checks held, which is a different thing (see `evaluation.md`).

## Differences between the cards that integrators should know about

- The Hausa-ASR card's explicit-processor snippet loads the processor from `NCAIR-NG/Hausa`, while its pipeline snippet loads everything from `NCAIR1/Hausa-ASR`. Forge loads the processor from the same repository as the model. An operator can override that per language with `ASR_PROCESSOR_OVERRIDES` (for example `ha=NCAIR-NG/Hausa`), at their own risk.
- None of the four speech cards sets a language token, task or generation parameter. Forge calls `generate` with the model's own generation configuration, as the cards do.
- The acronym is expanded differently by two official sources (the challenge page and the model cards' terms), so Forge copy does not expand it.

## Access and what that means for the build

- All five repositories are gated. Downloading them needs a Hugging Face access token from an account that accepted each model's conditions, supplied through the standard `HF_TOKEN` environment variable. Forge never reads, stores or logs the token.
- The model cards document local inference. They do not document a public hosted N-ATLAS API. The challenge page lists N-ATLAS API credentials as support for shortlisted teams. Until a team has them, text generation in Forge goes through whatever gateway Agent 1's platform provides, and speech recognition goes through the Forge ASR worker that runs the four official models locally.
- Forge does not claim streaming, any language other than the four above, or any capability the cards do not list.
