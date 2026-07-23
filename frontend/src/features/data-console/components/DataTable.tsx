import React from "react";

export type ColumnDef<T> = {
  header: string;
  accessor: (item: T) => React.ReactNode;
  className?: string;
};

type DataTableProps<T> = {
  data: T[];
  columns: ColumnDef<T>[];
  keyExtractor: (item: T) => string;
  onRowClick?: (item: T) => void;
  className?: string;
}

export function DataTable<T>({ data, columns, keyExtractor, onRowClick, className = "" }: DataTableProps<T>) {
  if (data.length === 0) {
    return (
      <div className={`p-8 text-center text-gray-500 border border-gray-200 rounded-lg ${className}`}>
        No data available
      </div>
    );
  }

  return (
    <div className={`overflow-x-auto border border-gray-200 rounded-lg shadow-sm ${className}`}>
      <table className="min-w-full divide-y divide-gray-200">
        <thead className="bg-gray-50">
          <tr>
            {columns.map((col, idx) => (
              <th
                key={idx}
                scope="col"
                className={`px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider ${col.className ?? ""}`}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="bg-white divide-y divide-gray-200">
          {data.map((item) => (
            <tr 
              key={keyExtractor(item)}
              onClick={() => onRowClick?.(item)}
              className={onRowClick ? "cursor-pointer hover:bg-gray-50 transition-colors" : ""}
            >
              {columns.map((col, idx) => (
                <td key={idx} className={`px-6 py-4 whitespace-nowrap text-sm text-gray-900 ${col.className ?? ""}`}>
                  {col.accessor(item)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
