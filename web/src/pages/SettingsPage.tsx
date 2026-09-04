// Settings: the one place keys are entered in the UI. GET never returns values — only
// whether each credential is set — so a set field shows a mask, an unset field is empty,
// and typing always uses a password input. Save writes to the local config via POST.

import { useEffect, useMemo, useState } from "react";
import { setup } from "../api/client";
import type { SetupKey, SetupKeyName, SetupKeysIn, SetupStatus } from "../api/types";
import { PageHeader } from "../components/PageHeader";
import { Button, ErrorBanner, Spinner } from "../components/ui";

const MASK = "••••••••";

type Drafts = Partial<Record<SetupKeyName, string>>;

function keyByName(status: SetupStatus, name: SetupKeyName): SetupKey | undefined {
  return status.keys.find((key) => key.name === name);
}

function displayedValue(item: SetupKey, drafts: Drafts, dirty: Set<SetupKeyName>): string {
  if (dirty.has(item.name)) return drafts[item.name] ?? "";
  return item.set ? MASK : "";
}

export default function SettingsPage(): JSX.Element {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [drafts, setDrafts] = useState<Drafts>({});
  const [dirty, setDirty] = useState<Set<SetupKeyName>>(new Set());
  const [cleared, setCleared] = useState<Set<SetupKeyName>>(new Set());
  const [sameReadWrite, setSameReadWrite] = useState(false);

  function load() {
    setLoading(true);
    setup
      .get()
      .then((result) => {
        setStatus(result);
        setError(null);
        setDrafts({});
        setDirty(new Set());
        setCleared(new Set());
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
  }, []);

  const canSave = dirty.size > 0 || cleared.size > 0;

  const payload = useMemo((): SetupKeysIn | null => {
    if (!canSave) return null;
    const body: SetupKeysIn = {};
    const clear: SetupKeyName[] = [];
    for (const name of dirty) {
      const value = (drafts[name] ?? "").trim();
      if (value === "" || value === MASK) {
        if (cleared.has(name) || status?.keys.find((key) => key.name === name)?.set) {
          clear.push(name);
        }
        continue;
      }
      body[name] = value;
    }
    for (const name of cleared) {
      if (body[name] === undefined && !clear.includes(name)) {
        clear.push(name);
      }
    }
    if (sameReadWrite && body.logfire_read_key) {
      body.logfire_write_key = body.logfire_read_key;
    }
    if (clear.length > 0) body.clear = clear;
    return body;
  }, [canSave, cleared, dirty, drafts, sameReadWrite, status]);

  function setDraft(name: SetupKeyName, raw: string, item: SetupKey) {
    let next = raw;
    if (!dirty.has(name) && item.set && raw.startsWith(MASK)) {
      next = raw.slice(MASK.length);
    }
    setDrafts((current) => ({ ...current, [name]: next }));
    setDirty((current) => new Set(current).add(name));
    setCleared((current) => {
      const nextCleared = new Set(current);
      nextCleared.delete(name);
      return nextCleared;
    });
  }

  function clearKey(name: SetupKeyName) {
    setDrafts((current) => ({ ...current, [name]: "" }));
    setDirty((current) => new Set(current).add(name));
    setCleared((current) => new Set(current).add(name));
  }

  async function save() {
    if (payload === null) return;
    setSaving(true);
    setError(null);
    try {
      const result = await setup.save(payload);
      setStatus(result);
      setDrafts({});
      setDirty(new Set());
      setCleared(new Set());
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  }

  if (error && status === null && !loading) {
    return (
      <section>
        <PageHeader title="Settings" />
        <ErrorBanner error={error} />
      </section>
    );
  }

  if (status === null) {
    return (
      <section>
        <PageHeader title="Settings" />
        <Spinner />
      </section>
    );
  }

  const writeItem = keyByName(status, "logfire_write_key");
  const visibleKeys = sameReadWrite
    ? status.keys.filter((key) => key.name !== "logfire_write_key")
    : status.keys;

  return (
    <section>
      <PageHeader
        title="Settings"
        description="Store API keys in the local config. Values are never sent back to the browser — a set key shows as masked until you replace it."
      />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <ul className="settings-keys">
        {visibleKeys.map((item) => (
          <li key={item.name} className="settings-key">
            <div className="settings-key-heading">
              <label className="settings-key-label" htmlFor={`key-${item.name}`}>
                {item.label}
              </label>
              <span className={item.required ? "setup-key-required" : "setup-key-optional"}>
                {item.required ? "Required" : "Optional"}
              </span>
              <span>{item.set ? "Set" : "Not set"}</span>
            </div>
            <p className="settings-explanation">{item.explanation}</p>
            {item.from_env && (
              <p className="settings-from-env">
                Currently set from the environment. Saving still writes the file, but the
                environment variable wins until it is unset.
              </p>
            )}
            <div className="settings-key-row">
              <input
                id={`key-${item.name}`}
                className="input"
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={displayedValue(item, drafts, dirty)}
                placeholder={item.set ? undefined : "Not set"}
                onChange={(event) => setDraft(item.name, event.target.value, item)}
              />
              {item.set && (
                <Button variant="secondary" type="button" onClick={() => clearKey(item.name)}>
                  Clear
                </Button>
              )}
            </div>
          </li>
        ))}
      </ul>
      {writeItem && (
        <label className="settings-same">
          <input
            type="checkbox"
            checked={sameReadWrite}
            onChange={(event) => setSameReadWrite(event.target.checked)}
          />
          Use the same key for read and write — paste once only when you sample traces
          from the valcore project itself.
        </label>
      )}
      <div className="form-footer">
        <div className="form-footer-actions">
          <Button onClick={() => void save()} disabled={!canSave || saving || payload === null}>
            {saving ? "Saving…" : "Save keys"}
          </Button>
        </div>
      </div>
    </section>
  );
}
