// Hand-rolled inline SVG icons. No icon library — the app carries no dependency
// for a handful of decorative glyphs. Every icon is stroke-only and tints from
// `currentColor` so the nav active state can recolor it without prop plumbing.

import type { ReactNode } from "react";

type IconProps = { className?: string; size?: number };

// The stroke geometry differs per icon, but every wrapper shares the same
// attributes: a fixed 24x24 coordinate space scaled to `size`, no fill, and
// `aria-hidden` so decorative marks never leak an accessible name.
function Svg({
  className,
  size = 16,
  children,
}: IconProps & { children: ReactNode }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export function OverviewIcon(props: IconProps): JSX.Element {
  return (
    <Svg {...props}>
      <rect x="4" y="4" width="6" height="6" />
      <rect x="14" y="4" width="6" height="6" />
      <rect x="4" y="14" width="6" height="6" />
      <rect x="14" y="14" width="6" height="6" />
    </Svg>
  );
}

export function EvaluatorIcon(props: IconProps): JSX.Element {
  return (
    <Svg {...props}>
      <path d="M12 3 L21 12 L12 21 L3 12 Z" />
    </Svg>
  );
}

export function DatasetIcon(props: IconProps): JSX.Element {
  return (
    <Svg {...props}>
      <line x1="4" y1="7" x2="20" y2="7" />
      <line x1="4" y1="12" x2="20" y2="12" />
      <line x1="4" y1="17" x2="20" y2="17" />
    </Svg>
  );
}

export function RunIcon(props: IconProps): JSX.Element {
  return (
    <Svg {...props}>
      <path d="M7 5 L19 12 L7 19 Z" />
    </Svg>
  );
}

export function CompareIcon(props: IconProps): JSX.Element {
  return (
    <Svg {...props}>
      <path d="M8 7 L4 11 L8 15" />
      <line x1="4" y1="11" x2="20" y2="11" />
      <path d="M16 9 L20 13 L16 17" />
      <line x1="20" y1="13" x2="4" y2="13" />
    </Svg>
  );
}

export function InfoIcon(props: IconProps): JSX.Element {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="9" />
      <line x1="12" y1="11" x2="12" y2="16" />
      <line x1="12" y1="8" x2="12" y2="8" />
    </Svg>
  );
}

export function ChevronIcon(props: IconProps): JSX.Element {
  return (
    <Svg {...props}>
      <path d="M6 9 L12 15 L18 9" />
    </Svg>
  );
}

export function PlusIcon(props: IconProps): JSX.Element {
  return (
    <Svg {...props}>
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </Svg>
  );
}
