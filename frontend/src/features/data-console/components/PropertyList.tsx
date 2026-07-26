/* eslint-disable */
import { type DisplayValueType } from "../../../contracts/browser";

export type PropertyItem = {
  label: string;
  value: unknown;
  type?: DisplayValueType;
  redacted?: boolean;
};

type PropertyListProps = {
  properties: PropertyItem[];
  className?: string;
}

export function PropertyList({ properties, className = "" }: PropertyListProps) {
  return (
    <div className={`border-t border-gray-200 ${className}`}>
      <dl className="divide-y divide-gray-200">
        {properties.map((prop, idx) => (
          <div key={idx} className="py-4 sm:grid sm:grid-cols-3 sm:gap-4 sm:py-5">
            <dt className="text-sm font-medium text-gray-500">{prop.label}</dt>
            <dd className="mt-1 text-sm text-gray-900 sm:col-span-2 sm:mt-0 flex items-center">
              {prop.redacted ? (
                <span className="inline-flex items-center rounded-md bg-red-50 px-2 py-1 text-xs font-medium text-red-700 ring-1 ring-inset ring-red-600/10">
                  REDACTED
                </span>
              ) : prop.type === "NULL" || prop.value === null ? (
                <span className="text-gray-400 italic">null</span>
              ) : prop.type === "BOOLEAN" ? (
                <span>{prop.value ? "true" : "false"}</span>
              ) : (
                <span>{typeof prop.value === 'object' && prop.value !== null ? JSON.stringify(prop.value) : String(prop.value)}</span>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
