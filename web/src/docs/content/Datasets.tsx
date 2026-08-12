// The datasets tab: where rows come from, how labels get onto them, and why labels are
// optional until you want to measure agreement.

import { CodeBlock, DocLink, DocNote, DocPage, DocSection } from "../primitives";

export function Datasets(): JSX.Element {
  return (
    <DocPage>
      <DocSection title="Three ways to get rows">
        <p>
          A dataset is a table of rows with named columns, and it arrives one of three ways.
          Upload a CSV when you already have data. Create a blank dataset and author rows by
          hand when you are working from a handful of known cases. Generate one from a
          description when you need coverage you do not have yet.
        </p>
        <p>
          Generation is a starting point, not an answer: a generated dataset is an editable draft
          like any other, and its rows are yours to correct.
        </p>
      </DocSection>

      <DocSection title="Generation settings and label mix">
        <p>
          A generated dataset keeps the request that produced it — the description, the columns
          asked for, and the per-column notes — as read-only provenance. Months later that is how
          you know what these rows were meant to represent. Uploaded and blank datasets have no
          such record, which is normal rather than missing.
        </p>
        <p>
          Prescribing a label mix is opt-in. Leave it off and the distribution follows whatever
          the description asks for. Turn it on to pin the proportions — useful when you need a
          rare category represented well enough to measure. The editor works in whole percents
          and shows the apportioned row count beside each label, so the number the model is
          actually told is never hidden behind a percentage.
        </p>
      </DocSection>

      <DocSection title="Labeling by hand">
        <p>
          Labels are ground truth: what the right answer is, independent of what any judge says.
          The labeling grid is built for getting through rows quickly with the keyboard.
        </p>
        <ul>
          <li>
            <code>j</code> / <code>k</code> — move between rows
          </li>
          <li>
            <code>1</code>–<code>9</code> — apply a categorical label
          </li>
          <li>
            <code>a</code> — accept the suggested label
          </li>
          <li>
            <code>u</code> — clear the label
          </li>
          <li>
            <code>?</code> — show the full shortcut list
          </li>
        </ul>
        <p>
          Every change saves immediately, applied optimistically and rolled back if the write
          fails. Cells are editable in place, rows can be added or deleted, and per-column notes
          record what a column is supposed to hold.
        </p>
      </DocSection>

      <DocSection title="When labels are required">
        <p>
          A dataset needs no labels to be scored. An ordinary run just records what the judge
          said. Labels are only required for a validation run, which compares the judge against
          them to measure agreement.
        </p>
        <DocNote>
          Validation is unavailable — and says so — while a dataset still has unlabeled rows. A
          partially labeled dataset would produce an agreement number over a silently shrinking
          subset.
        </DocNote>
      </DocSection>

      <DocSection title="Growing and exporting">
        <p>
          A dataset that turned out too small can be extended in place: generating more rows
          reuses the original request so the additions match the shape and intent of what is
          already there.
        </p>
        <p>
          Export writes a dataset out as a Python script or as a portable JSON package, and the
          CLI reads it back:
        </p>
        <CodeBlock>valcore export my-evaluator --dataset my-dataset --format json</CodeBlock>
        <p>
          Work with data in <DocLink to="/datasets">Datasets</DocLink>, or see{" "}
          <DocLink to="/docs/evals">Evals</DocLink> for the judge side of the column contract.
        </p>
      </DocSection>
    </DocPage>
  );
}
