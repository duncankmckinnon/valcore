// Presentation for the version editor, split into one component per authoring concern so
// `VersionEditor` keeps the form state, validation, and save orchestration while these render
// the controls. Every value and change handler arrives as a prop; nothing here holds form
// state except the one collapsible section's open/closed flag, which is purely visual.

import { useState } from "react";
import type { ReactNode } from "react";
import type { CapabilitySpec, OutputField, ScoreKind } from "../api/types";
import { CapabilitiesEditor } from "./CapabilitiesEditor";
import { OutputFieldsEditor } from "./OutputFieldsEditor";
import { ChevronIcon } from "./icons";
import { Tooltip } from "./Tooltip";
import { Button, Select, TextArea } from "./ui";

// A field label paired with its optional info affordance. The tooltip trigger is a
// `type="button"`, so it never carries `aria-expanded` and stays distinct from the one
// collapsible section's disclosure. A `<button>` is itself a labelable element, so a field
// carrying a tooltip must wrap in a `<div>` rather than a `<label>` — otherwise the label
// text would bind to the tooltip button instead of the control it names.
function FieldLabel({ text, tooltip }: { text: string; tooltip?: string }): JSX.Element {
  return (
    <span className="field-label">
      {text}
      {tooltip && <Tooltip text={tooltip} label={`About ${text}`} />}
    </span>
  );
}

type IdentitySectionProps = {
  versionName: string;
  model: string;
  models: string[];
  frozen: boolean;
  onVersionName: (value: string) => void;
  onModel: (value: string) => void;
  versionNameError?: ReactNode;
  modelError?: ReactNode;
};

export function IdentitySection({
  versionName,
  model,
  models,
  frozen,
  onVersionName,
  onModel,
  versionNameError,
  modelError,
}: IdentitySectionProps): JSX.Element {
  return (
    <section className="editor-section">
      <div className="editor-section-head">Identity</div>
      <div className="editor-section-body">
        <label className="field">
          <FieldLabel text="Version name" />
          <input
            className="input"
            aria-label="Version name"
            value={versionName}
            readOnly={frozen}
            onChange={(event) => onVersionName(event.target.value)}
          />
          {versionNameError}
        </label>

        <div className="field">
          <FieldLabel text="Model" tooltip="The judge model that runs this evaluator's prompt." />
          <Select
            aria-label="Model"
            disabled={frozen}
            value={model}
            options={models.map((name) => ({ value: name, label: name }))}
            onChange={(event) => onModel(event.target.value)}
          />
          {modelError}
        </div>
      </div>
    </section>
  );
}

type JudgmentSectionProps = {
  instructions: string;
  promptTemplate: string;
  frozen: boolean;
  onInstructions: (value: string) => void;
  onPromptTemplate: (value: string) => void;
  instructionsError?: ReactNode;
  promptTemplateError?: ReactNode;
};

export function JudgmentSection({
  instructions,
  promptTemplate,
  frozen,
  onInstructions,
  onPromptTemplate,
  instructionsError,
  promptTemplateError,
}: JudgmentSectionProps): JSX.Element {
  return (
    <section className="editor-section">
      <div className="editor-section-head">Judgment</div>
      <div className="editor-section-body">
        <label className="field">
          <FieldLabel text="Instructions" />
          <TextArea
            aria-label="Instructions"
            className="instructions"
            rows={12}
            value={instructions}
            readOnly={frozen}
            onChange={(event) => onInstructions(event.target.value)}
          />
          {instructionsError}
        </label>

        <div className="field">
          <FieldLabel
            text="Prompt template"
            tooltip="Braced names like {answer} must match the required columns exactly."
          />
          <TextArea
            aria-label="Prompt template"
            value={promptTemplate}
            readOnly={frozen}
            onChange={(event) => onPromptTemplate(event.target.value)}
          />
          {promptTemplateError}
        </div>
      </div>
    </section>
  );
}

type InputsSectionProps = {
  columns: string[];
  columnDraft: string;
  frozen: boolean;
  onColumnDraft: (value: string) => void;
  onAddColumn: () => void;
  onRemoveColumn: (name: string) => void;
  columnsError?: ReactNode;
};

