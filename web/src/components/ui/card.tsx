import type { ReactNode } from "react";

/**
 * The one container in the product.
 *
 * Every panel, list and detail block is a `Card`, so the interface has a single
 * rule for depth: page, card, and raised element inside a card. Anything that
 * needed a fourth level would be a sign the page has too much on it.
 */

export function Card({
  children,
  className = "",
  interactive = false,
}: {
  children: ReactNode;
  className?: string;
  /** Hover affordance for a whole card that is itself a link. */
  interactive?: boolean;
}) {
  return (
    <div
      className={[
        "rounded-xl border border-border bg-surface",
        interactive
          ? "transition-colors duration-150 hover:border-border-strong hover:bg-raised"
          : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {children}
    </div>
  );
}

/**
 * A card's title row.
 *
 * `actions` sits opposite the title and wraps beneath it on narrow screens, so
 * a refresh control never pushes a heading off the edge.
 */
export function CardHeader({
  title,
  description,
  actions,
  headingId,
  level = "h2",
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  headingId: string;
  level?: "h2" | "h3";
}) {
  const Heading = level;
  return (
    <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3 border-b border-border px-5 py-4">
      <div className="min-w-0 space-y-1">
        <Heading
          id={headingId}
          className="text-[0.9375rem] font-semibold text-foreground"
        >
          {title}
        </Heading>
        {description && (
          <p className="max-w-prose text-sm leading-relaxed text-muted">{description}</p>
        )}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

export function CardBody({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={`px-5 py-4 ${className}`}>{children}</div>;
}

export function CardFooter({ children }: { children: ReactNode }) {
  return (
    <div className="border-t border-border px-5 py-3 text-sm text-muted">{children}</div>
  );
}

/**
 * A titled card, which is nearly every card.
 *
 * Kept as one component so a panel is a single element at the call site and the
 * heading is never accidentally omitted — `headingId` is required, and the
 * section is labelled by it.
 */
export function Panel({
  title,
  description,
  actions,
  headingId,
  children,
  bodyClassName = "",
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  headingId: string;
  children: ReactNode;
  bodyClassName?: string;
}) {
  return (
    <section aria-labelledby={headingId}>
      <Card>
        <CardHeader
          title={title}
          description={description}
          actions={actions}
          headingId={headingId}
        />
        <CardBody className={bodyClassName}>{children}</CardBody>
      </Card>
    </section>
  );
}
