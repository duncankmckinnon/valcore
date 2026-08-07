// The footer that tells a user why the primary action is unavailable. Each form
// still owns its button's `disabled` state; this only explains it. Showing one
// instruction at a time is deliberate — a list of five is as unhelpful as none.

import type { ReactNode } from "react";

type FormFooterProps = {
  blockers: string[];
  ready?: ReactNode;
  children: ReactNode;
};

export function FormFooter({ blockers, ready, children }: FormFooterProps): JSX.Element {
  const blocker = blockers[0];
  return (
    <div className="form-footer">
      {blocker ? (
        <div className="form-footer-blocker" role="status">
          {blocker}
        </div>
      ) : ready ? (
        <div className="form-footer-ready">{ready}</div>
      ) : null}
      <div className="form-footer-actions">{children}</div>
    </div>
  );
}
