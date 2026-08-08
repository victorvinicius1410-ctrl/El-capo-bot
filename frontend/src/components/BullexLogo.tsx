import { useState } from "react";
import { cn } from "@/lib/utils";

export const BULLEX_BRAND_NAME = "Bullex";

/** Logo oficial Bullex (wordmark branco) — fonte canônica do site. */
export const BULLEX_LOGO_URL =
  "https://bull-ex.com/wp-content/uploads/2024/09/logo-bullex-white-1.webp";

/** Cópia local publicada em `/branding` (fallback se o CDN falhar). */
export const BULLEX_LOGO_LOCAL = "/branding/logo-bullex-white-1.webp";

export interface BullexLogoProps {
  className?: string;
  alt?: string;
  compact?: boolean;
}

/**
 * Marca Bullex com a logo oficial (webp branco).
 *
 * Ordem: CDN oficial → asset local → SVG embutido (nunca ícone quebrado).
 */
export function BullexLogo({ className, alt, compact = false }: BullexLogoProps) {
  const [sourceIndex, setSourceIndex] = useState(0);
  const label = alt ?? BULLEX_BRAND_NAME;
  const sizeClass = compact ? "bullex-logo-compact" : "bullex-logo-full";
  const sources = [BULLEX_LOGO_URL, BULLEX_LOGO_LOCAL] as const;

  if (sourceIndex >= sources.length) {
    return <BullexMarkSvg className={cn("bullex-logo", sizeClass, className)} title={label} />;
  }

  return (
    <span className={cn("bullex-logo-frame", sizeClass, className)}>
      <img
        src={sources[sourceIndex]}
        alt={label}
        className={cn("bullex-logo object-contain object-left", sizeClass)}
        loading="lazy"
        decoding="async"
        referrerPolicy="no-referrer"
        onError={() => setSourceIndex((current) => current + 1)}
      />
    </span>
  );
}

/** Wordmark SVG — fallback se CDN e asset local falharem. */
export function BullexMarkSvg({
  className,
  title = BULLEX_BRAND_NAME,
}: {
  className?: string;
  title?: string;
}) {
  return (
    <svg
      className={cn("bullex-logo bullex-mark-svg", className)}
      viewBox="0 0 220 48"
      role="img"
      aria-label={title}
      xmlns="http://www.w3.org/2000/svg"
    >
      <title>{title}</title>
      <defs>
        <linearGradient id="bullexMarkGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#7ef0f3" />
          <stop offset="100%" stopColor="#25dbe0" />
        </linearGradient>
      </defs>
      <rect x="0" y="4" width="40" height="40" rx="10" fill="url(#bullexMarkGrad)" />
      <path
        d="M12 14h12.2c5.4 0 8.8 2.7 8.8 7 0 2.8-1.4 4.9-3.9 6.1 3.2 1.1 5.1 3.5 5.1 7 0 4.8-3.8 7.9-10 7.9H12V14zm7.2 10.4h5c2.3 0 3.6-1.1 3.6-2.9s-1.3-2.8-3.6-2.8h-5v5.7zm0 12.2h5.6c2.7 0 4.2-1.3 4.2-3.3s-1.5-3.2-4.2-3.2h-5.6v6.5z"
        fill="#001014"
      />
      <text
        x="52"
        y="33"
        fill="#f4feff"
        fontFamily="Inter, Segoe UI, Helvetica, Arial, sans-serif"
        fontSize="26"
        fontWeight="780"
        letterSpacing="1.6"
      >
        BULLEX
      </text>
    </svg>
  );
}
