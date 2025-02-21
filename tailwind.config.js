module.exports = {
  // keep in sync with Dockerfile
  darkMode: ['selector', '[data-theme="dark"]'],
  content: [
    './apps/**/*.html',
    './assets/**/*.{js,jsx,ts,tsx,html}',
    './templates/**/*.html',
    './gpt_playground/settings.py',
  ],
  safelist: [
    'alert-success',
    'alert-info',
    'alert-error',
    'alert-warning',
    'alertify-notifier',
  ],
  theme: {
    extend: {
      aspectRatio: {
        '3/2': '3 / 2',
      },
    },
    container: {
      center: true,
      // padding: '2rem',
    },
  },
  variants: {
    extend: {},
  },
  plugins: [
    require('@tailwindcss/typography'),
    require("daisyui"),
  ],
  daisyui: {
    themes: ["light", "dark"],
  },
}
