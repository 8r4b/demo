/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/**/*.{js,jsx,ts,tsx}",
    "./public/index.html",
  ],
  theme: {
    extend: {
      boxShadow: {
        '3xl': '0 35px 60px -15px rgba(0, 0, 0, 0.3)',
        'inner-lg': 'inset 0 2px 8px rgba(0,0,0,0.2)',
        'inner-sm': 'inset 0 1px 3px rgba(0,0,0,0.1)',
      },
      fontFamily: {
        inter: ['Inter', 'sans-serif'],
        montserrat: ['Montserrat', 'sans-serif'],
      },
      keyframes: {
        fadeIn: {
          'from': { opacity: '0', transform: 'translateY(20px)' },
          'to': { opacity: '1', transform: 'translateY(0)' },
        },
        pulse: {
          '0%, 100%': { transform: 'scale(0.9) rotate(-30deg)', opacity: '0.15' },
          '50%': { transform: 'scale(1.1) rotate(-30deg)', opacity: '0.25' },
        }
      },
      animation: {
        fadeIn: 'fadeIn 0.8s ease-out forwards',
        pulse: 'pulse 3s infinite alternate ease-in-out',
      },
    },
  },
  plugins: [],
}

