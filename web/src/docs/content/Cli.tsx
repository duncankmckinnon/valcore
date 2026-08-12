// The CLI tab. Deliberately not a mirror of the README's command table: the README stays
// canonical for the exhaustive listing (it is the PyPI long_description, where a reader
// cannot reach these pages), and this tab teaches the usage patterns that table does not.

import { CodeBlock, DocLink, DocNote, DocPage, DocSection } from "../primitives";

export function Cli(): JSX.Element {
  return (
    <DocPage>
      <DocSection title="How the CLI relates to this app">
        <p>
          The app and the CLI are two front ends over the same SQLite workspace. Authoring is
          easier in the app; running, exporting, and anything scripted is easier from the command
          line. Nothing has to be running for the CLI to work — it opens the database directly, so
          runs do not depend on <code>valcore serve</code>.
        </p>
        <CodeBlock>valcore serve</CodeBlock>
      </DocSection>

      <DocSection title="Naming things">
        <p>
          Evaluators, versions, and datasets are addressable by name or by a unique id prefix, so
          you rarely need a full id:
        </p>
        <CodeBlock>valcore run 7f3a my-dataset</CodeBlock>
        <p>
          An ambiguous value is an error that lists the candidates rather than picking one. Add
          characters until it resolves.
        </p>
      </DocSection>

      <DocSection title="Flags that shape a run">
        <ul>
          <li>
            <code>--version</code> — a version other than the active one
          </li>
          <li>
            <code>--kind validation</code> — compare against labels instead of only recording
            output
          </li>
          <li>
            <code>--concurrency</code> — how many rows are in flight at once
          </li>
          <li>
            <code>--watch</code> — one line per completed row, rather than a final summary
          </li>
          <li>
            <code>--json</code> — machine-readable results on stdout
          </li>
          <li>
            <code>--min-accuracy</code> — exit non-zero below a threshold, for CI
          </li>
        </ul>
      </DocSection>

      <DocSection title="Which group to reach for">
        <p>
          <code>valcore config</code> holds credentials and defaults — the gateway key, Logfire
          tokens, the default model, and the path to the config file. Keys are only ever set from
          the CLI; no secret crosses HTTP into the browser.
        </p>
        <CodeBlock>valcore config set-key</CodeBlock>
        <p>
          <code>valcore list</code> shows what the workspace holds, as a table or with{" "}
          <code>--json</code>. <code>valcore export</code> and <code>valcore import</code> move
          evaluators and datasets between machines. <code>valcore logfire push</code> sends a
          dataset to Logfire&apos;s hosted store. <code>valcore skills</code> installs the bundled
          agent skills so a coding agent can drive valcore for you.
        </p>
      </DocSection>

      <DocSection title="Pointing at another database">
        <p>
          Every command takes <code>--db</code> on the group, which is how you keep a scratch
          workspace or a per-project database separate from the default under{" "}
          <code>~/.valcore</code>:
        </p>
        <CodeBlock>valcore --db ./evals.sqlite list evaluators</CodeBlock>
        <DocNote>
          For the complete command table, plus install, CI recipes, portable packages, and
          Logfire setup, see the README in the repository. This tab covers the patterns, not
          every flag.
        </DocNote>
        <p>
          Back to <DocLink to="/docs/runs">Runs</DocLink> for what the run commands measure.
        </p>
      </DocSection>
    </DocPage>
  );
}
