import Link from "next/link";
import type { ReactNode } from "react";

/**
 * Page furniture: the container every route sits in, its heading, and the
 * breadcrumb trail for the nested dashboard views.
 *
 * One max-width and one gutter across the product, so pages do not drift apart
 * as they are edited.
 */

export function PageContainer({ children }: { children: ReactNode }) {
  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 sm:py-10">{children}</div>
  );
}

export type Crumb = { label: string; href?: string };

export function Breadcrumbs({ trail }: { trail: Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb">
      <ol className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted">
        {trail.map((crumb, index) => {
          const last = index === trail.length - 1;
          return (
            <li key={`${crumb.label}-${index}`} className="flex items-center gap-2">
              {index > 0 && (
                <span aria-hidden className="text-border-strong">
                  /
                </span>
              )}
              {crumb.href && !last ? (
                <Link
                  href={crumb.href}
                  className="rounded transition-colors hover:text-foreground"
                >
                  {crumb.label}
                </Link>
              ) : (
                <span aria-current={last ? "page" : undefined} className="text-foreground">
                  {crumb.label}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  /** A short label above the title, for context the title itself should not carry. */
  eyebrow?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
      <div className="min-w-0 space-y-1.5">
        {eyebrow && (
          <p className="text-[0.6875rem] font-medium uppercase tracking-wider text-faint">
            {eyebrow}
          </p>
        )}
        <h1 className="text-2xl font-semibold tracking-tight sm:text-[1.75rem]">{title}</h1>
        {description && (
          <p className="max-w-2xl text-sm leading-relaxed text-muted">{description}</p>
        )}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
