// Ordered editor for an evaluator's output schema. Each field has a name, type,
// description, required flag, and — depending on type — enum values or numeric bounds.
// Fields can be added, removed, and reordered.

import type { FieldType, OutputField } from "../api/types";
import { Button, Select } from "./ui";

const FIELD_TYPES: { value: FieldType; label: string }[] = [
  { value: "str", label: "str" },
  { value: "int", label: "int" },
  { value: "float", label: "float" },
  { value: "bool", label: "bool" },
  { value: "enum", label: "enum" },
];

type OutputFieldsEditorProps = {
  fields: OutputField[];
  readOnly?: boolean;
  onChange: (fields: OutputField[]) => void;
};

function emptyField(): OutputField {
  return {
    name: "",
    type: "str",
    description: "",
    required: true,
    enum_values: null,
    minimum: null,
    maximum: null,
  };
}

export function OutputFieldsEditor({ fields, readOnly = false, onChange }: OutputFieldsEditorProps) {
  const patch = (index: number, changes: Partial<OutputField>) => {
    onChange(fields.map((field, i) => (i === index ? { ...field, ...changes } : field)));
  };

  const move = (index: number, delta: number) => {
    const target = index + delta;
    if (target < 0 || target >= fields.length) {
      return;
    }
    const next = [...fields];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  };

  const remove = (index: number) => {
    onChange(fields.filter((_, i) => i !== index));
  };

  const add = () => {
    onChange([...fields, emptyField()]);
  };

  return (
    <div className="output-fields">
      {fields.map((field, index) => (
        <div key={index} className="output-field-row">
          <input
            className="input"
            aria-label={`Field ${index} name`}
            placeholder="name"
            value={field.name}
            readOnly={readOnly}
            onChange={(event) => patch(index, { name: event.target.value })}
          />
          <Select
            aria-label={`Field ${index} type`}
            options={FIELD_TYPES}
            value={field.type}
            disabled={readOnly}
            onChange={(event) => {
              const type = event.target.value as FieldType;
              patch(index, {
                type,
                enum_values: type === "enum" ? (field.enum_values ?? []) : null,
                minimum: type === "int" || type === "float" ? field.minimum : null,
                maximum: type === "int" || type === "float" ? field.maximum : null,
              });
            }}
          />
          <input
            className="input"
            aria-label={`Field ${index} description`}
            placeholder="description"
            value={field.description}
            readOnly={readOnly}
            onChange={(event) => patch(index, { description: event.target.value })}
          />
          <label className="output-field-required">
            <input
              type="checkbox"
              checked={field.required}
              disabled={readOnly}
              onChange={(event) => patch(index, { required: event.target.checked })}
            />
            required
          </label>
          {field.type === "enum" && (
            <input
              className="input"
              aria-label={`Field ${index} enum values`}
              placeholder="comma,separated,values"
              value={(field.enum_values ?? []).join(", ")}
              readOnly={readOnly}
              onChange={(event) =>
                patch(index, {
                  enum_values: event.target.value
                    .split(",")
                    .map((value) => value.trim())
                    .filter((value) => value.length > 0),
                })
              }
            />
          )}
          {(field.type === "int" || field.type === "float") && (
            <div className="output-field-bounds">
              <input
                className="input"
                type="number"
                aria-label={`Field ${index} minimum`}
                placeholder="min"
                value={field.minimum ?? ""}
                readOnly={readOnly}
                onChange={(event) =>
                  patch(index, {
                    minimum: event.target.value === "" ? null : Number(event.target.value),
                  })
                }
              />
              <input
                className="input"
                type="number"
                aria-label={`Field ${index} maximum`}
                placeholder="max"
                value={field.maximum ?? ""}
                readOnly={readOnly}
                onChange={(event) =>
                  patch(index, {
                    maximum: event.target.value === "" ? null : Number(event.target.value),
                  })
                }
              />
            </div>
          )}
          {!readOnly && (
            <div className="output-field-controls">
              <Button
                variant="secondary"
                aria-label={`Move field ${index} up`}
                onClick={() => move(index, -1)}
              >
                ↑
              </Button>
              <Button
                variant="secondary"
                aria-label={`Move field ${index} down`}
                onClick={() => move(index, 1)}
              >
                ↓
              </Button>
              <Button
                variant="danger"
                aria-label={`Remove field ${index}`}
                onClick={() => remove(index)}
              >
                ×
              </Button>
            </div>
          )}
        </div>
      ))}
      {!readOnly && (
        <Button variant="secondary" onClick={add}>
          Add field
        </Button>
      )}
    </div>
  );
}

export default OutputFieldsEditor;
