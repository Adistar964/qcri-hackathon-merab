/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Kinetic Typography system, re-skinned into Fanar's palette.
        // (acid-yellow accent swapped for a luminous Fanar blue)
        bg: "#0A1730", // rich Fanar navy (the "rich black")
        surface: "#0E1E3D", // raised navy
        muted: "#14233F", // muted navy surface
        "muted-foreground": "#8FA3C0", // slate-blue secondary text
        foreground: "#FAFAFA", // off-white
        accent: "#4C8DFF", // luminous Fanar blue (the energy accent)
        "accent-foreground": "#04122B",
        maroon: "#A21C46", // Fanar maroon — secondary hot accent
        sand: "#E9E2D0", // warm Fanar sand
        line: "#24365C", // structural borders (navy-700)
      },
      fontFamily: {
        display: ["var(--font-space-grotesk)", "Inter", "system-ui", "sans-serif"],
        sans: ["var(--font-space-grotesk)", "Inter", "system-ui", "sans-serif"],
      },
      letterSpacing: {
        tightest: "-0.04em",
      },
      keyframes: {
        marquee: {
          "0%": { transform: "translateX(0)" },
          "100%": { transform: "translateX(-50%)" },
        },
        "fade-up": {
          from: { opacity: "0", transform: "translateY(10px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "slide-in": {
          from: { opacity: "0", transform: "translateX(10px)" },
          to: { opacity: "1", transform: "translateX(0)" },
        },
      },
      animation: {
        marquee: "marquee 28s linear infinite",
        "marquee-fast": "marquee 16s linear infinite",
        "fade-up": "fade-up 0.4s cubic-bezier(0.2,0.7,0.2,1) both",
        "slide-in": "slide-in 0.35s cubic-bezier(0.2,0.7,0.2,1) both",
      },
    },
  },
  plugins: [],
};
