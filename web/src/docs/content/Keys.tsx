// The keys tab: the three credentials the Overview setup card lists, where each one comes
// from, and what stops working without it. Deliberately the first tab — nothing that calls
// a model runs until the gateway key exists.
//
// Key names, commands, and required/optional status here must match
// src/valcore/api/routes/setup.py, which is what the Overview card renders from.

import { CodeBlock, DocLink, DocNote, DocPage, DocSection, ExternalLink } from "../primitives";

export function Keys(): JSX.Element {
  return (
    <DocPage>
      <DocSection title="The three keys">
        <p>
          The setup card on <DocLink to="/">Overview</DocLink> lists every credential valcore
          knows about and whether it is currently set. One is required; two are optional and
          only matter if you use Logfire.
        </p>
        <ul>
          <li>
            <strong>Pydantic AI Gateway key</strong> — required. Runs evaluators, and generates
            evaluators and datasets.
          </li>
          <li>
            <strong>Logfire write token</strong> — optional. Sends run traces to Logfire.
          </li>
          <li>
            <strong>Logfire API key</strong> — optional. Pushes datasets to Logfire&apos;s hosted
            store.
          </li>
        </ul>
        <DocNote>
          Keys are never entered through the web UI — no secret crosses HTTP — so every one of
          them is set from the command line. The card reports presence only; it never shows a
          key back to you.
        </DocNote>
      </DocSection>

      <DocSection title="Gateway key: what it unlocks">
        <p>
          valcore reaches models through the Pydantic AI Gateway, and that is currently the only
          route — there is no direct-to-provider client and no per-provider key. Without it,
          generation and runs are unavailable and the UI says why.
        </p>
        <p>
          Everything that does not call a model still works with no key at all: authoring by
          hand, uploading a CSV, editing rows, hand-labeling, and every export.
        </p>
        <CodeBlock>valcore config set-key</CodeBlock>
        <p>
          Run it with no argument to be prompted without the key echoing to your terminal or
          landing in shell history.
        </p>
      </DocSection>

      <DocSection title="Getting a gateway key">
        <p>
          Create the key in the Pydantic AI Gateway and paste it into the command above. The
          gateway documentation covers account setup and where keys are issued:
        </p>
        <p>
          <ExternalLink href="https://ai.pydantic.dev/gateway/">
            ai.pydantic.dev/gateway
          </ExternalLink>
        </p>
        <p>
          Model strings are always <code>gateway/&lt;provider&gt;:&lt;model&gt;</code> — for
          example <code>gateway/anthropic:claude-sonnet-5</code>, which is the default. Valid
          providers are <code>anthropic</code>, <code>openai</code>, <code>google</code>,{" "}
          <code>google-cloud</code>, <code>bedrock</code>, and <code>groq</code>. A string that
          does not match this shape is rejected before any request is made, so a bare model name
          fails immediately with a clear error rather than at call time.
        </p>
      </DocSection>

      <DocSection title="Logfire write token: traces">
        <p>
          With a write token configured, each run opens a <code>valcore.run</code> span carrying
          the evaluator version, dataset, and concurrency, with one child span per scored row.
          On close, the run span records its status and each agreement metric as attributes, so
          a Logfire query can filter runs by accuracy directly.
        </p>
        <CodeBlock>valcore config set-logfire-token</CodeBlock>
        <p>
          A write token belongs to one Logfire project and is created from that project&apos;s
          settings. Create the project first, then issue the token there:
        </p>
        <p>
          <ExternalLink href="https://logfire.pydantic.dev/docs/how-to-guides/create-write-tokens/">
            Creating write tokens
          </ExternalLink>
        </p>
        <DocNote>
          Tracing is an optional extra as well as an optional key — install it with{" "}
          <code>uv tool install &apos;valcore[logfire]&apos;</code>. The gateway already reports
          the LLM calls themselves; valcore adds only the surrounding run and row context, and
          deliberately does not re-report the calls, which would double-count tokens and cost.
        </DocNote>
      </DocSection>

      <DocSection title="Logfire API key: hosted datasets">
        <p>
          The API key is a separate credential from the write token, and it is only needed for
          one thing: pushing a dataset to Logfire&apos;s hosted dataset store.
        </p>
        <CodeBlock>valcore config set-logfire-key</CodeBlock>
        <p>
          It must carry the <code>project:read_datasets</code> and{" "}
          <code>project:write_datasets</code> scopes. A key without them will authenticate and
          then fail on the push. API keys are issued from your Logfire account settings:
        </p>
        <p>
          <ExternalLink href="https://logfire.pydantic.dev/docs/reference/api/">
            Logfire API reference
          </ExternalLink>
        </p>
        <CodeBlock>valcore logfire push my-dataset</CodeBlock>
      </DocSection>

      <DocSection title="Where keys are stored">
        <p>
          Keys live in <code>~/.valcore/config.toml</code>, written with mode <code>0600</code>,
          and are exported into the environment when a command runs. An already-exported
          environment variable always wins over the stored value, which is what you want in CI:
        </p>
        <CodeBlock>export PYDANTIC_AI_GATEWAY_API_KEY=sk-...</CodeBlock>
        <p>To check what is currently configured without revealing anything:</p>
        <CodeBlock>valcore config get</CodeBlock>
        <p>
          The stored key is masked unless you pass <code>--show-key</code>. See{" "}
          <DocLink to="/docs/cli">CLI</DocLink> for the rest of the config group, or{" "}
          <DocLink to="/docs/evals">Evals</DocLink> to start authoring now that setup is done.
        </p>
      </DocSection>
    </DocPage>
  );
}
