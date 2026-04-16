import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}"
  ],
  theme: {
    extend: {
      colors: {
        navy: "#0A1F3F",
        blue: "#1B5E9E",
        gold: "#E8A838",
        success: "#27AE60",
        warning: "#F39C12",
        danger: "#E74C3C",
        surface: "#ECF0F1",
        ink: "#10233F"
      },
      boxShadow: {
        shell: "0 18px 40px rgba(10, 31, 63, 0.12)"
      }
    }
  },
  plugins: []
};

export default config;

