import { useState, type ReactNode } from "react";
import { ChevronDown, ChevronUp, GripVertical } from "lucide-react";

/**
 * A reorderable list -- stage sequences, status ladders, provider order.
 *
 * Every item is reachable by keyboard: the up/down buttons are the real
 * mechanism, always present and always operable one item at a time. Drag and
 * drop is layered on top as a faster path for a mouse, never a replacement
 * for it, so nothing here depends on `draggable` actually working (jsdom,
 * for one, does not fire drag events).
 */
export function OrderedList<T>({
  label,
  error,
  items,
  onChange,
  renderItem,
  keyOf,
}: {
  label: string;
  /** A list-level error -- "at least one stage is required", an unreachable rung. */
  error?: string;
  items: readonly T[];
  onChange: (next: T[]) => void;
  renderItem: (item: T, index: number) => ReactNode;
  keyOf: (item: T) => string;
}) {
  const [dragIndex, setDragIndex] = useState<number | null>(null);

  function moveAt(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= items.length) return;
    const next = items.slice();
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  }

  function dropAt(index: number) {
    if (dragIndex === null || dragIndex === index) {
      setDragIndex(null);
      return;
    }
    const next = items.slice();
    const [moved] = next.splice(dragIndex, 1);
    next.splice(index, 0, moved);
    onChange(next);
    setDragIndex(null);
  }

  return (
    <div className="flex flex-col gap-1.5">
      <p className="premium-kicker">{label}</p>
      {error !== undefined ? <p role="alert" className="text-xs text-error">{error}</p> : null}
      <ul aria-label={label} className="flex flex-col gap-1.5">
        {items.map((item, index) => {
          const itemKey = keyOf(item);
          return (
            <li
              key={itemKey}
              draggable
              onDragStart={() => { setDragIndex(index); }}
              onDragOver={(event) => { event.preventDefault(); }}
              onDrop={() => { dropAt(index); }}
              onDragEnd={() => { setDragIndex(null); }}
              className="flex items-center gap-2 rounded-lg border border-outline-variant bg-surface-container-lowest p-2 shadow-sm"
            >
              <span aria-hidden="true" className="shrink-0 cursor-grab text-outline">
                <GripVertical size={14} />
              </span>
              <span className="min-w-0 flex-1">{renderItem(item, index)}</span>
              <span className="flex shrink-0 flex-col">
                <button
                  type="button"
                  aria-label={`Move ${itemKey} up`}
                  disabled={index === 0}
                  onClick={() => { moveAt(index, -1); }}
                  className="flex size-5 items-center justify-center rounded text-outline transition hover:bg-surface-container-low hover:text-primary disabled:cursor-not-allowed disabled:opacity-30"
                >
                  <ChevronUp size={12} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  aria-label={`Move ${itemKey} down`}
                  disabled={index === items.length - 1}
                  onClick={() => { moveAt(index, 1); }}
                  className="flex size-5 items-center justify-center rounded text-outline transition hover:bg-surface-container-low hover:text-primary disabled:cursor-not-allowed disabled:opacity-30"
                >
                  <ChevronDown size={12} aria-hidden="true" />
                </button>
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
