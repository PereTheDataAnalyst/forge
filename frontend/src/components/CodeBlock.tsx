import { CopyButton } from "./CopyButton";

export function CodeBlock({ code, label }: { code: string; label: string }) {
  return (
    <div className="code">
      <div className="code__bar">
        <span className="code__label">{label}</span>
        <CopyButton text={code} />
      </div>
      <pre className="code__pre" tabIndex={0}>
        <code>{code}</code>
      </pre>
    </div>
  );
}
