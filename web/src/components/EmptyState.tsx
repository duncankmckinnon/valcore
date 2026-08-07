// The first-run / no-data placeholder. Composes into Table's `empty` prop so a
// bare "No X yet." string becomes an explanatory panel with an optional icon and
// a call-to-action.

import type { ReactNode } from "react";

type EmptyStateProps = {
  icon?: ReactNode;
  message: ReactNode;
  action?: ReactNode;
};

export function EmptyState({ icon, message, action }: EmptyStateProps): JSX.Element {
  return (
    <div className="empty-state">
      {icon !== undefined && <div className="empty-state-icon">{icon}</div>}
      <div className="empty-state-text">{message}</div>
      {action}
    </div>
  );
}
