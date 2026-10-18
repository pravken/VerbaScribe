/** Build-time Tailwind config (previously inlined for the CDN runtime). Run `npm run vendor`. */
module.exports = {
  content: ['./static/index.html'],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Inter Variable"', '"Segoe UI Variable Text"', '"Segoe UI"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono Variable"', '"Cascadia Mono"', 'Consolas', 'monospace'],
      },
      colors: {
        apple: {
          bg: '#141416',
          sidebar: '#1C1C1E',
          card: '#242426',
          elevated: '#2C2C2E',
          border: 'rgba(255, 255, 255, 0.08)',
          blue: '#0A84FF',
          blueHover: '#0070E0',
          red: '#FF453A',
          green: '#30D158',
          amber: '#FF9F0A',
          label: '#F5F5F7',
          secondary: '#98989D',
          tertiary: '#636366',
        },
      },
    },
  },
};
