module.exports = {
  content: [
    './apps/**/*.html',
    './assets/**/*.js',
    './assets/**/*.vue',
    './templates/**/*.html',
    './gpt_playground/settings.py',
  ],
  safelist: [
    'alert-success',
    'alert-info',
    'alert-error',
    'alert-warning',
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
}
