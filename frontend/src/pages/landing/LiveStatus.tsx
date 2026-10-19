import type { ReactNode } from "react";
import { StatusPill, type Tone } from "../../components/StatusPill";
import { ApiError, api } from "../../lib/api";
import type { ProviderState, Readiness } from "../../lib/types";
import { useAsyncData } from "../../lib/useAsyncData";

interface Indicator {
  tone: Tone;
  label: string;
}

const CHECKING: Indicator = { tone: "neutral", label: "Checking" };
const UNKNOWN: Indicator = { tone: "neutral", label: "Unknown" };

/** The model host is a separate service that is not always connected, so a missing model is a warning here, not an alarm. */
const MODEL: Record<ProviderState, Indicator> = {
  ok: { tone: "success", label: "Connected" },
  loading: { tone: "warning", label: "Loading" },
  not_configured: { tone: "neutral", label: "Not connected" },
  unreachable: { tone: "warning", label: "Not reachable" },
  error: { tone: "warning", label: "Error" },
};

function platformIndicator(readiness: Readiness): Indicator {
  const { database, redis } = readiness.checks;
  return database.status === "ok" && redis.status === "ok"
    ? { tone: "success", label: "Up" }
    : { tone: "danger", label: "Having problems" };
}

function modelIndicator(readiness: Readiness): Indicator {
  return MODEL[readiness.checks.provider.status as ProviderState] ?? UNKNOWN;
}

/**
 * Live status from the public readiness endpoint, read once when the page loads. The server caches
 * its probe of the model host, so many visitors do not add load to the host.
 */
export function LiveStatus() {
  const { data, error, loading } = useAsyncData(() => api.health.ready(), []);

  let platform: Indicator = UNKNOWN;
  let model: Indicator = UNKNOWN;
  let note: ReactNode = "The status could not be loaded. Try again shortly.";

  if (data) {
    platform = platformIndicator(data);
    model = modelIndicator(data);
    note =
      model === MODEL.ok ? (
        "Both are running. Checked when this page loaded."
      ) : (
        <>
          The model host is away. Chat requests return <code>provider_unavailable</code> until it
          is back.
        </>
      );
  } else if (loading) {
    platform = CHECKING;
    model = CHECKING;
    note = "Checking the platform and the model.";
  } else if (error instanceof ApiError && error.status === 503) {
    platform = { tone: "danger", label: "Having problems" };
    note = "The platform reports a database or cache problem. Try again shortly.";
  }

  return (
    <div className="status" role="status">
      <dl className="status__list">
        <div className="status__item">
          <dt>Platform</dt>
          <dd>
            <StatusPill tone={platform.tone}>{platform.label}</StatusPill>
          </dd>
        </div>
        <div className="status__item">
          <dt>N-ATLAS model</dt>
          <dd>
            <StatusPill tone={model.tone}>{model.label}</StatusPill>
          </dd>
        </div>
      </dl>
      <p className="status__note">{note}</p>
    </div>
  );
}
