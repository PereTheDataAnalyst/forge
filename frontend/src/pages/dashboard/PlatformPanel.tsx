import { Link } from "react-router-dom";
import { StatusPill } from "../../components/StatusPill";
import { layersByStatus, type Layer } from "../../config";

function FeatureRow({ layer }: { layer: Layer }) {
  const available = layer.status === "available";
  const target = layer.appPath ?? layer.docsPath;
  const verb = layer.appPath ? "Open" : "Read";
  return (
    <li className="features__item">
      <div className="features__main">
        <h4 className="features__name">{layer.name}</h4>
        <p className="features__summary">{layer.summary}</p>
      </div>
      <div className="features__side">
        <StatusPill tone={available ? "success" : "neutral"}>
          {available ? "Available" : "In development"}
        </StatusPill>
        {available && target ? (
          <Link to={target} className="btn btn--ghost btn--sm" aria-label={`${verb} ${layer.name}`}>
            {verb}
          </Link>
        ) : null}
      </div>
    </li>
  );
}

/** Every capability and its real status, read from the registry in config.ts. */
export function PlatformPanel() {
  const { available, inDevelopment } = layersByStatus;
  return (
    <section className="panel" aria-labelledby="platform-panel-title">
      <div className="panel__header">
        <h2 id="platform-panel-title" className="panel__title">
          Platform
        </h2>
        <span className="muted">
          {available.length} available, {inDevelopment.length} in development
        </span>
      </div>
      <div className="panel__body">
        <h3 className="features__group">Available now</h3>
        <ul className="features">
          {available.map((layer) => (
            <FeatureRow key={layer.name} layer={layer} />
          ))}
        </ul>
        <h3 className="features__group">In development</h3>
        <ul className="features">
          {inDevelopment.map((layer) => (
            <FeatureRow key={layer.name} layer={layer} />
          ))}
        </ul>
      </div>
    </section>
  );
}
