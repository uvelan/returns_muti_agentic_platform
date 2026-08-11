/**
 * Platform theme: "Deep Forest Enterprise".
 *
 * The two Stitch design kits (returns platform v2, graph schema analyzer) emit
 * the same Material 3 token names and differ only in palette -- v2 is teal
 * (#004e47), the analyzer is blue (#005bbf). Consistency is therefore a matter
 * of choosing one set of values, not of reconciling two design languages.
 *
 * Teal wins because the copilot is the surface an associate lives in all day
 * and the v2 kit already carries the shell (rail, top bar, status chips) that
 * every other domain has to sit inside. The analyzer's screens re-skin by
 * substitution, since the token names they reference are these ones.
 *
 * Exposed under their bare M3 names on purpose: `bg-primary`, `text-on-surface`
 * and `border-outline-variant` are what the exported Stitch markup already
 * says, so a screen can be brought across without a rename pass that would
 * quietly drift from the design.
 *
 * @type {import('tailwindcss').Config}
 */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        surface: "#f7faf8",
        "surface-dim": "#d7dbd9",
        "surface-bright": "#f7faf8",
        "surface-container-lowest": "#ffffff",
        "surface-container-low": "#f1f4f2",
        "surface-container": "#ebefed",
        "surface-container-high": "#e6e9e7",
        "surface-container-highest": "#e0e3e1",
        "on-surface": "#181c1c",
        "on-surface-variant": "#3e4947",
        "inverse-surface": "#2d3130",
        "inverse-on-surface": "#eef1ef",
        outline: "#6e7977",
        "outline-variant": "#bec9c6",
        "surface-tint": "#066a61",
        primary: "#004e47",
        "on-primary": "#ffffff",
        "primary-container": "#00685f",
        "on-primary-container": "#93e4d8",
        "inverse-primary": "#85d5c9",
        secondary: "#466460",
        "on-secondary": "#ffffff",
        "secondary-container": "#c5e6e1",
        "on-secondary-container": "#4a6864",
        tertiary: "#6e341d",
        "on-tertiary": "#ffffff",
        "tertiary-container": "#8b4b32",
        "on-tertiary-container": "#ffc9b7",
        error: "#ba1a1a",
        "on-error": "#ffffff",
        "error-container": "#ffdad6",
        "on-error-container": "#93000a",
        // The near-black rail from the v2 shell. Not an M3 role -- the kit
        // paints the navigation with it directly, so it is named for what it
        // is rather than mapped onto a role it does not play.
        "rail-surface": "#0b1f1c",
        "rail-on-surface": "#e6efec",
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        // The analyzer kit sets identifiers, Cypher and field paths in a mono
        // face. Kept, because a graph property name is a literal and reads
        // wrongly in a proportional face.
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
