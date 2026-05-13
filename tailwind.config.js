/** @type {import('tailwindcss').Config} */
module.exports = {
  prefix: 'tw-',
  content: [
    './templates/**/*.html',
    './classroom_app/**/*.py',
    './static/js/**/*.js',
    './static/css/ui-system.src.css',
    './node_modules/flowbite/**/*.js',
  ],
  blocklist: ['[content_start:end_index]'],
  corePlugins: {
    preflight: false,
  },
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#eef2ff',
          100: '#e0e7ff',
          500: '#4f46e5',
          600: '#4338ca',
          700: '#3730a3',
        },
        ocean: {
          50: '#ecfeff',
          500: '#0891b2',
          600: '#0e7490',
        },
      },
      fontFamily: {
        sans: [
          'Segoe UI',
          'Microsoft YaHei UI',
          'Microsoft YaHei',
          'PingFang SC',
          'Hiragino Sans GB',
          'Noto Sans CJK SC',
          'WenQuanYi Micro Hei',
          'system-ui',
          'sans-serif',
        ],
      },
      boxShadow: {
        'soft-sm': '0 8px 24px -18px rgba(15, 23, 42, 0.45), 0 3px 10px -8px rgba(15, 23, 42, 0.28)',
        'soft-md': '0 18px 40px -24px rgba(15, 23, 42, 0.5), 0 8px 22px -16px rgba(15, 23, 42, 0.3)',
        'soft-lg': '0 28px 68px -36px rgba(15, 23, 42, 0.58), 0 16px 36px -24px rgba(15, 23, 42, 0.34)',
      },
    },
  },
  plugins: [
    require('flowbite/plugin'),
    require('daisyui'),
  ],
  daisyui: {
    logs: false,
    base: false,
    prefix: 'dui-',
    themes: [
      {
        lanshare: {
          primary: '#4f46e5',
          secondary: '#0891b2',
          accent: '#10b981',
          neutral: '#0f172a',
          'base-100': '#ffffff',
          'base-200': '#f1f5f9',
          'base-300': '#e2e8f0',
          info: '#0ea5e9',
          success: '#10b981',
          warning: '#f59e0b',
          error: '#ef4444',
          '--rounded-box': '0.7rem',
          '--rounded-btn': '0.5rem',
          '--rounded-badge': '9999px',
          '--animation-btn': '0.18s',
          '--animation-input': '0.18s',
          '--btn-focus-scale': '0.99',
          '--border-btn': '1px',
          '--tab-border': '1px',
          '--tab-radius': '0.5rem',
        },
      },
    ],
  },
};
