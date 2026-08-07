// The header carried at the top of every page: title, optional supporting
// description, and an optional primary action. Owning the single <h1> here keeps
// pages from each minting their own heading.

import type { ReactNode } from "react";

type PageHeaderProps = {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
};

export function PageHeader({ title, description, action }: PageHeaderProps): JSX.Element {
  return (
    <div className="page-header">
      <div className="page-header-text">
        <h1 className="page-title">{title}</h1>
        {description !== undefined && <p className="page-description">{description}</p>}
      </div>
      {action}
    </div>
  );
}
