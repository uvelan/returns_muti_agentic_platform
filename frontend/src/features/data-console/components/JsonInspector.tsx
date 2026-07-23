/* eslint-disable */
import React from "react";

type JsonInspectorProps = {
  data: unknown;
  redactedPaths?: string[];
  className?: string;
}

export function JsonInspector({ data, redactedPaths = [], className = "" }: JsonInspectorProps) {
  // A simplistic JSON renderer. In a real app, you might use a library like react-json-view.
  const renderValue = (value: unknown, path: string): React.ReactNode => {
    if (redactedPaths.includes(path)) {
      return <span className="text-red-500 font-bold">[REDACTED]</span>;
    }

    if (value === null) return <span className="text-gray-400">null</span>;
    if (typeof value === "boolean") return <span className="text-blue-500">{value.toString()}</span>;
    if (typeof value === "number") return <span className="text-green-600">{value}</span>;
    if (typeof value === "string") return <span className="text-orange-600">"{value}"</span>;
    
    if (Array.isArray(value)) {
      return (
        <div className="pl-4 border-l border-gray-200">
          <span className="text-gray-500">[</span>
          {value.map((item, idx) => (
            <div key={idx}>
              {renderValue(item, `${path}[${String(idx)}]`)}
              {idx < value.length - 1 && <span className="text-gray-500">,</span>}
            </div>
          ))}
          <span className="text-gray-500">]</span>
        </div>
      );
    }
    
    if (typeof value === "object") {
      return (
        <div className="pl-4 border-l border-gray-200">
          <span className="text-gray-500">{'{'}</span>
          {Object.entries(value as Record<string, unknown>).map(([key, val], idx, arr) => (
            <div key={key} className="flex">
              <span className="text-purple-600 mr-2">"{key}":</span>
              <span>
                {renderValue(val, path ? `${path}.${key}` : key)}
                {idx < arr.length - 1 && <span className="text-gray-500">,</span>}
              </span>
            </div>
          ))}
          <span className="text-gray-500">{'}'}</span>
        </div>
      );
    }
    
    return <span>{typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value)}</span>;
  };

  return (
    <div className={`font-mono text-sm bg-gray-50 p-4 rounded-md overflow-x-auto ${className}`}>
      {renderValue(data, "")}
    </div>
  );
}

