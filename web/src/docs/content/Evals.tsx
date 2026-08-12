// The evaluators tab. Written for someone with the version editor open in another tab,
// so it explains the concepts the form assumes rather than restating its field labels.

import { CodeBlock, DocLink, DocNote, DocPage, DocSection } from "../primitives";

export function Evals(): JSX.Element {
  return (
    <DocPage>
      <DocSection title="What an evaluator is">
        <p>
          An evaluator is an LLM-as-judge: a prompt, a model, and an output shape that together
          score one thing about a row of data. It reads the columns you give it and returns a
          label — a category from a fixed set, or a number — plus its reasoning.
        </p>
        <p>
          An evaluator is not tied to a dataset. The same judge runs over any dataset that
          supplies the columns it requires, which is what makes a judge comparable across data.
        </p>
      </DocSection>

      <DocSection title="Output fields and labels">
        <p>
          The output field defines what a judge returns. Categorical fields declare their label
          space up front — the set of allowed values — and that space is what the labeling grid
          offers and what agreement metrics are computed over. Numeric fields declare a range
          instead, and are scored with error measures rather than a confusion matrix.
        </p>
        <p>
          Declaring the label space is what lets valcore tell a disagreement from an invalid
          answer. A judge that returns something outside the space is a failure, not a low score.
        </p>
      </DocSection>

      <DocSection title="Versions: active and frozen">
        <p>
          Every change to a judge lands as a version, and one version is active — the one used
          when you do not name another. Editing an unsaved draft edits in place. Editing a frozen
          version copies it first, so a version that has already produced runs is never mutated
          underneath them.
        </p>
        <p>
          That is the point of freezing: a run records which version produced it, so a score
          stays attributable. When two versions exist you can read the exact difference between
          them in the version diff rather than guessing from timestamps.
        </p>
        <DocNote>
          Validation runs every render and gates Save before the server sees the payload, so an
          incomplete version tells you what is missing instead of failing on submit.
        </DocNote>
      </DocSection>

      <DocSection title="Capabilities and tools">
        <p>
          A judge can be granted capabilities when reading the row is not enough. FileSystem
          gives it a rooted directory to read from; Shell gives it an allow-listed set of
          commands and a timeout. Both are opt-in per version and configured alongside the
          prompt.
        </p>
        <p>
          Grant the narrowest thing that answers the question. A capability widens what a judge
          can see, which also widens what can change between runs.
        </p>
      </DocSection>

      <DocSection title="Seeding a dataset from an evaluator">
        <p>
          An evaluator and a dataset have to agree on columns, so rather than retype that shape,
          generate one from the other. A dataset generated from a version always gets that
          version&apos;s required columns; you can name extra columns, and per-column notes say
          what each should contain. Suggested labels are optional — ask for them when you want
          the model to propose ground truth, and the label space comes from the evaluator.
        </p>
        <p>
          The reverse direction works too: generate an evaluator from a dataset and it is
          drafted against that dataset&apos;s columns. The result is an editable draft, not a
          saved version.
        </p>
        <p>
          Author judges in <DocLink to="/evaluators">Evaluators</DocLink>, then see{" "}
          <DocLink to="/docs/datasets">Datasets</DocLink> for what happens to the data side.
        </p>
      </DocSection>

      <DocSection title="Running one">
        <p>
          Runs are driven from the command line, against the active version unless you name
          another:
        </p>
        <CodeBlock>valcore run my-evaluator my-dataset</CodeBlock>
        <p>
          See <DocLink to="/docs/runs">Runs</DocLink> for run kinds and what gets measured.
        </p>
      </DocSection>
    </DocPage>
  );
}
