import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#06080C",
          900: "#0A0D13",
          850: "#0E121A",
          800: "#121722",
          750: "#171D2A",
          700: "#1D2433",
          600: "#273043",
          500: "#36415A",
        },
        fg: { DEFAULT: "#E7ECF3", soft: "#B4BFCD", muted: "#7C8899", faint: "#566173" },
        brand: { DEFAULT: "#8C9BFF", strong: "#6B7CF6", soft: "#8C9BFF1F" },
        up: { DEFAULT: "#22C993", soft: "#22C9931A" },
        down: { DEFAULT: "#F2595F", soft: "#F2595F1A" },
        warn: { DEFAULT: "#F2B54A", soft: "#F2B54A1A" },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      boxShadow: {
        panel: "0 1px 0 0 rgba(255,255,255,0.03) inset, 0 8px 24px -12px rgba(0,0,0,0.6)",
        pop: "0 24px 64px -16px rgba(0,0,0,0.75)",
      },
      keyframes: {
        flashUp: { "0%": { backgroundColor: "#22C99333" }, "100%": { backgroundColor: "transparent" } },
        flashDown: { "0%": { backgroundColor: "#F2595F33" }, "100%": { backgroundColor: "transparent" } },
        rise: { "0%": { opacity: "0", transform: "translateY(6px)" }, "100%": { opacity: "1", transform: "none" } },
      },
      animation: {
        "flash-up": "flashUp 900ms ease-out",
        "flash-down": "flashDown 900ms ease-out",
        rise: "rise 260ms ease-out",
      },
    },
  },
  plugins: [],
};

export default config;
