// Label-distribution editor shared by both generation flows. Prescribing a mix is opt-in:
// with it off the field is omitted from the payload and the distribution follows whatever
// the description/instructions ask for, which is the server's default. The parent owns the
// percents record and the on/off flag; this component only reports edits back.
//
// The editor works in whole percents because that is how people describe a mix, and shows
// the apportioned row count beside each label so the number the model is actually told is
// never hidden behind a percentage.

import { Button } from "./ui";
import { TOTAL_PERCENT, apportion, evenSplit, totalPercent } from "./labelMix";
import type { LabelMixPercents } from "./labelMix";

type LabelMixEditorProps = {
  labels: string[];
  percents: LabelMixPercents;
  enabled: boolean;
  // Drives the row-count preview; the same count that will be sent as `count`.
  count: number;
  onChangeEnabled: (enabled: boolean) => void;
  onChangePercents: (percents: LabelMixPercents) => void;
};

export function LabelMixEditor({
  labels,
  percents,
  enabled,
  count,
  onChangeEnabled,
  onChangePercents,
}: LabelMixEditorProps) {
  // Nothing to distribute over. A numeric score space never reaches here (the API rejects
  // a mix for one), and an empty label list means the user has not defined one yet.
  if (labels.length === 0) return null;

  const total = totalPercent(percents);
  // Only preview counts for a total that could actually be submitted. `apportion` would
  // happily normalise 20/50 into a full ten rows, which reads as a valid plan sitting
  // next to a blocking error.
  const rows = total === TOTAL_PERCENT ? apportion(percents, count) : null;

  const toggle = (next: boolean) => {
    onChangeEnabled(next);
    // Seed an even split on first enable so the control opens in a valid, submittable
    // state rather than at 0% with a blocking error already showing.
    if (next && total === 0) onChangePercents(evenSplit(labels));
  };

  const setPercent = (label: string, raw: string) => {
    const parsed = raw === "" ? 0 : Number(raw);
    if (Number.isNaN(parsed)) return;
    onChangePercents({ ...percents, [label]: Math.max(0, Math.round(parsed)) });
  };

  return (
    <div className="label-mix-editor">
      <label className="field-inline">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(event) => toggle(event.target.checked)}
        />
        Prescribe label distribution
      </label>

      {!enabled && (
        <p className="field-hint">
          Off: the description and instructions decide the mix of labels.
        </p>
      )}

      {enabled && (
        <>
          {labels.map((label) => (
            <div key={label} className="label-mix-row">
              <span className="label-mix-name">{label}</span>
              <input
                className="input"
                type="number"
                min={0}
                max={TOTAL_PERCENT}
                aria-label={`Percent for ${label}`}
                value={percents[label] ?? 0}
                onChange={(event) => setPercent(label, event.target.value)}
              />
              <span className="label-mix-unit">%</span>
              {rows && <span className="label-mix-count">{rows[label] ?? 0} rows</span>}
            </div>
          ))}

          <div className="label-mix-footer">
            <span className={total === TOTAL_PERCENT ? "label-mix-total" : "destructive-warning"}>
              Total {total}%
            </span>
            <Button
              type="button"
              variant="secondary"
              onClick={() => onChangePercents(evenSplit(labels))}
            >
              Even split
            </Button>
          </div>

          {total !== TOTAL_PERCENT && (
            <p className="destructive-warning">Percentages must add up to {TOTAL_PERCENT}%.</p>
          )}
        </>
      )}
    </div>
  );
}

export default LabelMixEditor;
