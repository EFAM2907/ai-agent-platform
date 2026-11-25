import type { ReactNode } from "react";

// Common page pattern of the admin views: 48px top bar with the page title,
// then a centered content column (max ~1100px) with 24px padding and 24px
// between sections.
export function PageShell({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="view">
      <header className="page-bar">
        <h1 className="page-bar__title">{title}</h1>
        {actions && <div className="page-bar__actions">{actions}</div>}
      </header>
      <div className="view__body">
        {description && <p className="view__description">{description}</p>}
        {children}
      </div>
    </div>
  );
}
