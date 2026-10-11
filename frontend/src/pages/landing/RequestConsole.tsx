import { useId, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { CodeBlock } from "../../components/CodeBlock";
import { DEFAULT_MODEL_ID, languages, type LanguageInfo } from "../../config";
import {
  RESPONSE_SHAPE,
  buildSnippet,
  type SnippetLanguage,
} from "../../lib/snippets";

const DEFAULT_PROMPTS: Record<LanguageInfo["code"], string> = {
  en: "Explain in two sentences what an API key is.",
  ha: "menene ake nufi da gwagwarmaya",
  ig: "Translate to Igbo: Good morning, how are you?",
  yo: "Translate to Yoruba: Good morning, how are you?",
};

const SNIPPET_TABS: { id: SnippetLanguage; label: string }[] = [
  { id: "curl", label: "cURL" },
  { id: "powershell", label: "PowerShell" },
  { id: "javascript", label: "JavaScript" },
  { id: "python", label: "Python" },
];

const TOKEN_CHOICES = [128, 256, 512, 1024];

/**
 * An interactive request builder. It shows the exact request the gateway accepts and the shape
 * of its response. It never calls a model and never displays generated text.
 */
export function RequestConsole() {
  const uid = useId();
  const [language, setLanguage] = useState<LanguageInfo["code"]>("en");
  const [prompt, setPrompt] = useState(DEFAULT_PROMPTS.en);
  const [edited, setEdited] = useState(false);
  const [temperature, setTemperature] = useState(0.1);
  const [maxTokens, setMaxTokens] = useState(256);
  const [view, setView] = useState<"request" | "response">("request");
  const [snippet, setSnippet] = useState<SnippetLanguage>("curl");

  function chooseLanguage(code: LanguageInfo["code"]) {
    setLanguage(code);
    if (!edited) setPrompt(DEFAULT_PROMPTS[code]);
  }

  const code = useMemo(
    () =>
      buildSnippet(snippet, {
        baseUrl: window.location.origin,
        model: DEFAULT_MODEL_ID,
        prompt,
        language,
        temperature,
        maxTokens,
      }),
    [snippet, prompt, language, temperature, maxTokens],
  );

  return (
    <div className="console" id="console">
      <div className="console__controls">
        <fieldset className="console__group">
          <legend className="console__legend">Language</legend>
          <div className="segmented" role="radiogroup" aria-label="Language">
            {languages.map((item) => (
              <label key={item.code} className="segmented__item">
                <input
                  type="radio"
                  name={`${uid}-language`}
                  value={item.code}
                  checked={language === item.code}
                  onChange={() => chooseLanguage(item.code)}
                />
                <span>{item.native}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <div className="console__group">
          <label className="console__legend" htmlFor={`${uid}-prompt`}>
            Prompt
          </label>
          <textarea
            id={`${uid}-prompt`}
            className="textarea"
            rows={4}
            maxLength={600}
            value={prompt}
            onChange={(event) => {
              setPrompt(event.target.value);
              setEdited(true);
            }}
          />
        </div>

        <div className="console__row">
          <div className="console__group">
            <label className="console__legend" htmlFor={`${uid}-temp`}>
              Temperature <span className="mono">{temperature.toFixed(1)}</span>
            </label>
            <input
              id={`${uid}-temp`}
              type="range"
              min={0}
              max={2}
              step={0.1}
              value={temperature}
              onChange={(event) => setTemperature(Number(event.target.value))}
            />
          </div>
          <div className="console__group">
            <label className="console__legend" htmlFor={`${uid}-tokens`}>
              Max tokens
            </label>
            <select
              id={`${uid}-tokens`}
              className="select"
              value={maxTokens}
              onChange={(event) => setMaxTokens(Number(event.target.value))}
            >
              {TOKEN_CHOICES.map((choice) => (
                <option key={choice} value={choice}>
                  {choice}
                </option>
              ))}
            </select>
          </div>
        </div>

        <p className="console__note">
          This builder shows requests. It does not call a model. To send one,{" "}
          <Link to="/register">create an API key</Link>.
        </p>
      </div>

      <div className="console__output">
        <div className="tabs" role="tablist" aria-label="Output">
          <button
            type="button"
            role="tab"
            id={`${uid}-tab-request`}
            aria-selected={view === "request"}
            aria-controls={`${uid}-panel`}
            className="tabs__tab"
            onClick={() => setView("request")}
          >
            Request
          </button>
          <button
            type="button"
            role="tab"
            id={`${uid}-tab-response`}
            aria-selected={view === "response"}
            aria-controls={`${uid}-panel`}
            className="tabs__tab"
            onClick={() => setView("response")}
          >
            Response shape
          </button>
        </div>
        <div
          role="tabpanel"
          id={`${uid}-panel`}
          aria-labelledby={`${uid}-tab-${view}`}
          className="console__panel"
        >
          {view === "request" ? (
            <>
              <div className="segmented segmented--dark" role="radiogroup" aria-label="Code language">
                {SNIPPET_TABS.map((tab) => (
                  <label key={tab.id} className="segmented__item">
                    <input
                      type="radio"
                      name={`${uid}-snippet`}
                      value={tab.id}
                      checked={snippet === tab.id}
                      onChange={() => setSnippet(tab.id)}
                    />
                    <span>{tab.label}</span>
                  </label>
                ))}
              </div>
              <CodeBlock label="POST /api/v1/chat" code={code} />
            </>
          ) : (
            <>
              <CodeBlock label="200 OK" code={RESPONSE_SHAPE} />
              <p className="console__caption">
                Values in angle brackets are placeholders, not model output.
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
