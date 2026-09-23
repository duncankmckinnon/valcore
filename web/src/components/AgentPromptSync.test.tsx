// Tests for AgentPromptSync: the explicit Logfire sync dialog for one agent. It owns all
// prompt-sync traffic (Inspect, Link, Pull, Push, Resolve, Unlink), so this suite drives the
// dialog through the typed client. AgentDetail only wires the open/close state and the
// post-pull refresh; those integration checks live in AgentDetail.test.tsx.
//
// Contract under test: <AgentPromptSync agentId open onClose onPulled />. Each template
// renders as a group named "Instructions" / "Input template" with a "Select <name>"
// checkbox. Pull/Push act on the checked fields of the matching direction, every eligible
// field starting checked. Resolve and Unlink always go through a confirmation dialog.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AgentPromptSync from "./AgentPromptSync";
import { ApiError, agents, setup } from "../api/client";
import type {
  PromptSyncField,
  PromptSyncPullResult,
  PromptSyncStatus,
  PromptSyncTemplateStatus,
  SetupStatus,
} from "../api/types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    agents: {
      ...actual.agents,
      promptSyncStatus: vi.fn(),
      promptSyncLink: vi.fn(),
      promptSyncPull: vi.fn(),
      promptSyncPush: vi.fn(),
      promptSyncResolve: vi.fn(),
      promptSyncUnlink: vi.fn(),
    },
    setup: { ...actual.setup, get: vi.fn() },
  };
});

const statusMock = vi.mocked(agents.promptSyncStatus);
const linkMock = vi.mocked(agents.promptSyncLink);
const pullMock = vi.mocked(agents.promptSyncPull);
const pushMock = vi.mocked(agents.promptSyncPush);
const resolveMock = vi.mocked(agents.promptSyncResolve);
const unlinkMock = vi.mocked(agents.promptSyncUnlink);

const NAMES: Record<PromptSyncField, string> = {
  instructions: "valcore_agent_agent-1_instructions",
  input_template: "valcore_agent_agent-1_input_template",
};

function inSync(field: PromptSyncField, text: string, version = 3): PromptSyncTemplateStatus {
  return {
    variable_name: NAMES[field],
    state: "in_sync",
    local_text: text,
    base_text: text,
    remote_text: text,
    remote_version: version,
    base_remote_version: version,
    error: null,
  };
}

function template(
  field: PromptSyncField,
  overrides: Record<string, unknown>,
): PromptSyncTemplateStatus {
  return { ...inSync(field, "same"), ...overrides } as PromptSyncTemplateStatus;
}

function makeStatus(overrides: Partial<PromptSyncStatus> = {}): PromptSyncStatus {
  return {
    linked: true,
    local_version_id: "av-1",
    revision: "rev-1",
    error: null,
    templates: {
      instructions: inSync("instructions", "Help the user."),
      input_template: inSync("input_template", "Question: {question}"),
    },
    ...overrides,
  };
}

function localChanged(field: PromptSyncField): PromptSyncTemplateStatus {
  return template(field, {
    state: "local_changed",
    local_text: "LOCAL edit",
    base_text: "BASE text",
    remote_text: "BASE text",
    remote_version: 3,
    base_remote_version: 3,
  });
}

function remoteChanged(field: PromptSyncField): PromptSyncTemplateStatus {
  return template(field, {
    state: "remote_changed",
    local_text: "BASE text",
    base_text: "BASE text",
    remote_text: "REMOTE edit",
    remote_version: 4,
    base_remote_version: 3,
  });
}

function conflict(field: PromptSyncField): PromptSyncTemplateStatus {
  return template(field, {
    state: "conflict",
    local_text: "LOCAL edit",
    base_text: "BASE text",
    remote_text: "REMOTE edit",
    remote_version: 4,
    base_remote_version: 3,
  });
}

function missing(field: PromptSyncField): PromptSyncTemplateStatus {
  return template(field, {
    state: "remote_missing",
    local_text: "LOCAL only",
    base_text: "BASE text",
    remote_text: null,
    remote_version: null,
    base_remote_version: 3,
  });
}

