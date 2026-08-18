module.exports = {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      fontFamily: {
        mono: ["JetBrains Mono", "SFMono-Regular", "Menlo", "monospace"],
        sans: ["Inter", "ui-sans-serif", "system-ui"],
      },
      colors: {
        obsidian: "#08080a",
        crimson: "#ff2a4b",
        wine: "#3d0c14",
        titanium: "#121316",
      },
    },
  },
  plugins: [],
};
