// Capability checkboxes plus the per-capability config inputs (FileSystem `root_dir`,
// Shell `allowed_commands` + `default_timeout`). Extracted from VersionEditor so the editor
// stays readable; behavior and every aria-label are unchanged.

import { useEffect, useState } from "react";
import type { CapabilitySpec } from "../api/types";

type CapabilitiesEditorProps = {
  available: string[];
  value: CapabilitySpec[];
  readOnly: boolean;
  onChange: (capabilities: CapabilitySpec[]) => void;
};

function parseCommands(text: string): string[] {
  return text
    .split(",")
    .map((command) => command.trim())
    .filter((command) => command.length > 0);
}

export function CapabilitiesEditor({
  available,
  value,
  readOnly,
  onChange,
}: CapabilitiesEditorProps) {
  const configOf = (name: string): Record<string, unknown> | null => {
    const found = value.find((capability) => capability.name === name);
    return found ? found.config : null;
  };

  // The Shell commands input keeps the user's raw text so separators typed between commands
  // survive; the parsed list is what gets emitted. Reconstructing the value from the parsed
  // list would swallow a trailing comma or space mid-word ("ls, cat" -> "lscat").
  const shellCommands = ((configOf("Shell")?.allowed_commands as string[]) ?? []);
  const [commandsText, setCommandsText] = useState(() => shellCommands.join(", "));

  useEffect(() => {
    if (parseCommands(commandsText).join(",") !== shellCommands.join(",")) {
      setCommandsText(shellCommands.join(", "));
    }
    // Resync only when the external value diverges from the current text (a version swap or
    // an applied refine), never on the round trip of our own edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shellCommands.join(",")]);

  const toggle = (name: string, enabled: boolean) => {
    onChange(
      enabled
        ? [...value, { name, config: {} }]
        : value.filter((capability) => capability.name !== name),
    );
  };

  const updateConfig = (name: string, changes: Record<string, unknown>) => {
    onChange(
      value.map((capability) =>
        capability.name === name
          ? { ...capability, config: { ...capability.config, ...changes } }
          : capability,
      ),
    );
  };

  return (
    <>
      {available.map((name) => {
        const capConfig = configOf(name);
        const enabled = capConfig !== null;
        return (
          <div key={name} className="capability">
            <label className="capability-toggle">
              <input
                type="checkbox"
                checked={enabled}
                disabled={readOnly}
                onChange={(event) => toggle(name, event.target.checked)}
              />
              {name}
            </label>
            {enabled && name === "FileSystem" && (
              <input
                className="input"
                aria-label="FileSystem root dir"
                placeholder="root dir"
                value={String(capConfig?.root_dir ?? "")}
                readOnly={readOnly}
                onChange={(event) => updateConfig(name, { root_dir: event.target.value })}
              />
            )}
            {enabled && name === "Shell" && (
              <div className="capability-config">
                <input
                  className="input"
                  aria-label="Shell allowed commands"
                  placeholder="allowed commands (comma separated)"
                  value={commandsText}
                  readOnly={readOnly}
                  onChange={(event) => {
                    setCommandsText(event.target.value);
                    updateConfig(name, { allowed_commands: parseCommands(event.target.value) });
                  }}
                />
                <input
                  className="input"
                  type="number"
                  aria-label="Shell timeout"
                  placeholder="timeout (s)"
                  value={
                    capConfig && capConfig.default_timeout !== undefined
                      ? String(capConfig.default_timeout)
                      : ""
                  }
                  readOnly={readOnly}
                  onChange={(event) =>
                    updateConfig(name, {
                      default_timeout:
                        event.target.value === "" ? undefined : Number(event.target.value),
                    })
                  }
                />
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}

export default CapabilitiesEditor;
