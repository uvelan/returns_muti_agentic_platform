import { useCallback, useState } from "react";

const STORAGE_KEY = "platform.rail.collapsed";

/**
 * Whether the domain rail is collapsed, remembered across navigation.
 *
 * Persisted rather than held in component state: the rail unmounts and
 * remounts on every domain change, so plain state would silently re-expand it
 * each time an operator moved between domains -- which reads as the setting
 * not working rather than as a reset.
 *
 * `localStorage` is wrapped because it throws outright in a browser with
 * site data blocked, and losing a layout preference must not take the shell
 * down with it.
 */
function read(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

export function useRailCollapsed(): readonly [boolean, () => void] {
  const [collapsed, setCollapsed] = useState(read);

  const toggle = useCallback(() => {
    setCollapsed((previous) => {
      const next = !previous;
      try {
        window.localStorage.setItem(STORAGE_KEY, String(next));
      } catch {
        // Preference is lost for this session; the rail still toggles.
      }
      return next;
    });
  }, []);

  return [collapsed, toggle];
}
