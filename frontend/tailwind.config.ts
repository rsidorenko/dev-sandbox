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
          "0%, 100%": { transform: "translate(0,0) scale(1)", opacity: ".6" },
          "50%": { transform: "translate(110px,70px) scale(1.25)", opacity: ".9" },
        },
        "aurora-b": {
          "0%, 100%": { transform: "translate(0,0) scale(1.15)", opacity: ".55" },
          "50%": { transform: "translate(-100px,-60px) scale(1)", opacity: ".85" },
        },
        "aurora-sweep": {
          "0%": { transform: "rotate(0deg) scale(1.4)" },
          "100%": { transform: "rotate(360deg) scale(1.4)" },
        },
        "fade-in-up": {
          "0%": { opacity: "0", transform: "translateY(16px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        shimmer: {
          "0%": { backgroundPosition: "0% 50%" },
          "100%": { backgroundPosition: "200% 50%" },
        },
      },
      animation: {
        "aurora-a": "aurora-a 7s ease-in-out infinite",
        "aurora-b": "aurora-b 8s ease-in-out infinite",
        "aurora-sweep": "aurora-sweep 9s linear infinite",
        "fade-in-up": "fade-in-up 0.6s ease-out both",
        "fade-in": "fade-in 0.6s ease-out both",
        shimmer: "shimmer 4s linear infinite",
      },
    },
  },
  plugins: [],
};

export default config;
