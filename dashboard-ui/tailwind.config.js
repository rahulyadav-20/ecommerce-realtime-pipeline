/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "sans-serif"],
      },
      colors: {
        // ── Base canvas ──────────────────────────────────────────────────────
        ink: {
          950: "#05070f",   // page background
          900: "#080c1a",   // sidebar
          800: "#0d1225",   // card background
          700: "#111a31",   // elevated surface
          600: "#1a263f",   // borders / dividers
          500: "#253350",   // muted borders
          400: "#364d6e",   // placeholder / disabled
        },
        // ── Accent (indigo-blue — professional analytics look) ───────────────
        accent: {
          50:  "#eef2ff",
          100: "#e0e7ff",
          300: "#a5b4fc",
          400: "#818cf8",
          500: "#6366f1",
          600: "#4f46e5",
          700: "#4338ca",
        },
        // ── Semantic chart colours ────────────────────────────────────────────
        chart: {
          blue:    "#3b82f6",
          indigo:  "#6366f1",
          purple:  "#8b5cf6",
          pink:    "#ec4899",
          teal:    "#14b8a6",
          green:   "#22c55e",
          yellow:  "#eab308",
          orange:  "#f97316",
          red:     "#ef4444",
        },
        // ── Status ────────────────────────────────────────────────────────────
        up:       "#22c55e",
        degraded: "#eab308",
        down:     "#ef4444",
      },
      backgroundImage: {
        "gradient-radial":         "radial-gradient(var(--tw-gradient-stops))",
        "gradient-card":           "linear-gradient(135deg, #0d1225 0%, #111a31 100%)",
        "gradient-accent":         "linear-gradient(135deg, #6366f1 0%, #3b82f6 100%)",
        "gradient-green":          "linear-gradient(135deg, #22c55e 0%, #14b8a6 100%)",
        "gradient-revenue":        "linear-gradient(180deg, rgba(99,102,241,0.2) 0%, rgba(99,102,241,0) 100%)",
      },
      boxShadow: {
        card:   "0 1px 3px rgba(0,0,0,0.4), 0 1px 2px rgba(0,0,0,0.6)",
        glow:   "0 0 20px rgba(99,102,241,0.15)",
        "glow-green": "0 0 12px rgba(34,197,94,0.25)",
      },
      animation: {
        "fade-in":    "fadeIn 0.3s ease-out",
        "slide-up":   "slideUp 0.3s ease-out",
        "pulse-slow": "pulse 3s cubic-bezier(0.4,0,0.6,1) infinite",
        "blink":      "blink 1.4s ease-in-out infinite",
      },
      keyframes: {
        fadeIn:  { from: { opacity: "0" }, to: { opacity: "1" } },
        slideUp: { from: { opacity: "0", transform: "translateY(8px)" }, to: { opacity: "1", transform: "none" } },
        blink:   { "0%,100%": { opacity: "1" }, "50%": { opacity: "0.3" } },
      },
    },
  },
  plugins: [],
};
