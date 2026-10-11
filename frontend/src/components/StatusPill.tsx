import type { ReactNode } from "react";

export type Tone = "success" | "warning" | "danger" | "neutral" | "info";

export function StatusPill({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`pill pill--${tone}`}>{children}</span>;
}
