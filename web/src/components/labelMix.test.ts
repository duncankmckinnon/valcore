import { describe, expect, it } from "vitest";
import { apportion, evenSplit, toProportions, totalPercent } from "./labelMix";

describe("totalPercent", () => {
  it("sums the given percents", () => {
    expect(totalPercent({ pass: 60, fail: 40 })).toBe(100);
  });

  it("treats a missing entry as zero rather than NaN", () => {
    expect(totalPercent({ pass: 100, fail: undefined as unknown as number })).toBe(100);
  });
});

describe("evenSplit", () => {
  it("splits evenly when the label count divides 100", () => {
    expect(evenSplit(["pass", "fail"])).toEqual({ pass: 50, fail: 50 });
  });

  it("totals exactly 100 when the split is not whole", () => {
    const split = evenSplit(["pass", "fail", "borderline"]);

    expect(totalPercent(split)).toBe(100);
    expect(split).toEqual({ pass: 34, fail: 33, borderline: 33 });
  });
});

describe("toProportions", () => {
  it("converts whole percents to shares summing to one", () => {
    expect(toProportions({ pass: 25, fail: 75 })).toEqual({ pass: 0.25, fail: 0.75 });
  });

  it("drops labels at zero so the payload says nothing about them", () => {
    // The API reads an omitted label as "no rows", which is what a zero percent means.
    expect(toProportions({ pass: 100, fail: 0 })).toEqual({ pass: 1 });
  });
});

describe("apportion", () => {
  it("splits evenly when the shares divide the count", () => {
    expect(apportion({ pass: 50, fail: 50 }, 10)).toEqual({ pass: 5, fail: 5 });
  });

  it("assigns exactly count rows when the split is not whole", () => {
    const rows = apportion({ pass: 34, fail: 33, borderline: 33 }, 10);
    const total = Object.values(rows).reduce((sum, value) => sum + value, 0);

    expect(total).toBe(10);
  });

  it("gives the leftover row to the largest remainder", () => {
    // 0.8*9 = 7.2 and 0.2*9 = 1.8, so 'pass' holds the larger remainder. 'fail' sorts
    // first alphabetically, so an alphabetical rule would hand it the ninth row instead.
    expect(apportion({ fail: 80, pass: 20 }, 9)).toEqual({ fail: 7, pass: 2 });
  });

  it("breaks remainder ties by label name so the preview is stable", () => {
    expect(apportion({ pass: 70, fail: 25, borderline: 5 }, 10)).toEqual({
      pass: 7,
      fail: 2,
      borderline: 1,
    });
  });

  it("drops labels that round down to no rows", () => {
    const rows = apportion({ pass: 90, fail: 10 }, 2);

    expect(rows.fail).toBeUndefined();
    expect(rows.pass).toBe(2);
  });

  it("normalises shares that do not total 100 so the count still adds up", () => {
    // Mirrors the server: spending the slack would leave rows unassigned.
    const rows = apportion({ pass: 33, fail: 33, borderline: 33 }, 100);
    const total = Object.values(rows).reduce((sum, value) => sum + value, 0);

    expect(total).toBe(100);
  });

  it("returns nothing when there is nothing to distribute", () => {
    expect(apportion({}, 10)).toEqual({});
    expect(apportion({ pass: 100 }, 0)).toEqual({});
  });
});
