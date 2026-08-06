// Offline mirror of the server-side label-mix apportionment (`_apportion` in
// src/valcore/datagen.py), so the editor can show the row counts the model will actually
// be asked for rather than a percentage the user has to convert in their head. Pure
// functions, no React; the server stays authoritative and still returns 422 on mismatch.
//
// The editor works in whole percents while the API takes proportions, so the two live
// side by side here: `toProportions` is the conversion at the boundary, and everything
// else reasons in percents.

/** Label -> whole percent of the requested row count. */
export type LabelMixPercents = Record<string, number>;

export const TOTAL_PERCENT = 100;

/** Sum of the given percents, treating a missing or blank entry as zero. */
export function totalPercent(percents: LabelMixPercents): number {
  return Object.values(percents).reduce((sum, percent) => sum + (percent || 0), 0);
}

/**
 * Split `TOTAL_PERCENT` as evenly as possible across `labels`.
 *
 * The remainder goes to the earliest labels, so the result always totals exactly 100 and
 * a three-label split reads 34/33/33 rather than 33/33/33 with a point unaccounted for.
 */
export function evenSplit(labels: string[]): LabelMixPercents {
  const base = Math.floor(TOTAL_PERCENT / labels.length);
  let remainder = TOTAL_PERCENT - base * labels.length;
  const percents: LabelMixPercents = {};
  for (const label of labels) {
    percents[label] = base + (remainder > 0 ? 1 : 0);
    if (remainder > 0) remainder -= 1;
  }
  return percents;
}

/**
 * Convert whole percents to the proportions the API expects.
 *
 * Labels at zero percent are dropped: the API reads an omitted label as "no rows", which
 * is what a zero means, and sending 0.0 would only make the payload noisier.
 */
export function toProportions(percents: LabelMixPercents): Record<string, number> {
  const proportions: Record<string, number> = {};
  for (const [label, percent] of Object.entries(percents)) {
    if (percent > 0) proportions[label] = percent / TOTAL_PERCENT;
  }
  return proportions;
}

/**
 * Apportion `count` rows across the given percents, largest-remainder.
 *
 * Mirrors `_apportion` so the preview matches the prompt: shares are normalised by their
 * own total, each label takes its whole part, and the leftover rows go to the largest
 * fractional parts with ties broken by label name. Labels apportioned zero rows are
 * dropped, exactly as the server drops them.
 */
export function apportion(percents: LabelMixPercents, count: number): Record<string, number> {
  const entries = Object.entries(percents).filter(([, percent]) => percent > 0);
  const total = entries.reduce((sum, [, percent]) => sum + percent, 0);
  if (total === 0 || count < 1) return {};

  const exact = new Map(entries.map(([label, percent]) => [label, (percent / total) * count]));
  const counts = new Map([...exact].map(([label, value]) => [label, Math.floor(value)]));
  const assigned = [...counts.values()].reduce((sum, value) => sum + value, 0);

  const byRemainder = [...exact.keys()].sort((a, b) => {
    const remainderA = exact.get(a)! - counts.get(a)!;
    const remainderB = exact.get(b)! - counts.get(b)!;
    if (remainderA !== remainderB) return remainderB - remainderA;
    return a < b ? -1 : 1;
  });
  for (const label of byRemainder.slice(0, count - assigned)) {
    counts.set(label, counts.get(label)! + 1);
  }

  const result: Record<string, number> = {};
  for (const [label, value] of counts) {
    if (value > 0) result[label] = value;
  }
  return result;
}
