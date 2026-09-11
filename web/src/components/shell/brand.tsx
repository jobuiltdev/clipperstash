/**
 * The wordmark.
 *
 * The glyph is a timeline with a segment marked out of it — which is what the
 * product does. Drawn inline as geometry rather than shipped as an asset, so it
 * inherits the current text colour and stays crisp at any size.
 */
export function Brand({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2.5 ${className}`}>
      <svg
        viewBox="0 0 20 20"
        aria-hidden
        className="size-[18px] shrink-0"
        fill="none"
      >
        <rect
          x="1"
          y="5"
          width="18"
          height="10"
          rx="2.5"
          stroke="currentColor"
          strokeWidth="1.5"
          opacity="0.45"
        />
        <rect x="7.5" y="7.5" width="5" height="5" rx="1" fill="currentColor" />
      </svg>
      <span className="text-[0.9375rem] font-semibold tracking-tight">ClipperStash</span>
    </span>
  );
}
