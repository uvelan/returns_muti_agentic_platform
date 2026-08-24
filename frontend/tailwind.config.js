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

        // --- Graph Schema Analyzer ------------------------------------------
        //
        // The analyzer is a dark emerald world while the rest of the platform is
        // light teal, and it stays that way. The note at the top of this file
        // says its screens "re-skin by substitution, since the token names they
        // reference are these ones" -- but they referenced no token at all: 419
        // literal colour classes and 51 arbitrary hex values across four
        // screens, which is why substituting anything was never possible.
        //
        // These are that world, named. Prefixed rather than folded into the M3
        // roles above, because those roles describe a light surface and an
        // analyzer panel is not a dimmer version of a platform panel -- it is a
        // different ground with its own contrast relationships.
        //
        // **Nine near-blacks collapsed to four.** `#0a1714`, `#081511`,
        // `#091511`, `#091814`, `#0b1b16` and `#0c1915` all sit within 1.03:1
        // of each other -- indistinguishable, and drift rather than intent. The
        // four that remain are real steps: the page, its panels, the wells sunk
        // into them, and the one raised element above them.
        "analyzer-surface": "#050c0a",
        "analyzer-surface-container": "#0a1714",
        "analyzer-surface-sunken": "#07120f",
        "analyzer-surface-raised": "#101f1b",

        // Boundaries. `outline-variant` is 1.21:1 on its own ground and belongs
        // between two regions, never around a control -- the same distinction
        // `outline-variant` and `outline-control` draw on the light side.
        "analyzer-outline": "#064e3b",
        "analyzer-outline-variant": "#022c22",
        // The boundary of a form control, which WCAG 1.4.11 governs at 3:1.
        // 3.34:1 on panels, 3.47:1 on wells, 3.60:1 on the page. Ten of the
        // analyzer's thirteen controls were drawn with `outline-variant` at
        // 1.21:1, so the only thing marking a field was invisible to anyone who
        // needed it to be visible. This is emerald-700 rather than an invented
        // colour, so the world is unchanged apart from the edges now being there.
        "analyzer-outline-control": "#047857",
        // The same job on the analyzer's outlined secondary buttons, which are
        // neutral by design and stay neutral: 3.85:1, where `slate-700` was
        // 1.77:1. Separate from the token above so nobody reaches for a grey
        // edge on an input, or a green one on a Cancel.
        "analyzer-outline-control-neutral": "#64748b",

        // Accent. `on-primary` reads 7.88:1 on `primary`.
        //
        // `on-primary`, `primary-container` and `outline-variant` share a value
        // and are three tokens anyway: the first is text on the emerald fill,
        // the second is the ground under a chip, the third is a divider. They
        // coincide today and a change to one should not silently move the other
        // two, which is the whole reason a role is not a colour.
        "analyzer-primary": "#34d399",
        "analyzer-on-primary": "#022c22",
        "analyzer-primary-container": "#022c22",
        "analyzer-accent": "#6ee7b7",

        // Text, in three steps: emphasis, body, muted. 14.87:1, 12.35:1 and
        // 7.15:1 on `surface-container`, so all three clear 1.4.3 with room.
        //
        // `on-surface` is the *body* weight because that is what 16 uses of it
        // are -- button labels, names, monospace payloads. Naming the four-use
        // emphasis colour `on-surface` and leaving body unnamed is how a token
        // set stops being usable: the next person reaches for the obvious name
        // and gets the wrong weight.
        //
        // The muted step is the 115-use workhorse and passes at 7.15:1, which is
        // why the grey-on-colour warnings over this feature were false positives.
        "analyzer-on-surface-emphasis": "#e2e8f0",
        "analyzer-on-surface": "#cbd5e1",
        "analyzer-on-surface-variant": "#94a3b8",

        // Status, at the two weights that recur with one intent. The long tail
        // of red and amber steps stays literal on purpose: a token per value is
        // not a design system, and those uses are genuinely one-off.
        "analyzer-error": "#fca5a5",
        "analyzer-error-container": "#450a0a",
        "analyzer-warning": "#fcd34d",
        "analyzer-warning-container": "#451a03",
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
