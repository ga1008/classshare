/** @type {import('tailwindcss').Config} */
module.exports = {
  prefix: 'tw-',
  content: [
    './templates/**/*.html',
    './classroom_app/**/*.py',
    './frontend/src/**/*.{ts,tsx}',
    './static/js/**/*.js',
    './static/css/ui-system.src.css',
  ],
  blocklist: ['[content_start:end_index]', '[a-z0-9_:-]'],
  corePlugins: {
    preflight: false,
  },
  theme: {
    extend: {
      colors: {
        border: 'hsl(var(--ls-border) / <alpha-value>)',
        input: 'hsl(var(--ls-input) / <alpha-value>)',
        ring: 'hsl(var(--ls-ring) / <alpha-value>)',
        background: 'hsl(var(--ls-background) / <alpha-value>)',
        foreground: 'hsl(var(--ls-foreground) / <alpha-value>)',
        primary: {
          DEFAULT: 'hsl(var(--ls-primary) / <alpha-value>)',
          foreground: 'hsl(var(--ls-primary-foreground) / <alpha-value>)',
        },
        secondary: {
          DEFAULT: 'hsl(var(--ls-secondary) / <alpha-value>)',
          foreground: 'hsl(var(--ls-secondary-foreground) / <alpha-value>)',
        },
        destructive: {
          DEFAULT: 'hsl(var(--ls-destructive) / <alpha-value>)',
          foreground: 'hsl(var(--ls-destructive-foreground) / <alpha-value>)',
        },
        muted: {
          DEFAULT: 'hsl(var(--ls-muted) / <alpha-value>)',
          foreground: 'hsl(var(--ls-muted-foreground) / <alpha-value>)',
        },
        accent: {
          DEFAULT: 'hsl(var(--ls-accent) / <alpha-value>)',
          foreground: 'hsl(var(--ls-accent-foreground) / <alpha-value>)',
        },
        popover: {
          DEFAULT: 'hsl(var(--ls-popover) / <alpha-value>)',
          foreground: 'hsl(var(--ls-popover-foreground) / <alpha-value>)',
        },
        card: {
          DEFAULT: 'hsl(var(--ls-card) / <alpha-value>)',
          foreground: 'hsl(var(--ls-card-foreground) / <alpha-value>)',
        },
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
      borderRadius: {
        lg: 'var(--ls-radius)',
        md: 'calc(var(--ls-radius) - 2px)',
        sm: 'calc(var(--ls-radius) - 4px)',
      },
      boxShadow: {
        'soft-sm': '0 8px 24px -18px rgba(15, 23, 42, 0.45), 0 3px 10px -8px rgba(15, 23, 42, 0.28)',
        'soft-md': '0 18px 40px -24px rgba(15, 23, 42, 0.5), 0 8px 22px -16px rgba(15, 23, 42, 0.3)',
        'soft-lg': '0 28px 68px -36px rgba(15, 23, 42, 0.58), 0 16px 36px -24px rgba(15, 23, 42, 0.34)',
      },
    },
  },
  plugins: [
    require('tailwindcss-animate'),
  ],
};
