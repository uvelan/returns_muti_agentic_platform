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
        // 5.66:1 on `surface`, 5.13:1 on `surface-container`. It was #6e7977 --
        // 4.28:1 on surface, and 4.07:1 once a panel darkened the ground under
        // it. One token, 119 failing nodes across 39 of 40 routes, and every
        // usage is small secondary text (10-12px), so the large-text exception
        // never applied anywhere.
        outline: "#5b6664",
        // Panel borders and dividers. Deliberately unchanged: 1.4.11 governs the
        // boundary of a *component*, not a rule between two regions, and
        // darkening all 183 usages to satisfy 49 form controls would repaint
        // every panel in the product to fix inputs.
        "outline-variant": "#bec9c6",
        // The boundary of a form control, which 1.4.11 does govern at 3:1.
        // 3.43:1 on white, 3.26:1 on `surface`.
        //
        // A separate token because an input's fill is `surface` on a page whose
        // background is also `surface` -- literally 1.00:1 -- and `.premium-field`
        // is `surface-container-lowest` on a white panel, also 1.00:1. The 1px
        // border is the only thing that says "this is an input", and at 1.62:1 it
        // was invisible to anyone who needed it to be visible.
        "outline-control": "#828d8a",
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
      boxShadow: {
        panel: "0 18px 45px -28px rgb(11 31 28 / 0.42), 0 2px 8px rgb(11 31 28 / 0.05)",
        float: "0 22px 60px -32px rgb(0 78 71 / 0.38)",
      },
    },
  },
  plugins: [],
};
