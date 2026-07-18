import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./src/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eff6ff",
          100: "#dbeafe",
          200: "#bfdbfe",
          300: "#93c5fd",
          400: "#60a5fa",
          500: "#3b82f6",
          600: "#2563eb",
          700: "#1d4ed8",
          800: "#1e40af",
          900: "#1e3a8a",
          950: "#172554",
        },
      },
      keyframes: {
        "aurora-a": {
          "0%, 100%": { transform: "translate(0,0) scale(1)", opacity: ".45" },
          "50%": { transform: "translate(50px,30px) scale(1.15)", opacity: ".7" },
        },
        "aurora-b": {
          "0%, 100%": { transform: "translate(0,0) scale(1.1)", opacity: ".35" },
          "50%": { transform: "translate(-45px,-25px) scale(1)", opacity: ".6" },
        },
        "aurora-sweep": {
          "0%": { transform: "rotate(0deg) scale(1.3)" },
          "100%": { transform: "rotate(360deg) scale(1.3)" },
        },
      },
      animation: {
        "aurora-a": "aurora-a 18s ease-in-out infinite",
        "aurora-b": "aurora-b 22s ease-in-out infinite",
        "aurora-sweep": "aurora-sweep 26s linear infinite",
      },
    },
  },
  plugins: [],
};

export default config;