function unsupported(field: PromptSyncField, error: string): PromptSyncTemplateStatus {
  return template(field, {
    state: "unsupported",
    local_text: null,
    base_text: null,
    remote_text: null,
    remote_version: null,
    base_remote_version: null,
    error,
  });
}

function unlinkedPreview(): PromptSyncStatus {
  return makeStatus({
    linked: false,
    templates: {
      instructions: template("instructions", {
        state: "remote_changed",
        local_text: "Local instructions",
        base_text: null,
        remote_text: "Remote instructions",
        remote_version: 2,
        base_remote_version: null,
      }),
      input_template: template("input_template", {
        state: "remote_changed",
        local_text: "Local {question}",
        base_text: null,
        remote_text: "Remote {question}",
        remote_version: 5,
        base_remote_version: null,
      }),
    },
  });
}

function setKeys(writeKeySet: boolean) {
  vi.mocked(setup.get).mockResolvedValue({
    keys: [
      { name: "gateway_api_key", set: true },
      { name: "logfire_write_key", set: writeKeySet },
    ],
    local_cli_default: null,
    local_cli_options: [],
  } as unknown as SetupStatus);
}

function group(name: string) {
  return screen.getByRole("group", { name });
}

async function renderOpen(status: PromptSyncStatus, props: { onPulled?: () => void } = {}) {
  statusMock.mockResolvedValue(status);
  const onClose = vi.fn();
  const onPulled = props.onPulled ?? vi.fn();
  const utils = render(
    <AgentPromptSync agentId="agent-1" open onClose={onClose} onPulled={onPulled} />,
  );
  await screen.findByRole("group", { name: "Instructions" });
  return { ...utils, onClose, onPulled };
}

