// Hand-rolled inline SVG icons. Per the design brief this app takes no icon
// dependency, so each glyph is a small stroke-based SVG that inherits the
// surrounding text colour via `currentColor`.

import type { SVGProps } from "react";

export function InfoIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 16 16"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      <circle cx="8" cy="8" r="6.5" />
      <line x1="8" y1="7.25" x2="8" y2="11" />
      <circle cx="8" cy="4.75" r="0.5" fill="currentColor" stroke="none" />
    </svg>
  );
}
