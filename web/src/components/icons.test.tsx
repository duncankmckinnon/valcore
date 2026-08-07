import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import {
  ChevronIcon,
  CompareIcon,
  DatasetIcon,
  EvaluatorIcon,
  InfoIcon,
  OverviewIcon,
  PlusIcon,
  RunIcon,
} from "./icons";

afterEach(() => {
  cleanup();
});

// Exercise every exported icon uniformly: they share the same contract, so the
// suite is table-driven rather than one bespoke block per icon.
const ICONS = [
  ["OverviewIcon", OverviewIcon],
  ["EvaluatorIcon", EvaluatorIcon],
  ["DatasetIcon", DatasetIcon],
  ["RunIcon", RunIcon],
  ["CompareIcon", CompareIcon],
  ["InfoIcon", InfoIcon],
  ["ChevronIcon", ChevronIcon],
  ["PlusIcon", PlusIcon],
] as const;

describe("icons", () => {
  it.each(ICONS)("%s renders a single <svg> element", (_name, Icon) => {
    const { container } = render(<Icon />);
    const svgs = container.querySelectorAll("svg");
    expect(svgs).toHaveLength(1);
  });

  it.each(ICONS)("%s defaults to size 16 on width and height", (_name, Icon) => {
    const { container } = render(<Icon />);
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("width")).toBe("16");
    expect(svg?.getAttribute("height")).toBe("16");
  });

  it.each(ICONS)("%s applies a passed size to width and height", (_name, Icon) => {
    const { container } = render(<Icon size={32} />);
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("width")).toBe("32");
    expect(svg?.getAttribute("height")).toBe("32");
  });

  it.each(ICONS)("%s uses a 24x24 viewBox", (_name, Icon) => {
    const { container } = render(<Icon />);
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("viewBox")).toBe("0 0 24 24");
  });

  it.each(ICONS)("%s strokes with currentColor and no fill", (_name, Icon) => {
    const { container } = render(<Icon />);
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("fill")).toBe("none");
    expect(svg?.getAttribute("stroke")).toBe("currentColor");
  });

  it.each(ICONS)("%s is hidden from the accessibility tree", (_name, Icon) => {
    const { container } = render(<Icon />);
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("aria-hidden")).toBe("true");
    expect(svg?.getAttribute("focusable")).toBe("false");
  });

  it.each(ICONS)("%s exposes no accessible name via title or desc", (_name, Icon) => {
    const { container } = render(<Icon />);
    expect(container.querySelector("svg title")).toBeNull();
    expect(container.querySelector("svg desc")).toBeNull();
  });

  it.each(ICONS)("%s spreads className onto the <svg>", (_name, Icon) => {
    const { container } = render(<Icon className="nav-icon" />);
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("class")).toContain("nav-icon");
  });
});
