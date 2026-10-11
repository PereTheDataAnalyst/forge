import type { ReactNode } from "react";

export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <h3 className="empty__title">{title}</h3>
      <div className="empty__body">{children}</div>
      {action ? <div className="empty__action">{action}</div> : null}
    </div>
  );
}
