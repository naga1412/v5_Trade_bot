import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: {
          base: "var(--bg-base)",
          chart: "var(--bg-chart)",
          panel: "var(--bg-panel)",
          elevated: "var(--bg-elevated)",
        },
        border: {
          DEFAULT: "var(--border)",
          strong: "var(--border-strong)",
        },
        text: {
          primary: "var(--text-primary)",
          secondary: "var(--text-secondary)",
          tertiary: "var(--text-tertiary)",
        },
        green: { DEFAULT: "var(--green)", 15: "var(--green-15)" },
        red: { DEFAULT: "var(--red)", 15: "var(--red-15)" },
        gold: { DEFAULT: "var(--gold)", 15: "var(--gold-15)" },
        purple: { DEFAULT: "var(--purple)", 15: "var(--purple-15)" },
        cyan: { DEFAULT: "var(--cyan)" },
        orange: { DEFAULT: "var(--orange)" },
        pink: { DEFAULT: "var(--pink)" },
      },
      fontFamily: {
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
        sans: ["Inter", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
} satisfies Config;
