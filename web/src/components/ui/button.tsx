import type { ButtonHTMLAttributes, ReactNode } from "react";

/**
 * Button styling, shared by `<button>` and by links that need to look like one.
 *
 * Exported as a function rather than wrapped in a polymorphic component so an
 * `<a>` stays an `<a>` and a `<button>` stays a `<button>`. Navigation and
 * action are different things to a keyboard and to a screen reader, and the
 * markup should say which is which.
 */

export type ButtonVariant = "primary" | "secondary" | "ghost";
export type ButtonSize = "sm" | "md";

const BASE =
  "inline-flex items-center justify-center gap-2 rounded-lg font-medium " +
  "transition-colors duration-150 " +
  "disabled:cursor-not-allowed disabled:opacity-45";

const VARIANTS: Record<ButtonVariant, string> = {
  // The product has no brand colour, so emphasis is contrast: the primary
  // action is the brightest thing on the page and there is only ever one.
  primary: "bg-foreground text-inverse hover:bg-white",
  secondary:
    "border border-border bg-raised text-foreground hover:border-border-strong hover:bg-border",
  ghost: "text-muted hover:bg-raised hover:text-foreground",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-[0.8125rem]",
  md: "h-9 px-4 text-sm",
};

export function buttonStyles({
  variant = "secondary",
  size = "md",
  className = "",
}: {
  variant?: ButtonVariant;
  size?: ButtonSize;
  className?: string;
} = {}): string {
  return [BASE, VARIANTS[variant], SIZES[size], className].filter(Boolean).join(" ");
}

export function Button({
  variant = "secondary",
  size = "md",
  className = "",
  children,
  ...props
}: {
  variant?: ButtonVariant;
  size?: ButtonSize;
  children: ReactNode;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      {...props}
      className={buttonStyles({ variant, size, className })}
    >
      {children}
    </button>
  );
}
