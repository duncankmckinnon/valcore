// Explicit, version-aware synchronization of an agent's two text templates.
import { useEffect, useState } from "react";
import { ApiError, agents, setup } from "../api/client";
import type {
  PromptSyncChoice,
  PromptSyncField,
  PromptSyncStatus,
  PromptSyncTemplateStatus,
} from "../api/types";
import { Button, ConfirmDialog, ErrorBanner, Modal, Spinner } from "./ui";

type Props = {
  agentId: string;
  open: boolean;
  onClose: () => void;
  onPulled: (activeVersionId: string | null) => void;
};

const fields: PromptSyncField[] = ["instructions", "input_template"];
const titles: Record<PromptSyncField, string> = {
  instructions: "Instructions",
  input_template: "Input template",
};
const stateLabels: Record<PromptSyncTemplateStatus["state"], string> = {
  in_sync: "In sync",
  local_changed: "Local changed",
  remote_changed: "Remote changed",
  conflict: "Conflict",
  remote_missing: "Remote missing",
  unsupported: "Unsupported",
};

type Confirmation =
  | { kind: "resolve"; fields: [PromptSyncField, ...PromptSyncField[]]; choice: PromptSyncChoice }
  | { kind: "unlink" };

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Shows the inspected three-way diff and performs only user-initiated sync actions. */
export default function AgentPromptSync({ agentId, open, onClose, onPulled }: Props) {
  const [status, setStatus] = useState<PromptSyncStatus | null>(null);
  const [keyPresent, setKeyPresent] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Record<PromptSyncField, boolean>>({
    instructions: true,
    input_template: true,
  });
  const [choices, setChoices] = useState<Partial<Record<PromptSyncField, PromptSyncChoice>>>({});
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [showLinkOptions, setShowLinkOptions] = useState(true);

  useEffect(() => {
    if (!open) return;
    let live = true;
    setStatus(null);
    setKeyPresent(null);
    setError(null);
    setSelected({ instructions: true, input_template: true });
    setChoices({});
    setConfirmation(null);
    setShowLinkOptions(true);
    void setup.get().then((result) => {
      if (live) setKeyPresent(result.keys.some((key) => key.name === "logfire_write_key" && key.set));
    }).catch((err: unknown) => {
      if (live) setError(messageOf(err));
    });
    void agents.promptSyncStatus(agentId).then((result) => {
      if (live) setStatus(result);
    }).catch((err: unknown) => {
      if (live) setError(messageOf(err));
    });
    return () => { live = false; };
  }, [agentId, open]);

  async function inspect(): Promise<PromptSyncStatus> {
    const fresh = await agents.promptSyncStatus(agentId);
    setStatus(fresh);
    return fresh;
  }

  function eligible(current: PromptSyncStatus, field: PromptSyncField, action: "pull" | "push") {
    return current.templates[field].state === (action === "pull" ? "remote_changed" : "local_changed");
  }

  async function mutate(action: "link-local" | "link-remote" | "pull" | "push") {
    if (!status || keyPresent !== true || status.error || busy) return;
    setBusy(true);
    setError(null);
    try {
      const fresh = await inspect();
      if (fresh.error) return;
      if (action === "link-local" || action === "link-remote") {
        if (fresh.linked || !fresh.local_version_id ||
            fields.some((field) => fresh.templates[field].state === "unsupported") ||
            (action === "link-remote" && fields.some((field) => fresh.templates[field].remote_version === null))) return;
        const result = await agents.promptSyncLink(agentId, {
          initial: action === "link-local" ? "local" : "remote",
          expected: fresh.revision,
        });
        setStatus(result);
        if (action === "link-remote") onPulled(result.local_version_id);
      } else {
        const chosen = fields.filter((field) => selected[field] && eligible(status, field, action));
        if (!fresh.linked || chosen.length === 0 || chosen.some((field) => !eligible(fresh, field, action))) return;
        if (action === "pull") {
          const result = await agents.promptSyncPull(agentId, { fields: chosen, expected: fresh.revision });
          setStatus(result);
          onPulled(result.active_version_id);
        } else {
          setStatus(await agents.promptSyncPush(agentId, { fields: chosen, expected: fresh.revision }));
        }
      }
    } catch (err) {
      setError(messageOf(err));
      if (err instanceof ApiError && err.status === 409) {
        try { await inspect(); } catch (inspectError) { setError(messageOf(inspectError)); }
      }
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!confirmation || !status || busy) return;
    const pending = confirmation;
    setConfirmation(null);
    setBusy(true);
    setError(null);
    try {
      // A changed key has a local-only revision that is valid only for Unlink.
      const fresh = pending.kind === "unlink" && status.error
        ? status
        : await inspect();
      if (pending.kind === "unlink") {
        if (!fresh.linked) return;
        setStatus(await agents.promptSyncUnlink(agentId, { expected: fresh.revision }));
        setShowLinkOptions(false);
      } else {
        if (fresh.revision !== status.revision) {
          setChoices({});
          setError("Sync state changed. Review the refreshed diff and confirm again.");
          return;
        }
        if (fresh.error || !fresh.linked || pending.fields.some((field) => {
          const state = fresh.templates[field].state;
          return state !== "conflict" && !(state === "remote_missing" && pending.choice === "local");
        })) return;
        const result = await agents.promptSyncResolve(agentId, {
          fields: pending.fields,
          choice: pending.choice,
          expected: fresh.revision,
        });
        setStatus(result);
        setChoices({});
        if (pending.choice === "remote") onPulled(result.local_version_id);
      }
    } catch (err) {
      setError(messageOf(err));
      if (err instanceof ApiError && err.status === 409) {
        try { await inspect(); } catch (inspectError) { setError(messageOf(inspectError)); }
      }
    } finally {
      setBusy(false);
    }
  }

  if (!open) return null;
  const canWrite = keyPresent === true && !busy && !status?.error;
  const pullable = status?.linked && fields.some((field) => eligible(status, field, "pull"));
  const pushable = status?.linked && fields.some((field) => eligible(status, field, "push"));
  const pullFields = status ? fields.filter((field) => selected[field] && eligible(status, field, "pull")) : [];
  const pushFields = status ? fields.filter((field) => selected[field] && eligible(status, field, "push")) : [];
  const resolved = status ? fields.filter((field) => status.templates[field].state === "conflict" && choices[field]) : [];
  const resolveChoice = resolved.length ? choices[resolved[0]] : undefined;
  const canResolve = resolved.length > 0 && resolved.every((field) => choices[field] === resolveChoice);

  return (
    <>
      <Modal open title="Logfire sync" size="lg" onClose={onClose} footer={<Button variant="secondary" onClick={onClose}>Close</Button>}>
        <div className="prompt-sync">
          {keyPresent === false && <p>Configure the Logfire sync key in Settings to enable writes.</p>}
          <ErrorBanner error={error || status?.error} />
          {!status && !error && <Spinner />}
          {status && <>
            <p>{status.linked ? "Linked" : "Not linked"} · Local version {status.local_version_id ?? "none"}</p>
            <div className="prompt-sync-fields">
              {fields.map((field) => {
                const item = status.templates[field];
                const selectable = status.linked && (item.state === "local_changed" || item.state === "remote_changed");
                return <fieldset className="prompt-sync-field" key={field}>
                  <legend>{titles[field]}</legend>
                  <p className="prompt-sync-variable">{item.variable_name}</p>
                  <p><strong>{stateLabels[item.state]}</strong> · Remote version {item.remote_version ?? "none"} · Baseline remote version {item.base_remote_version ?? "none"}</p>
                  {item.error && <p role="alert">{item.error}</p>}
                  {selectable && <label><input type="checkbox" checked={selected[field]} onChange={(event) => setSelected((old) => ({ ...old, [field]: event.target.checked }))} /> Select {titles[field].toLowerCase()}</label>}
                  <div className="prompt-sync-diff">
                    <div><span>Local</span><pre>{item.local_text ?? "(absent)"}</pre></div>
                    <div><span>Baseline</span><pre>{item.base_text ?? "(absent)"}</pre></div>
                    <div><span>Remote</span><pre>{item.remote_text ?? "(absent)"}</pre></div>
                  </div>
                  {status.linked && !status.error && item.state === "conflict" && <div className="prompt-sync-choices">
                    <label><input type="radio" name={`resolve-${field}`} checked={choices[field] === "local"} onChange={() => setChoices((old) => ({ ...old, [field]: "local" }))} /> Keep local text for {field === "instructions" ? "instructions" : "input template"}</label>
                    <label><input type="radio" name={`resolve-${field}`} checked={choices[field] === "remote"} onChange={() => setChoices((old) => ({ ...old, [field]: "remote" }))} /> Use remote text for {field === "instructions" ? "instructions" : "input template"}</label>
                  </div>}
                </fieldset>;
              })}
            </div>
            {!status.linked && !showLinkOptions && <Button onClick={() => setShowLinkOptions(true)}>Link</Button>}
            {!status.linked && showLinkOptions && <div className="prompt-sync-actions">
              <div><Button disabled={!canWrite || !status.local_version_id || fields.some((field) => status.templates[field].state === "unsupported")} onClick={() => void mutate("link-local")}>Link with local text</Button><p>Creates missing Logfire variables and publishes local text to new variable versions.</p></div>
              <div><Button variant="secondary" disabled={!canWrite || !status.local_version_id || fields.some((field) => status.templates[field].remote_version === null || status.templates[field].state === "unsupported")} onClick={() => void mutate("link-remote")}>Link with remote text</Button><p>Creates a new local agent version from the remote text.</p></div>
            </div>}
            {status.linked && <div className="prompt-sync-actions">
              {!status.error && <>
                {pullable && <div><Button disabled={!canWrite || pullFields.length === 0} onClick={() => void mutate("pull")}>Pull</Button><p>Pull selected remote text into a new local agent version.</p></div>}
                {pushable && <div><Button disabled={!canWrite || pushFields.length === 0} onClick={() => void mutate("push")}>Push</Button><p>Push selected local text as a new Logfire variable version. The production label and other serving labels stay unchanged.</p></div>}
                {fields.some((field) => status.templates[field].state === "conflict") && <Button disabled={!canWrite || !canResolve} onClick={() => setConfirmation({ kind: "resolve", fields: resolved as [PromptSyncField, ...PromptSyncField[]], choice: resolveChoice! })}>Resolve</Button>}
                {fields.filter((field) => status.templates[field].state === "remote_missing").map((field) => <Button key={field} disabled={!canWrite} onClick={() => setConfirmation({ kind: "resolve", fields: [field], choice: "local" })}>Recreate on Logfire</Button>)}
              </>}
              <Button variant="secondary" disabled={busy} onClick={() => setConfirmation({ kind: "unlink" })}>Unlink</Button>
            </div>}
          </>}
        </div>
      </Modal>
      <ConfirmDialog
        open={confirmation?.kind === "resolve"}
        title="Confirm resolve"
        message={confirmation?.kind === "resolve" && status
          ? `Use ${confirmation.choice} text for ${confirmation.fields.join(", ")}? ${confirmation.fields.map((field) => `Local: ${status.templates[field].local_text ?? "(absent)"}; Baseline: ${status.templates[field].base_text ?? "(absent)"}; Remote: ${status.templates[field].remote_text ?? "(absent)"}`).join(" ")} This will ${confirmation.choice === "local" ? "create a new Logfire variable version" : "create a new local agent version"}.`
          : ""}
        confirmLabel="Confirm"
        busy={busy}
        onClose={() => setConfirmation(null)}
        onConfirm={() => void confirm()}
      />
      <ConfirmDialog
        open={confirmation?.kind === "unlink"}
        title="Unlink Logfire sync?"
        message="Remove only this local sync link? Logfire variables are left unchanged. You can link again with the current key."
        confirmLabel="Unlink"
        busy={busy}
        onClose={() => setConfirmation(null)}
        onConfirm={() => void confirm()}
      />
    </>
  );
}
