import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      colors: {
        ink: {
          900: "#0a0a0a",
          800: "#161616",
          700: "#1f1f1f",
          600: "#2a2a2a",
          500: "#3a3a3a",
          400: "#6e6e6e",
          300: "#a0a0a0",
          200: "#d0d0d0",
          100: "#eaeaea",
          50: "#f7f7f7",
        },
        accent: { DEFAULT: "#00d4a4", muted: "#0a8060" },
      },
    },
  },
  plugins: [],
};
export default config;
