import { ChevronDown, ChevronRight, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { fieldLabel } from "./configurationEditorUtils";

type ConfigurationRecord = Record<string, unknown>;

type StructuredConfigurationEditorProps = {
  value: unknown;
  onChange: (value: unknown) => void;
  disabled?: boolean;
  path?: string[];
};

function isRecord(value: unknown): value is ConfigurationRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function fieldId(path: string[]): string {
  return `configuration-${path.join("-").replace(/[^A-Za-z0-9_-]/g, "-")}`;
}

function defaultArrayItem(items: unknown[]): unknown {
  const sample = items[0];
  if (typeof sample === "boolean") return false;
  if (typeof sample === "number") return 0;
  if (isRecord(sample)) return {};
  return "";
}

function PrimitiveEditor({
  value,
  onChange,
  disabled,
  path,
}: Required<Pick<StructuredConfigurationEditorProps, "onChange" | "path">> &
  Pick<StructuredConfigurationEditorProps, "value" | "disabled">) {
  const id = fieldId(path);
  const label = fieldLabel(path.at(-1) ?? "Value");

  if (typeof value === "boolean") {
    return (
      <div className="flex items-center justify-between gap-4 rounded-md border border-gray-200 bg-white px-3 py-2.5">
        <div>
          <label htmlFor={id} className="text-sm font-medium text-gray-800">{label}</label>
          <p className="font-mono text-[11px] text-gray-400">{path.join(".")}</p>
        </div>
        <button
          id={id}
          type="button"
          role="switch"
          aria-checked={value}
          aria-label={label}
          disabled={disabled}
          onClick={() => { onChange(!value); }}
          className={`relative h-6 w-11 rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${value ? "bg-blue-600" : "bg-gray-300"}`}
        >
          <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform ${value ? "translate-x-5" : "translate-x-0.5"}`} />
        </button>
      </div>
    );
  }

  if (typeof value === "number") {
    return (
      <div className="space-y-1.5">
        <label htmlFor={id} className="text-xs font-semibold text-gray-700">{label}</label>
        <input
          id={id}
          type="number"
          value={value}
          disabled={disabled}
          onChange={(event) => { onChange(Number(event.target.value)); }}
          className="w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 disabled:bg-gray-100"
        />
        <p className="font-mono text-[11px] text-gray-400">{path.join(".")}</p>
      </div>
    );
  }

  const textValue = typeof value === "string" ? value : "";
  const multiline = textValue.includes("\n") || textValue.length > 120;
  const className = "w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 disabled:bg-gray-100";

  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="text-xs font-semibold text-gray-700">{label}</label>
      {multiline ? (
        <textarea id={id} rows={4} value={textValue} disabled={disabled} onChange={(event) => { onChange(event.target.value); }} className={className} />
      ) : (
        <input id={id} type="text" value={textValue} disabled={disabled} onChange={(event) => { onChange(event.target.value); }} className={className} />
      )}
      <p className="font-mono text-[11px] text-gray-400">{path.join(".")}</p>
    </div>
  );
}

function ArrayEditor({ value, onChange, disabled, path }: StructuredConfigurationEditorProps & { value: unknown[] }) {
  const resolvedPath = path ?? [];
  const label = fieldLabel(resolvedPath.at(-1) ?? "Items");
  return (
    <section className="rounded-lg border border-gray-200 bg-gray-50/70 p-3">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <h4 className="text-xs font-semibold text-gray-800">{label}</h4>
          <p className="text-[11px] text-gray-500">{value.length} configured item{value.length === 1 ? "" : "s"}</p>
        </div>
        <button type="button" disabled={disabled} onClick={() => { onChange([...value, defaultArrayItem(value)]); }} className="inline-flex items-center gap-1 rounded-md border border-blue-200 bg-blue-50 px-2.5 py-1.5 text-xs font-semibold text-blue-700 hover:bg-blue-100 disabled:opacity-50">
          <Plus className="h-3.5 w-3.5" /> Add item
        </button>
      </div>
      <div className="space-y-2">
        {value.map((item, index) => (
          <div key={`${resolvedPath.join(".")}-${String(index)}`} className="flex items-start gap-2 rounded-md border border-gray-200 bg-white p-3">
            <div className="min-w-0 flex-1">
              <StructuredConfigurationEditor
                value={item}
                disabled={disabled}
                path={[...resolvedPath, String(index)]}
                onChange={(nextItem) => {
                  const next = [...value];
                  next[index] = nextItem;
                  onChange(next);
                }}
              />
            </div>
            <button type="button" aria-label={`Remove ${label} item ${String(index + 1)}`} disabled={disabled} onClick={() => { onChange(value.filter((_, itemIndex) => itemIndex !== index)); }} className="rounded p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600 disabled:opacity-50">
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        ))}
        {value.length === 0 && <p className="rounded-md border border-dashed border-gray-300 p-4 text-center text-xs text-gray-500">No values configured.</p>}
      </div>
    </section>
  );
}

function ObjectEditor({ value, onChange, disabled, path }: StructuredConfigurationEditorProps & { value: ConfigurationRecord }) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const entries = Object.entries(value);
  const resolvedPath = path ?? [];

  if (entries.length === 0) {
    return <p className="rounded-md border border-dashed border-gray-300 p-4 text-center text-xs text-gray-500">No values configured.</p>;
  }

  return (
    <div className="space-y-3">
      {entries.map(([key, item]) => {
        const itemPath = [...resolvedPath, key];
        const nested = isRecord(item) || Array.isArray(item);
        const isExpanded = expanded[key] ?? true;
        if (!nested) {
          return <PrimitiveEditor key={key} value={item} disabled={disabled} path={itemPath} onChange={(nextItem) => { onChange({ ...value, [key]: nextItem }); }} />;
        }
        return (
          <section key={key} className="rounded-lg border border-gray-200 bg-white shadow-sm">
            <button type="button" onClick={() => { setExpanded((current) => ({ ...current, [key]: !isExpanded })); }} className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left">
              <span>
                <span className="block text-sm font-semibold text-gray-800">{fieldLabel(key)}</span>
                <span className="block font-mono text-[11px] text-gray-400">{itemPath.join(".")}</span>
              </span>
              {isExpanded ? <ChevronDown className="h-4 w-4 text-gray-400" /> : <ChevronRight className="h-4 w-4 text-gray-400" />}
            </button>
            {isExpanded && (
              <div className="border-t border-gray-100 bg-gray-50/40 p-3">
                <StructuredConfigurationEditor value={item} disabled={disabled} path={itemPath} onChange={(nextItem) => { onChange({ ...value, [key]: nextItem }); }} />
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}

export function StructuredConfigurationEditor({ value, onChange, disabled = false, path = [] }: StructuredConfigurationEditorProps) {
  if (Array.isArray(value)) return <ArrayEditor value={value} onChange={onChange} disabled={disabled} path={path} />;
  if (isRecord(value)) return <ObjectEditor value={value} onChange={onChange} disabled={disabled} path={path} />;
  return <PrimitiveEditor value={value} onChange={onChange} disabled={disabled} path={path} />;
}

