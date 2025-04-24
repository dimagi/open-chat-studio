module.exports = {
  // keep in sync with Dockerfile
  content: [
    './apps/**/*.html',
    './assets/**/*.{js,jsx,ts,tsx,html}',
    './templates/**/*.html',
    './gpt_playground/settings.py',
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
}
