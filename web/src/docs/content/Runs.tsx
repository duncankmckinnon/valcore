// The runs tab: kinds, what each metric means, comparison, and the two commands that
// produce runs. Named Runs rather than Experiments because the UI says Runs and
// `valcore experiment` is one specific command rather than the whole idea.

import { CodeBlock, DocLink, DocNote, DocPage, DocSection } from "../primitives";

export function Runs(): JSX.Element {
  return (
    <DocPage>
      <DocSection title="What a run is">
        <p>
          A run is one evaluator version scored over one dataset, recorded with its results. The
          version is part of the record, so a score stays attributable after the judge moves on.
        </p>
        <p>Runs are started from the command line or from the launcher in the app:</p>
        <CodeBlock>valcore run my-evaluator my-dataset</CodeBlock>
      </DocSection>

      <DocSection title="Two kinds">
        <p>
          An ordinary run records what the judge returned for each row. That is enough to inspect
          behavior, spot invalid outputs, and compare two judges against each other.
        </p>
        <p>
          A validation run additionally compares the judge against the dataset&apos;s labels and
          reports agreement. It requires a fully labeled dataset, so the option is unavailable —
          with the reason shown — when rows are still unlabeled.
        </p>
        <CodeBlock>valcore run my-evaluator my-dataset --kind validation</CodeBlock>
      </DocSection>

      <DocSection title="Reading the metrics">
        <p>
          What you get depends on the output field. A categorical judge produces a confusion
          matrix: rows are the label, columns are what the judge said, and the diagonal is
          agreement. Reading off the diagonal tells you which categories a judge confuses, which
          a single accuracy number hides.
        </p>
        <p>
          A numeric judge produces error measures instead — mean absolute error and root mean
          square error. MAE is the typical miss; RMSE punishes large misses harder, so a gap
          between them means a few rows are badly wrong rather than everything being slightly
          off.
        </p>
      </DocSection>

      <DocSection title="Comparing two runs">
        <p>
          Comparison puts two runs over the same dataset side by side, disagreements first,
          because the rows where they differ are the only ones that explain a change. Comparing
          runs over different datasets is refused rather than rendered — the numbers would not be
          about the same thing.
        </p>
        <p>
          Open <DocLink to="/runs/compare">Compare</DocLink> to pick two, or{" "}
          <DocLink to="/runs">Runs</DocLink> to inspect one on its own.
        </p>
      </DocSection>

      <DocSection title="run and experiment">
        <p>
          <code>valcore run</code> uses valcore&apos;s own runner. <code>valcore experiment</code>{" "}
          scores the same pairing through <code>pydantic_evals.Dataset.evaluate</code> instead,
          which is the path to take when you want results inside the pydantic-evals ecosystem.
        </p>
        <CodeBlock>valcore experiment my-evaluator my-dataset</CodeBlock>
      </DocSection>

      <DocSection title="Gating on a threshold">
        <p>
          A validation run can fail on its own accuracy, which is what makes it usable as a
          check rather than a report:
        </p>
        <CodeBlock>
          valcore run my-evaluator my-dataset --kind validation --min-accuracy 0.9
        </CodeBlock>
        <DocNote>
          Progress goes to stderr and results to stdout, so redirecting stdout yields clean JSON.
          The CLI talks to SQLite directly — runs work whether or not <code>serve</code> is up.
        </DocNote>
        <p>
          See <DocLink to="/docs/cli">CLI</DocLink> for the flags that shape a run.
        </p>
      </DocSection>
    </DocPage>
  );
}
