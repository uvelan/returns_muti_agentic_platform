export type FilterOption = {
  label: string;
  value: string;
};

type FilterBarProps = {
  label: string;
  options: FilterOption[];
  value: string;
  onChange: (value: string) => void;
  className?: string;
}

export function FilterBar({ label, options, value, onChange, className = "" }: FilterBarProps) {
  return (
    <div className={`flex items-center space-x-2 ${className}`}>
      <label className="text-sm font-medium text-gray-700">{label}:</label>
      <select
        className="block w-full pl-3 pr-10 py-2 text-base border-gray-300 focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm rounded-md"
        value={value}
        onChange={(e) => { onChange(e.target.value); }}
        aria-label={`Filter by ${label}`}
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    </div>
  );
}
