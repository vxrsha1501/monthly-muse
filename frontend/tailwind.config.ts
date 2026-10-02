import type { Config } from "tailwindcss";

/** Design tokens (Section 12): calm editorial SaaS, one confident accent. */
const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          50: "#EEF2FF",
          100: "#E0E7FF",
          200: "#C7D2FE",
          500: "#6366F1",
          600: "#4F46E5",
          700: "#4338CA",
          800: "#3730A3",
        },
        success: "#059669",
        warning: "#F59E0B",
        danger: "#DC2626",
        info: "#0284C7",
        surface: "#FFFFFF",
        canvas: "#F8FAFC",
        ink: "#0F172A",
        "ink-2": "#475569",
        muted: "#64748B",
        line: "#E2E8F0",
        chart: {
          1: "#4F46E5",
          2: "#0D9488",
          3: "#F59E0B",
          4: "#DB2777",
          5: "#0284C7",
          6: "#7C3AED",
          7: "#64748B",
        },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      fontSize: {
        display: ["30px", { lineHeight: "36px", fontWeight: "600" }],
        h1: ["24px", { lineHeight: "32px", fontWeight: "600" }],
        h2: ["20px", { lineHeight: "28px", fontWeight: "600" }],
        h3: ["16px", { lineHeight: "24px", fontWeight: "600" }],
        body: ["14px", { lineHeight: "20px", fontWeight: "400" }],
        message: ["16px", { lineHeight: "26px", fontWeight: "400" }],
        small: ["12px", { lineHeight: "16px", fontWeight: "500" }],
      },
      borderRadius: {
        input: "8px",
        card: "12px",
        modal: "16px",
      },
      boxShadow: {
        card: "0 1px 2px rgba(15,23,42,0.06)",
        pop: "0 4px 12px rgba(15,23,42,0.08)",
        lift: "0 4px 12px rgba(15,23,42,0.10)",
      },
      maxWidth: { content: "1200px" },
      transitionDuration: { DEFAULT: "160ms" },
      keyframes: {
        fade: { "0%": { opacity: "0", transform: "translateY(4px)" }, "100%": { opacity: "1", transform: "none" } },
        pulseDot: { "0%, 100%": { opacity: "1" }, "50%": { opacity: "0.35" } },
      },
      animation: {
        fade: "fade 160ms ease-out",
        pulseDot: "pulseDot 1.2s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
export default config;
