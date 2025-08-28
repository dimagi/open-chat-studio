/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./src/**/*.{tsx,ts,html,css}",
    "./index.html"
  ],
  plugins: [
    require('@tailwindcss/typography'),
    // Add default utilities plugin for v4 compatibility
    function({ matchUtilities, theme, addUtilities }) {
      // Add essential utilities that are missing in v4
      addUtilities({
        '.rounded-lg': { 'border-radius': '0.5rem' },
        '.rounded-md': { 'border-radius': '0.375rem' },
        '.rounded-full': { 'border-radius': '9999px' },
        '.shadow-lg': { 'box-shadow': '0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1)' },
        '.shadow-2xl': { 'box-shadow': '0 25px 50px -12px rgb(0 0 0 / 0.25)' },
        '.border-0': { 'border-width': '0px' },
        '.transition-all': { 'transition-property': 'all' },
        '.duration-200': { 'transition-duration': '200ms' },
        '.ease-in-out': { 'transition-timing-function': 'cubic-bezier(0.4, 0, 0.2, 1)' },
        '.transform': { 'transform': 'translate(var(--tw-translate-x), var(--tw-translate-y)) rotate(var(--tw-rotate)) skewX(var(--tw-skew-x)) skewY(var(--tw-skew-y)) scaleX(var(--tw-scale-x)) scaleY(var(--tw-scale-y))' },
        '.hover\\:scale-105:hover': { 'transform': 'scale(1.05)' },
        '.flex': { 'display': 'flex' },
        '.gap-\\[8px\\]': { 'gap': '8px' },
        '.items-center': { 'align-items': 'center' },
        '.font-medium': { 'font-weight': '500' },
        '.whitespace-nowrap': { 'white-space': 'nowrap' },
        '.object-contain': { 'object-fit': 'contain' },
        '.flex-shrink-0': { 'flex-shrink': '0' },
        '.text-left': { 'text-align': 'left' }
      });
    }
  ]
}