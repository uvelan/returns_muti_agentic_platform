import { ChevronLeft, ChevronRight } from "lucide-react";

type PaginationControlsProps = {
  pageIndex: number;
  pageSize: number;
  hasMore: boolean;
  onPageChange: (newPageIndex: number) => void;
  onPageSizeChange: (newPageSize: number) => void;
  className?: string;
}

export function PaginationControls({ pageIndex, pageSize, hasMore, onPageChange, onPageSizeChange, className = "" }: PaginationControlsProps) {
  return (
    <div className={`flex items-center justify-between px-4 py-3 bg-white border-t border-gray-200 sm:px-6 ${className}`}>
      <div className="flex items-center">
        <span className="text-sm text-gray-700 mr-2">Rows per page:</span>
        <select
          value={pageSize}
          onChange={(e) => { onPageSizeChange(Number(e.target.value)); }}
          className="block w-20 pl-3 pr-10 py-1.5 text-sm border-gray-300 focus:outline-none focus:ring-blue-500 focus:border-blue-500 rounded-md"
          aria-label="Rows per page"
        >
          {[10, 20, 50, 100].map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </select>
      </div>
      
      <div className="flex items-center space-x-2">
        <button
          onClick={() => { onPageChange(Math.max(0, pageIndex - 1)); }}
          disabled={pageIndex === 0}
          className={`relative inline-flex items-center px-2 py-2 rounded-md border border-gray-300 bg-white text-sm font-medium ${
            pageIndex === 0 ? "text-gray-300 cursor-not-allowed" : "text-gray-500 hover:bg-gray-50"
          }`}
          aria-label="Previous page"
        >
          <ChevronLeft className="h-5 w-5" aria-hidden="true" />
        </button>
        <span className="text-sm text-gray-700">
          Page {pageIndex + 1}
        </span>
        <button
          onClick={() => { onPageChange(pageIndex + 1); }}
          disabled={!hasMore}
          className={`relative inline-flex items-center px-2 py-2 rounded-md border border-gray-300 bg-white text-sm font-medium ${
            !hasMore ? "text-gray-300 cursor-not-allowed" : "text-gray-500 hover:bg-gray-50"
          }`}
          aria-label="Next page"
        >
          <ChevronRight className="h-5 w-5" aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}
