# Agent prompt sync with Logfire

valcore can explicitly sync an agent's two text templates with ordinary Logfire
**managed variables**, so a prompt can be edited in Logfire and pulled back, or authored in
valcore and pushed. Sync is an editing convenience only. It is **not** live runtime prompt
loading: agent trials and dataset runs always use the local agent version and make no
Logfire prompt-sync calls. Existing agent versions remain executable without Logfire;
optional Logfire tracing and hosted model calls have their own network behavior.

## What syncs, and what does not

Only two text fields sync:

| Field | valcore source |
|---|---|
| `instructions` | the single string in `AgentVersion.spec["instructions"]` |
| `input_template` | `AgentVersion.prompt_template` |

Model, capabilities, tools, input and dependency mappings, and the output contract are never
synced. A pull copies the current active version and changes only the selected text.

> **Not Prompt Management.** These are ordinary managed variables. They do **not** appear as
> first-class Prompt Management objects in Logfire, and valcore never creates or modifies
> `prompt__` variables (that prefix is reserved for Prompt Management). Logfire does not
> currently document a write API for Prompt Management objects.

## Setup

1. Create one Logfire API key for the project you want to sync with, with **both** scopes:
   - `project:read_variables`
   - `project:write_variables`
2. Store it in valcore's write-key slot:

   ```bash
   valcore config set-logfire-write-key
   ```

   `valcore config set-logfire-key` also works but stores the same key as the dataset read
   key too.

The project is selected by the key; there is no project ID to enter. Use a **single** key
holding both scopes. Reading and writing must address the same project, so do not pair a
write key with the separate dataset read key, which may point at another project. A trace
token or a dataset-only key is not sufficient. The key stays server-side and is never
returned to the browser or logged.

## Variable names

Each agent uses two string variables, named from its immutable agent ID:

- `valcore_agent_<agent_id>_instructions`
- `valcore_agent_<agent_id>_input_template` (an empty string when there is no input mapping)

## Workflow

Every step is explicit; nothing syncs in the background. Use the agent page's sync dialog or
`valcore agent prompt-sync` (see the [command reference](../src/valcore/skills/use-valcore/reference.md)).

- **Inspect** (`status`): read-only. Shows each field as `in_sync`, `local_changed`,
  `remote_changed`, `conflict`, `remote_missing`, or `unsupported`.
- **Link**: pick an initial source. `local` creates missing remote variables and publishes
  new latest versions for existing variables whose text differs from local text. `remote`
  creates a new local version and requires both remote variables to exist. If both sides
  already match, link just records a baseline. Retrying a partly failed link reconciles
  matching text instead of writing a duplicate version.
- **Pull**: creates a new active valcore agent version with the remote text. Frozen and
  historical versions are never modified.
- **Push**: creates a new Logfire variable version for each changed field.
- **Resolve**: settles a conflict, or recreates a remotely deleted variable (`local` only).
- **Unlink**: removes only the local link; remote variables are left alone.

The agent dialog shows the text before each action. Pull and Push run when clicked; Resolve
opens a confirmation dialog with the three-way diff. The CLI previews Pull, Push and Resolve
and prompts for confirmation by default; `--yes` skips that prompt.

### Latest-version behavior and labels

valcore compares against each variable's **latest saved version**, not a movable label such
as `production`. Edits made in Logfire become eligible to pull once saved as a new version.
Push writes a new version under a dedicated `valcore_sync` label and preserves every other
label, rollout and variable. It never promotes `production` or any other serving label, and
`valcore_sync` is only a publishing mechanism, not what valcore reads back.

### Conflicts

valcore stores the last-synced text of each field as a baseline. If both sides changed a field
since the baseline, it is a **conflict**: local, baseline and remote text are shown and neither
side is overwritten. Ordinary pull and push skip conflicts but still process other eligible
fields. Resolve them by choosing `local` (push your text) or `remote` (pull Logfire's) after
reviewing the diff. If both sides end up identical, the baseline is just updated with no new
version.

### Key rotation

The link records a nonreversible fingerprint of the configured key. If the key changes, sync
is refused before any read or write of the new project. Unlink, then link again.

## Supported template subset

Logfire variables use `{{column}}` placeholders, while valcore's input template uses
`{column}`. Placeholder names must be simple identifiers such as `column_name`; dotted
paths (`user.name`), hyphenated names (`user-name`) and reserved Logfire words (such as
`if` or `each`) are rejected. valcore converts supported placeholders at the boundary and
checks that the conversion round-trips exactly before linking, pulling or pushing. It
rejects, with an actionable error, rather than flattening or partially rendering:

- Python format specs and conversions (for example `{x:>10}` or `{x!r}`) and literal brace
  escapes that cannot be represented on both sides;
- Logfire blocks and template composition;
- Logfire template expressions in `instructions`, which are literal text in valcore;
- missing, `null` or list-valued instructions (save a single string first; the empty string
  is valid).

## Concurrency caveat

valcore re-reads the remote variables and active local version before every write, and
rejects a stale review. Logfire's variable config API does not promise an atomic
compare-and-swap, so a simultaneous remote writer is not guaranteed to be detected.
