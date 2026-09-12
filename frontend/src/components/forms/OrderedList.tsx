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
  fixedTrailing,
}: {
  label: string;
  /** A list-level error -- "at least one stage is required", an unreachable rung. */
  error?: string;
  items: readonly T[];
  onChange: (next: T[]) => void;
  renderItem: (item: T, index: number) => ReactNode;
  keyOf: (item: T) => string;
  /**
   * CFG-8 A1: an item this predicate matches is rendered non-movable -- no
   * drag handle, up/down buttons disabled, and nothing else can be moved
   * past it. For a constraint a backend model validator enforces anyway
   * (e.g. `FERGUSON_STANDARD_RETURN` pinned last in a precedence list), the
   * control itself should refuse the drag rather than let an operator drop
   * it, run Validate, and learn only from the page-level error list.
   */
  fixedTrailing?: (item: T) => boolean;
}) {
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const isFixed = (item: T) => fixedTrailing?.(item) === true;

  function moveAt(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= items.length) return;
    if (isFixed(items[index]) || isFixed(items[target])) return;
    const next = items.slice();
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  }

  function dropAt(index: number) {
    if (dragIndex === null || dragIndex === index || isFixed(items[index]) || isFixed(items[dragIndex])) {
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
          const fixed = isFixed(item);
          return (
            <li
              key={itemKey}
              draggable={!fixed}
              onDragStart={() => { if (!fixed) setDragIndex(index); }}
              onDragOver={(event) => { event.preventDefault(); }}
              onDrop={() => { dropAt(index); }}
              onDragEnd={() => { setDragIndex(null); }}
              className="flex items-center gap-2 rounded-lg border border-outline-variant bg-surface-container-lowest p-2 shadow-sm"
            >
              <span
                aria-hidden="true"
                className={fixed ? "shrink-0 text-outline-variant" : "shrink-0 cursor-grab text-outline"}
              >
                <GripVertical size={14} />
              </span>
              <span className="min-w-0 flex-1">{renderItem(item, index)}</span>
              {fixed ? (
                <span className="shrink-0 text-[10px] font-medium uppercase tracking-wide text-on-surface-variant">
                  Fixed last
                </span>
              ) : (
                <span className="flex shrink-0 flex-col">
                  <button
                    type="button"
                    aria-label={`Move ${itemKey} up`}
                    disabled={index === 0 || isFixed(items[index - 1])}
                    onClick={() => { moveAt(index, -1); }}
                    className="flex size-5 items-center justify-center rounded text-outline transition hover:bg-surface-container-low hover:text-primary disabled:cursor-not-allowed disabled:opacity-30"
                  >
                    <ChevronUp size={12} aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    aria-label={`Move ${itemKey} down`}
                    disabled={index === items.length - 1 || isFixed(items[index + 1])}
                    onClick={() => { moveAt(index, 1); }}
                    className="flex size-5 items-center justify-center rounded text-outline transition hover:bg-surface-container-low hover:text-primary disabled:cursor-not-allowed disabled:opacity-30"
                  >
                    <ChevronDown size={12} aria-hidden="true" />
                  </button>
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
