import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg:        "#0f1117",
        surface:   "#1a1d2e",
        surface2:  "#22253a",
        border:    "#2a2d44",
        accent:    "#7c3aed",
        accent2:   "#0ea5e9",
        success:   "#22c55e",
        danger:    "#ef4444",
        warning:   "#f59e0b",
        muted:     "#8b8fae",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