export function InputsSection({
  columns,
  columnDraft,
  frozen,
  onColumnDraft,
  onAddColumn,
  onRemoveColumn,
  columnsError,
}: InputsSectionProps): JSX.Element {
  return (
    <section className="editor-section">
      <div className="editor-section-head">Inputs</div>
      <div className="editor-section-body">
        <div className="field">
          <FieldLabel
            text="Required columns"
            tooltip="Input columns the prompt and judge can reference."
          />
          <div className="chips">
            {columns.map((column) => (
              <span key={column} className="chip">
                {column}
                {!frozen && (
                  <button
                    type="button"
                    className="chip-remove"
                    aria-label={`Remove column ${column}`}
                    onClick={() => onRemoveColumn(column)}
                  >
                    ×
                  </button>
                )}
              </span>
            ))}
          </div>
          {!frozen && (
            <div className="chip-add">
              <input
                className="input"
                aria-label="Add required column"
                value={columnDraft}
                onChange={(event) => onColumnDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    onAddColumn();
                  }
                }}
              />
              <Button variant="secondary" onClick={onAddColumn}>
                Add
              </Button>
            </div>
          )}
          {columnsError}
        </div>
      </div>
    </section>
  );
}

type OutputContractSectionProps = {
  outputFields: OutputField[];
  scoreKind: ScoreKind;
  scoreField: string;
  scoreFieldOptions: OutputField[];
  frozen: boolean;
  onOutputFields: (fields: OutputField[]) => void;
  onScoreKind: (kind: ScoreKind) => void;
  onScoreField: (value: string) => void;
  outputFieldsError?: ReactNode;
  scoreKindError?: ReactNode;
  scoreFieldError?: ReactNode;
  scoreLabelsError?: ReactNode;
};

export function OutputContractSection({
  outputFields,
  scoreKind,
  scoreField,
  scoreFieldOptions,
  frozen,
  onOutputFields,
  onScoreKind,
  onScoreField,
  outputFieldsError,
  scoreKindError,
  scoreFieldError,
  scoreLabelsError,
}: OutputContractSectionProps): JSX.Element {
  return (
    <section className="editor-section">
      <div className="editor-section-head">Output contract</div>
      <div className="editor-section-body">
        <div className="field">
          <FieldLabel
            text="Output fields"
            tooltip="The structured fields the judge must return for each row."
          />
          <OutputFieldsEditor fields={outputFields} readOnly={frozen} onChange={onOutputFields} />
          {outputFieldsError}
        </div>

        <div className="field">
          <FieldLabel
            text="Score kind"
            tooltip="Whether the evaluation's score is a category or a number."
          />
          <Select
            aria-label="Score kind"
            disabled={frozen}
            value={scoreKind}
            options={[
              { value: "categorical", label: "categorical" },
              { value: "numeric", label: "numeric" },
            ]}
            onChange={(event) => onScoreKind(event.target.value as ScoreKind)}
          />
          {scoreKindError}
        </div>

        <div className="field">
          <FieldLabel
            text="Score field"
            tooltip="Which output field becomes the evaluation's score."
          />
          <Select
            aria-label="Score field"
            disabled={frozen}
            value={scoreField}
            options={scoreFieldOptions.map((field) => ({ value: field.name, label: field.name }))}
            onChange={(event) => onScoreField(event.target.value)}
          />
          {scoreFieldError}
          {scoreLabelsError}
        </div>
      </div>
    </section>
  );
}

type CapabilitiesSectionProps = {
  availableCapabilities: string[];
  capabilities: CapabilitySpec[];
  availableTools: string[];
  tools: string[];
  frozen: boolean;
  onCapabilities: (capabilities: CapabilitySpec[]) => void;
  onToggleTool: (name: string, enabled: boolean) => void;
};

// The one collapsible section, closed on first render. The disclosure is a plain button
// carrying `aria-expanded` (not a `<details>`, so styling stays class-based); its body — and
// therefore every capability and tool control — is absent from the DOM while closed.
export function CapabilitiesSection({
  availableCapabilities,
  capabilities,
  availableTools,
  tools,
  frozen,
  onCapabilities,
  onToggleTool,
}: CapabilitiesSectionProps): JSX.Element {
  const [open, setOpen] = useState(false);

  return (
    <section className="editor-section">
      <div className="editor-section-head">
        <button
          type="button"
          className="editor-disclosure"
          aria-expanded={open}
          onClick={() => setOpen((prev) => !prev)}
        >
          <ChevronIcon />
          Capabilities &amp; tools
        </button>
      </div>
      {open && (
        <div className="editor-section-body">
          <div className="field">
            <FieldLabel
              text="Capabilities"
              tooltip="Extra abilities granted to the judge, like file or shell access."
            />
            <CapabilitiesEditor
              available={availableCapabilities}
              value={capabilities}
              readOnly={frozen}
              onChange={onCapabilities}
            />
          </div>

          <div className="field">
            <FieldLabel
              text="Tools"
              tooltip="Tools the judge may call while evaluating a row."
            />
            <div className="tools">
              {availableTools.map((name) => (
                <label key={name} className="tool-toggle">
                  <input
                    type="checkbox"
                    checked={tools.includes(name)}
                    disabled={frozen}
                    onChange={(event) => onToggleTool(name, event.target.checked)}
                  />
                  {name}
                </label>
              ))}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
