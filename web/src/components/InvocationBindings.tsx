import { useState } from "react";
import type { InvocationMapping } from "../api/types";

type Props = {
  columns: string[];
  metadataColumns?: string[];
  required: string[];
  mapping: InvocationMapping;
  onChange: (mapping: InvocationMapping) => void;
  recorded?: boolean;
  outputOnly?: boolean;
  metadataOnly?: boolean;
};

export default function InvocationBindings({ columns, metadataColumns = columns, required, mapping, onChange, recorded, outputOnly, metadataOnly }: Props) {
  const [extra, setExtra] = useState("");
  const names = [...new Set([...required, ...Object.keys(mapping.metadata ?? {})])];
  function select(label: string, value: string, update: (column: string) => void, automatic: string, options = columns) {
    return <label className="field" key={label}><span className="field-label">{label}</span>
      <select aria-label={label} value={value} onChange={e => update(e.target.value)}>
        <option value="">{automatic}</option>
        {options.map(column => <option key={column} value={column}>{column}</option>)}
      </select></label>;
  }
  return <fieldset><legend>Invocation bindings</legend>
    {recorded && <p className="muted">Input and output come from the recorded agent invocation.</p>}
    {!metadataOnly && <>
      {!outputOnly && select("Input column", mapping.input ?? "", value => { const next = { ...mapping }; if (value) next.input = value; else delete next.input; onChange(next); }, recorded ? "Recorded input" : "Select input…")}
      {select("Output column", mapping.output ?? "", value => { const next = { ...mapping }; if (value) next.output = value; else delete next.output; onChange(next); }, recorded ? "Recorded output" : "Select output…")}
    </>}
    {names.map(name => select(`Metadata: ${name}`, mapping.metadata?.[name] ?? "", value => {
      const metadata = { ...mapping.metadata }; if (value) metadata[name] = value; else delete metadata[name];
      onChange({ ...mapping, metadata });
    }, metadataColumns.includes(name) ? `Use ${name}` : "Select metadata column…", metadataColumns))}
    <label className="field"><span className="field-label">Additional metadata name</span><input aria-label="Additional metadata name" value={extra} onChange={e => setExtra(e.target.value)} /></label>
    <button type="button" disabled={!extra.trim() || names.includes(extra.trim())} onClick={() => { onChange({ ...mapping, metadata: { ...mapping.metadata, [extra.trim()]: metadataColumns[0] ?? "" } }); setExtra(""); }}>Add metadata binding</button>
  </fieldset>;
}
