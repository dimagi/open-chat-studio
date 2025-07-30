/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./src/**/*.{ts,tsx,html}'],
  theme: {
    extend: {
      animation: {
        progress: 'progress 3s infinite linear',
        dots: 'dots 1s steps(5, end) infinite',
      },
      keyframes: {
        progress: {
          '0%': {transform: ' translateX(0) scaleX(0)'},
          '10%': {transform: 'translateX(0) scaleX(0.3)'},
          '50%': {transform: 'translateX(100%) scaleX(0.3)'},
          '90%': {transform: 'translateX(0) scaleX(0.3)'},
          '100%': {transform: 'translateX(0) scaleX(0)'},
        },
        dots: {
          '0%, 20%': {
            color: 'rgba(0,0,0,0)',
            textShadow: '.25em 0 0 rgba(0,0,0,0), .5em 0 0 rgba(0,0,0,0)',
          },
          '40%': {
            color: 'black',
            textShadow: '.25em 0 0 rgba(0,0,0,0), .5em 0 0 rgba(0,0,0,0)',
          },
          '60%': {
            textShadow: '.25em 0 0 black, .5em 0 0 rgba(0,0,0,0)',
          },
          '80%, 100%': {
            textShadow: '.25em 0 0 black, .5em 0 0 black',
          },
        },
      },
      transformOrigin: {
        'left-right': '0% 50%',
      }
    }
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
}
