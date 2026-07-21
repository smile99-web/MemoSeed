import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: ["./src/app/**/*.{ts,tsx}", "./src/components/**/*.{ts,tsx}", "./src/lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      screens: {
        // iPad Mini portrait: 744px, iPad Pro 11" portrait: 834px
        ipad: "744px",
        // iPad landscape / larger tablets
        "ipad-lg": "1024px",
      },
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      boxShadow: {
        soft: "0 8px 30px -12px rgb(15 23 42 / 0.18)",
        "glow-cyan": "0 8px 28px -8px rgb(8 145 178 / 0.5)",
        "glow-emerald": "0 8px 28px -8px rgb(5 150 105 / 0.5)",
        "glow-violet": "0 8px 28px -8px rgb(124 58 237 / 0.5)",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};

export default config;