beforeEach(() => {
  setKeys(true);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AgentPromptSync", () => {
  describe("inspection", () => {
    it("renders nothing and makes no request while closed", () => {
      render(
        <AgentPromptSync agentId="agent-1" open={false} onClose={vi.fn()} onPulled={vi.fn()} />,
      );
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(statusMock).not.toHaveBeenCalled();
    });

    it("inspects on open and shows variable names plus local and remote versions", async () => {
      await renderOpen(makeStatus());

      expect(statusMock).toHaveBeenCalledWith("agent-1");
      const dialog = screen.getByRole("dialog", { name: "Logfire sync" });
      expect(dialog).toHaveTextContent("av-1");
      const instructions = group("Instructions");
      expect(instructions).toHaveTextContent(NAMES.instructions);
      expect(instructions).toHaveTextContent("Remote version 3");
      expect(group("Input template")).toHaveTextContent(NAMES.input_template);
    });

    it("labels each template with its status", async () => {
      await renderOpen(
        makeStatus({
          templates: {
            instructions: localChanged("instructions"),
            input_template: remoteChanged("input_template"),
          },
        }),
      );
      expect((group("Instructions")).textContent).toMatch(/local changed/i);
      expect((group("Input template")).textContent).toMatch(/remote changed/i);
    });

    it("reports in-sync templates without offering Pull or Push", async () => {
      await renderOpen(makeStatus());
      expect((group("Instructions")).textContent).toMatch(/in sync/i);
      expect(screen.queryByRole("button", { name: "Pull" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Push" })).not.toBeInTheDocument();
    });

    it("shows the local and remote text for a one-sided change", async () => {
      await renderOpen(
        makeStatus({
          templates: {
            instructions: localChanged("instructions"),
            input_template: inSync("input_template", "Q"),
          },
        }),
      );
      const instructions = group("Instructions");
      expect(instructions).toHaveTextContent("LOCAL edit");
      expect(instructions).toHaveTextContent("BASE text");
    });

    it("shows the server's error for an unsupported template and never offers writes for it", async () => {
      await renderOpen(
        makeStatus({
          templates: {
            instructions: unsupported("instructions", "Instructions must be a single string."),
            input_template: inSync("input_template", "Q"),
          },
        }),
      );
      const instructions = group("Instructions");
      expect((instructions).textContent).toMatch(/unsupported/i);
      expect(instructions).toHaveTextContent("Instructions must be a single string.");
      expect(within(instructions).queryByRole("checkbox")).not.toBeInTheDocument();
    });

    it("shows a failed inspect as an alert instead of a status", async () => {
      statusMock.mockRejectedValue(
        new ApiError("logfire_write_key lacks project:read_variables", "SyncScopeError", 403),
      );
      render(<AgentPromptSync agentId="agent-1" open onClose={vi.fn()} onPulled={vi.fn()} />);

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "logfire_write_key lacks project:read_variables",
      );
      expect(screen.queryByRole("button", { name: "Push" })).not.toBeInTheDocument();
    });

    it("shows the server-side status error verbatim (under-scoped key)", async () => {
      await renderOpen(
        makeStatus({ error: "The Logfire key is missing project:write_variables." }),
      );
      expect(screen.getByRole("alert")).toHaveTextContent(
        "The Logfire key is missing project:write_variables.",
      );
    });
  });

  describe("missing key", () => {
    it("disables every write and points at Settings when the sync key is absent", async () => {
      setKeys(false);
      await renderOpen(unlinkedPreview());

      expect(screen.getByText(/Settings/)).toBeInTheDocument();
      for (const name of [/^Link/, "Pull", "Push"]) {
        for (const button of screen.queryAllByRole("button", { name })) {
          expect(button).toBeDisabled();
        }
      }
      expect(linkMock).not.toHaveBeenCalled();
    });

    it("shows the configuration error returned without remote state", async () => {
      setKeys(false);
      await renderOpen(
        makeStatus({
          linked: false,
          error: "No Logfire sync key is configured.",
          templates: {
            instructions: unsupported("instructions", ""),
            input_template: unsupported("input_template", ""),
          },
        }),
      );
      expect(screen.getByRole("alert")).toHaveTextContent("No Logfire sync key is configured.");
    });
  });

  describe("linking", () => {
    it("previews the remote variables of an unlinked agent before linking", async () => {
      await renderOpen(unlinkedPreview());

      expect(group("Instructions")).toHaveTextContent("Remote instructions");
      expect(group("Instructions")).toHaveTextContent("Local instructions");
      expect(group("Input template")).toHaveTextContent("Remote version 5");
      expect(screen.queryByRole("button", { name: "Unlink" })).not.toBeInTheDocument();
    });

    it("links using local text with the inspected revision", async () => {
      linkMock.mockResolvedValue(makeStatus({ revision: "rev-2" }));
      const user = userEvent.setup();
      await renderOpen(unlinkedPreview());

      await user.click(screen.getByRole("button", { name: "Link with local text" }));

      await waitFor(() =>
        expect(linkMock).toHaveBeenCalledWith("agent-1", {
          initial: "local",
          expected: "rev-1",
        }),
      );
      expect(await screen.findByRole("button", { name: "Unlink" })).toBeInTheDocument();
    });

    it("links using remote text", async () => {
      linkMock.mockResolvedValue(makeStatus({ revision: "rev-2" }));
      const user = userEvent.setup();
      await renderOpen(unlinkedPreview());

      await user.click(screen.getByRole("button", { name: "Link with remote text" }));

      await waitFor(() =>
        expect(linkMock).toHaveBeenCalledWith("agent-1", {
          initial: "remote",
          expected: "rev-1",
        }),
      );
    });

    it("spells out what each link direction does", async () => {
      await renderOpen(unlinkedPreview());
      const dialog = screen.getByRole("dialog", { name: "Logfire sync" });
      expect((dialog).textContent).toMatch(/creates? (the )?(missing )?(Logfire )?variables/i);
      expect((dialog).textContent).toMatch(/new (local )?(agent )?version/i);
    });

    it("cannot link from remote while a remote variable is missing", async () => {
      await renderOpen(
        makeStatus({
          linked: false,
          templates: {
            instructions: template("instructions", {
              state: "remote_missing",
              local_text: "Local",
              base_text: null,
              remote_text: null,
              remote_version: null,
              base_remote_version: null,
            }),
            input_template: unlinkedPreview().templates.input_template,
          },
        }),
      );
      expect(screen.getByRole("button", { name: "Link with remote text" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Link with local text" })).not.toBeDisabled();
    });

    it("links a matching pair by recording the baseline", async () => {
      linkMock.mockResolvedValue(makeStatus());
      const user = userEvent.setup();
      await renderOpen(
        makeStatus({
          linked: false,
          templates: {
            instructions: inSync("instructions", "Same"),
            input_template: inSync("input_template", "Same"),
          },
        }),
      );

      await user.click(screen.getByRole("button", { name: "Link with local text" }));
      await waitFor(() => expect(linkMock).toHaveBeenCalledTimes(1));
    });
  });

  describe("pull and push", () => {
    it("pulls every remote-changed field with the inspected revision", async () => {
      const result: PromptSyncPullResult = {
        ...makeStatus({ revision: "rev-2", local_version_id: "av-2" }),
        active_version_id: "av-2",
      };
      pullMock.mockResolvedValue(result);
      const onPulled = vi.fn();
      const user = userEvent.setup();
      await renderOpen(
        makeStatus({
          templates: {
            instructions: remoteChanged("instructions"),
            input_template: remoteChanged("input_template"),
          },
        }),
        { onPulled },
      );

      await user.click(screen.getByRole("button", { name: "Pull" }));

      await waitFor(() =>
        expect(pullMock).toHaveBeenCalledWith("agent-1", {
          fields: ["instructions", "input_template"],
          expected: "rev-1",
        }),
      );
      expect(onPulled).toHaveBeenCalledWith("av-2");
    });

    it("does not report a pull that failed", async () => {
      pullMock.mockRejectedValue(new ApiError("Remote text uses Logfire blocks.", "SyncUnsupportedError", 422));
      const onPulled = vi.fn();
      const user = userEvent.setup();
      await renderOpen(
        makeStatus({
          templates: {
            instructions: remoteChanged("instructions"),
            input_template: inSync("input_template", "Q"),
          },
        }),
        { onPulled },
      );

      await user.click(screen.getByRole("button", { name: "Pull" }));

      expect(await screen.findByText("Remote text uses Logfire blocks.")).toBeInTheDocument();
      expect(onPulled).not.toHaveBeenCalled();
    });

    it("pushes every local-changed field", async () => {
      pushMock.mockResolvedValue(makeStatus({ revision: "rev-2" }));
      const user = userEvent.setup();
      await renderOpen(
        makeStatus({
          templates: {
            instructions: localChanged("instructions"),
            input_template: localChanged("input_template"),
          },
        }),
      );

      await user.click(screen.getByRole("button", { name: "Push" }));

      await waitFor(() =>
        expect(pushMock).toHaveBeenCalledWith("agent-1", {
          fields: ["instructions", "input_template"],
          expected: "rev-1",
        }),
      );
    });

    it("spells out that Push creates a new version and leaves serving labels alone", async () => {
      await renderOpen(
        makeStatus({
          templates: {
            instructions: localChanged("instructions"),
            input_template: inSync("input_template", "Q"),
          },
        }),
      );
      const dialog = screen.getByRole("dialog", { name: "Logfire sync" });
      expect((dialog).textContent).toMatch(/new (Logfire )?(variable )?version/i);
      expect((dialog).textContent).toMatch(/production/i);
    });

    it("spells out that Pull creates a new local version", async () => {
      await renderOpen(
        makeStatus({
          templates: {
            instructions: remoteChanged("instructions"),
            input_template: inSync("input_template", "Q"),
          },
        }),
      );
      expect(screen.getByRole("dialog", { name: "Logfire sync" }).textContent).toMatch(
        /new (local )?(agent )?version/i,
      );
    });

    it("pushes only the local field and pulls only the remote field in a mixed state", async () => {
      pushMock.mockResolvedValue(makeStatus({ revision: "rev-2" }));
      pullMock.mockResolvedValue({ ...makeStatus({ revision: "rev-3" }), active_version_id: "av-2" });
      const user = userEvent.setup();
      await renderOpen(
        makeStatus({
          templates: {
            instructions: localChanged("instructions"),
            input_template: remoteChanged("input_template"),
          },
        }),
      );

      await user.click(screen.getByRole("button", { name: "Push" }));
      await waitFor(() =>
        expect(pushMock).toHaveBeenCalledWith("agent-1", {
          fields: ["instructions"],
          expected: "rev-1",
        }),
      );
    });

    it("pulls only the remote field in a mixed state", async () => {
      pullMock.mockResolvedValue({ ...makeStatus({ revision: "rev-3" }), active_version_id: "av-2" });
      const user = userEvent.setup();
      await renderOpen(
        makeStatus({
          templates: {
            instructions: localChanged("instructions"),
            input_template: remoteChanged("input_template"),
          },
        }),
      );

      await user.click(screen.getByRole("button", { name: "Pull" }));
      await waitFor(() =>
        expect(pullMock).toHaveBeenCalledWith("agent-1", {
          fields: ["input_template"],
          expected: "rev-1",
        }),
      );
    });

    it("limits an action to the fields the user leaves selected", async () => {
      pushMock.mockResolvedValue(makeStatus({ revision: "rev-2" }));
      const user = userEvent.setup();
      await renderOpen(
        makeStatus({
          templates: {
            instructions: localChanged("instructions"),
            input_template: localChanged("input_template"),
          },
        }),
      );

      await user.click(screen.getByRole("checkbox", { name: "Select input template" }));
      await user.click(screen.getByRole("button", { name: "Push" }));

      await waitFor(() =>
        expect(pushMock).toHaveBeenCalledWith("agent-1", {
          fields: ["instructions"],
          expected: "rev-1",
        }),
      );
    });

    it("disables Push when every eligible field is deselected", async () => {
      const user = userEvent.setup();
      await renderOpen(
        makeStatus({
          templates: {
            instructions: localChanged("instructions"),
            input_template: inSync("input_template", "Q"),
          },
        }),
      );

      await user.click(screen.getByRole("checkbox", { name: "Select instructions" }));
      expect(screen.getByRole("button", { name: "Push" })).toBeDisabled();
    });

    it("excludes conflicted fields from ordinary Pull and Push", async () => {
      await renderOpen(
        makeStatus({
          templates: {
            instructions: conflict("instructions"),
            input_template: inSync("input_template", "Q"),
          },
        }),
      );
      expect(screen.queryByRole("button", { name: "Pull" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Push" })).not.toBeInTheDocument();
    });
  });

  describe("refresh and stale revisions", () => {
    it("refreshes status immediately before applying and uses the fresh revision", async () => {
      pushMock.mockResolvedValue(makeStatus({ revision: "rev-3" }));
      const user = userEvent.setup();
      await renderOpen(
        makeStatus({
          templates: {
            instructions: localChanged("instructions"),
            input_template: inSync("input_template", "Q"),
          },
        }),
      );
      statusMock.mockResolvedValue(
        makeStatus({
          revision: "rev-2",
          templates: {
            instructions: localChanged("instructions"),
            input_template: inSync("input_template", "Q"),
          },
        }),
      );

      await user.click(screen.getByRole("button", { name: "Push" }));

      await waitFor(() =>
        expect(pushMock).toHaveBeenCalledWith("agent-1", {
          fields: ["instructions"],
          expected: "rev-2",
        }),
      );
      expect(statusMock.mock.invocationCallOrder[1]).toBeLessThan(
        pushMock.mock.invocationCallOrder[0],
      );
    });

    it("does not write when the pre-apply refresh shows the field is no longer eligible", async () => {
      const user = userEvent.setup();
      await renderOpen(
        makeStatus({
          templates: {
            instructions: localChanged("instructions"),
            input_template: inSync("input_template", "Q"),
          },
        }),
      );
      statusMock.mockResolvedValue(
        makeStatus({
          revision: "rev-2",
          templates: {
            instructions: conflict("instructions"),
            input_template: inSync("input_template", "Q"),
          },
        }),
      );

      await user.click(screen.getByRole("button", { name: "Push" }));

      await waitFor(() => expect(group("Instructions").textContent).toMatch(/conflict/i));
      expect(pushMock).not.toHaveBeenCalled();
    });

    it("refreshes the diff on a 409 instead of retrying the write", async () => {
      pushMock.mockRejectedValue(new ApiError("Sync state changed.", "SyncConflictError", 409));
      const user = userEvent.setup();
      const pushable = makeStatus({
        templates: {
          instructions: localChanged("instructions"),
          input_template: inSync("input_template", "Q"),
        },
      });
      await renderOpen(pushable);
      // Pre-apply refresh still looks pushable; the post-409 refresh shows a conflict.
      statusMock.mockResolvedValueOnce(pushable).mockResolvedValue(
        makeStatus({
          revision: "rev-9",
          templates: {
            instructions: conflict("instructions"),
            input_template: inSync("input_template", "Q"),
          },
        }),
      );

      await user.click(screen.getByRole("button", { name: "Push" }));

      await waitFor(() => expect(group("Instructions").textContent).toMatch(/conflict/i));
      expect((screen.getByRole("alert")).textContent).toMatch(/changed/i);
      expect(pushMock).toHaveBeenCalledTimes(1);
    });

    it("refreshes after a 409 from Resolve and requires a new confirmation", async () => {
      resolveMock.mockRejectedValue(new ApiError("Sync state changed.", "SyncConflictError", 409));
      const user = userEvent.setup();
      await renderOpen(
        makeStatus({
          templates: {
            instructions: conflict("instructions"),
            input_template: inSync("input_template", "Q"),
          },
        }),
      );

      await user.click(screen.getByRole("radio", { name: "Keep local text for instructions" }));
      await user.click(screen.getByRole("button", { name: "Resolve" }));
      await user.click(
        within(screen.getByRole("dialog", { name: "Confirm resolve" })).getByRole("button", {
          name: "Confirm",
        }),
      );

      await waitFor(() => expect(resolveMock).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(statusMock.mock.calls.length).toBeGreaterThanOrEqual(3));
      expect(resolveMock).toHaveBeenCalledTimes(1);
    });
  });

  describe("conflicts", () => {
    async function openConflict() {
      const user = userEvent.setup();
      await renderOpen(
        makeStatus({
          templates: {
            instructions: conflict("instructions"),
            input_template: inSync("input_template", "Q"),
          },
        }),
      );
      return user;
    }

    it("shows local, baseline, and remote text for a conflict", async () => {
      await openConflict();
      const instructions = group("Instructions");
      expect((instructions).textContent).toMatch(/conflict/i);
      expect(instructions).toHaveTextContent("LOCAL edit");
      expect(instructions).toHaveTextContent("BASE text");
      expect(instructions).toHaveTextContent("REMOTE edit");
    });

    it("keeps Resolve disabled until a side is chosen", async () => {
      await openConflict();
      expect(screen.getByRole("button", { name: "Resolve" })).toBeDisabled();
    });

    it("resolves with local text only after confirmation", async () => {
      resolveMock.mockResolvedValue(makeStatus({ revision: "rev-2" }));
      const user = await openConflict();

      await user.click(screen.getByRole("radio", { name: "Keep local text for instructions" }));
      await user.click(screen.getByRole("button", { name: "Resolve" }));

      const confirm = screen.getByRole("dialog", { name: "Confirm resolve" });
      expect(confirm).toHaveTextContent("LOCAL edit");
      expect(confirm).toHaveTextContent("REMOTE edit");
      expect(resolveMock).not.toHaveBeenCalled();

      await user.click(within(confirm).getByRole("button", { name: "Confirm" }));
      await waitFor(() =>
        expect(resolveMock).toHaveBeenCalledWith("agent-1", {
          fields: ["instructions"],
          choice: "local",
          expected: "rev-1",
        }),
      );
    });

    it("resolves with remote text and reports the pulled version", async () => {
      resolveMock.mockResolvedValue(makeStatus({ revision: "rev-2" }));
      const onPulled = vi.fn();
      const user = userEvent.setup();
      await renderOpen(
        makeStatus({
          templates: {
            instructions: conflict("instructions"),
            input_template: inSync("input_template", "Q"),
          },
        }),
        { onPulled },
      );

      await user.click(screen.getByRole("radio", { name: "Use remote text for instructions" }));
      await user.click(screen.getByRole("button", { name: "Resolve" }));
      await user.click(
        within(screen.getByRole("dialog", { name: "Confirm resolve" })).getByRole("button", {
          name: "Confirm",
        }),
      );

      await waitFor(() =>
        expect(resolveMock).toHaveBeenCalledWith("agent-1", {
          fields: ["instructions"],
          choice: "remote",
          expected: "rev-1",
        }),
      );
      // A remote resolution creates a local version, so the page must refresh.
      await waitFor(() => expect(onPulled).toHaveBeenCalled());
    });

    it("makes no request when the resolve confirmation is cancelled", async () => {
      const user = await openConflict();

      await user.click(screen.getByRole("radio", { name: "Keep local text for instructions" }));
      await user.click(screen.getByRole("button", { name: "Resolve" }));
      await user.click(
        within(screen.getByRole("dialog", { name: "Confirm resolve" })).getByRole("button", {
          name: "Cancel",
        }),
      );

      expect(screen.queryByRole("dialog", { name: "Confirm resolve" })).not.toBeInTheDocument();
      expect(resolveMock).not.toHaveBeenCalled();
      expect(group("Instructions")).toHaveTextContent("REMOTE edit");
    });

    it("never calls Resolve from a plain Pull or Push", async () => {
      await openConflict();
      expect(pullMock).not.toHaveBeenCalled();
      expect(pushMock).not.toHaveBeenCalled();
      expect(resolveMock).not.toHaveBeenCalled();
    });
  });

  describe("remote missing", () => {
    it("shows a missing remote variable as a separate recovery state", async () => {
      await renderOpen(
        makeStatus({
          templates: {
            instructions: missing("instructions"),
            input_template: inSync("input_template", "Q"),
          },
        }),
      );
      const instructions = group("Instructions");
      expect((instructions).textContent).toMatch(/missing/i);
      expect((instructions).textContent).not.toMatch(/conflict/i);
      expect(instructions).toHaveTextContent("LOCAL only");
      expect(screen.queryByRole("button", { name: "Pull" })).not.toBeInTheDocument();
      expect(
        within(instructions).queryByRole("radio", { name: /Use remote text/ }),
      ).not.toBeInTheDocument();
    });

    it("recreates the variable from local text only after confirmation", async () => {
      resolveMock.mockResolvedValue(makeStatus({ revision: "rev-2" }));
      const user = userEvent.setup();
      await renderOpen(
        makeStatus({
          templates: {
            instructions: missing("instructions"),
            input_template: inSync("input_template", "Q"),
          },
        }),
      );

      await user.click(screen.getByRole("button", { name: "Recreate on Logfire" }));
      const confirm = screen.getByRole("dialog", { name: "Confirm resolve" });
      expect(confirm).toHaveTextContent("LOCAL only");
      expect(resolveMock).not.toHaveBeenCalled();

      await user.click(within(confirm).getByRole("button", { name: "Confirm" }));
      await waitFor(() =>
        expect(resolveMock).toHaveBeenCalledWith("agent-1", {
          fields: ["instructions"],
          choice: "local",
          expected: "rev-1",
        }),
      );
    });

    it("does not recreate when confirmation is cancelled", async () => {
      const user = userEvent.setup();
      await renderOpen(
        makeStatus({
          templates: {
            instructions: missing("instructions"),
            input_template: inSync("input_template", "Q"),
          },
        }),
      );

      await user.click(screen.getByRole("button", { name: "Recreate on Logfire" }));
      await user.click(
        within(screen.getByRole("dialog", { name: "Confirm resolve" })).getByRole("button", {
          name: "Cancel",
        }),
      );
      expect(resolveMock).not.toHaveBeenCalled();
    });
  });

  describe("unlink and key rotation", () => {
    it("unlinks only after confirmation and explains only the local link is removed", async () => {
      unlinkMock.mockResolvedValue(makeStatus({ linked: false, revision: "rev-2" }));
      const user = userEvent.setup();
      await renderOpen(makeStatus());

      await user.click(screen.getByRole("button", { name: "Unlink" }));
      const confirm = screen.getByRole("dialog", { name: "Unlink Logfire sync?" });
      expect((confirm).textContent).toMatch(/Logfire variables? (are|is) (left )?(unchanged|untouched)|does not (delete|change)/i);
      expect(unlinkMock).not.toHaveBeenCalled();

      await user.click(within(confirm).getByRole("button", { name: "Unlink" }));
      await waitFor(() =>
        expect(unlinkMock).toHaveBeenCalledWith("agent-1", { expected: "rev-1" }),
      );
      expect(await screen.findByRole("button", { name: /^Link/ })).toBeInTheDocument();
    });

    it("makes no request when the unlink confirmation is cancelled", async () => {
      const user = userEvent.setup();
      await renderOpen(makeStatus());

      await user.click(screen.getByRole("button", { name: "Unlink" }));
      await user.click(
        within(screen.getByRole("dialog", { name: "Unlink Logfire sync?" })).getByRole("button", {
          name: "Cancel",
        }),
      );
      expect(unlinkMock).not.toHaveBeenCalled();
    });

    it("offers only Unlink when the key changed, with the server's rebind error", async () => {
      await renderOpen(
        makeStatus({
          error: "The Logfire key changed since this agent was linked. Unlink and link again.",
        }),
      );

      expect((screen.getByRole("alert")).textContent).toMatch(/Unlink and link again/);
      expect(screen.getByRole("button", { name: "Unlink" })).not.toBeDisabled();
      expect(screen.queryByRole("button", { name: "Pull" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Push" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Resolve" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Recreate on Logfire" })).not.toBeInTheDocument();
    });

    it("unlinks with the local-only revision during a rebind", async () => {
      unlinkMock.mockResolvedValue(makeStatus({ linked: false, revision: "rev-2" }));
      const user = userEvent.setup();
      await renderOpen(
        makeStatus({ revision: "local-only", error: "The Logfire key changed. Unlink first." }),
      );

      await user.click(screen.getByRole("button", { name: "Unlink" }));
      await user.click(
        within(screen.getByRole("dialog", { name: "Unlink Logfire sync?" })).getByRole("button", {
          name: "Unlink",
        }),
      );
      await waitFor(() =>
        expect(unlinkMock).toHaveBeenCalledWith("agent-1", { expected: "local-only" }),
      );
    });
  });

  describe("dialog lifecycle", () => {
    it("closes from the Close button", async () => {
      const user = userEvent.setup();
      const { onClose } = await renderOpen(makeStatus());
      await user.click(screen.getByRole("button", { name: "Close" }));
      expect(onClose).toHaveBeenCalled();
    });

    it("never writes on open, on refresh, or on close", async () => {
      const user = userEvent.setup();
      await renderOpen(
        makeStatus({
          templates: {
            instructions: localChanged("instructions"),
            input_template: remoteChanged("input_template"),
          },
        }),
      );
      await user.click(screen.getByRole("button", { name: "Close" }));

      for (const fn of [linkMock, pullMock, pushMock, resolveMock, unlinkMock]) {
        expect(fn).not.toHaveBeenCalled();
      }
    });
  });
});
