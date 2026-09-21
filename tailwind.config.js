/** @type {import('tailwindcss').Config} */
module.exports = {
  prefix: 'tw-',
  darkMode: ['selector', '[data-appearance="dark"]'],
  content: [
    './templates/**/*.html',
    './classroom_app/**/*.py',
    './frontend/src/**/*.{ts,tsx}',
    './static/js/**/*.js',
    './static/css/ui-system.src.css',
    './static/css/lq/**/*.css',
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
          soft: 'hsl(var(--ls-primary-soft))',
          'on-soft': 'hsl(var(--ls-on-primary-soft) / <alpha-value>)',
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
        success: 'hsl(var(--ls-success) / <alpha-value>)',
        warning: 'hsl(var(--ls-warning) / <alpha-value>)',
        info: 'hsl(var(--ls-info) / <alpha-value>)',
        surface: Object.fromEntries([0, 1, 2].map(level => [level, `hsl(var(--ls-surface-${level}) / <alpha-value>)`])),
        ink: {
          DEFAULT: 'hsl(var(--ls-ink) / <alpha-value>)',
          2: 'hsl(var(--ls-ink-2) / <alpha-value>)',
          3: 'hsl(var(--ls-ink-3) / <alpha-value>)',
        },
        tone: Object.fromEntries(['success', 'warning', 'danger', 'info', 'neutral'].map(tone => [tone,
          Object.fromEntries(['base', 'fg', 'on-base', 'solid', 'on-solid', 'soft'].map(level => [level,
            level === 'soft' ? `hsl(var(--ls-tone-${tone}-soft))` : `hsl(var(--ls-tone-${tone}-${level}) / <alpha-value>)`,
          ])),
        ])),
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
          'var(--font-family-sans)',
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
        lq: ['var(--ls-font-sans)'],
      },
      borderRadius: {
        lg: 'var(--ls-radius)',
        md: 'calc(var(--ls-radius) - 2px)',
        sm: 'calc(var(--ls-radius) - 4px)',
        ...Object.fromEntries(['xs', 'sm', 'md', 'lg', 'xl', '2xl', 'capsule'].map(size => [`lq-${size}`, `var(--ls-r-${size})`])),
      },
      boxShadow: {
        ...Object.fromEntries([1, 2, 3, 4, 'focus'].map(level => [`lq-${level}`, `var(--ls-shadow-${level})`])),
        'soft-sm': '0 8px 24px -18px rgba(15, 23, 42, 0.45), 0 3px 10px -8px rgba(15, 23, 42, 0.28)',
        'soft-md': '0 18px 40px -24px rgba(15, 23, 42, 0.5), 0 8px 22px -16px rgba(15, 23, 42, 0.3)',
        'soft-lg': '0 28px 68px -36px rgba(15, 23, 42, 0.58), 0 16px 36px -24px rgba(15, 23, 42, 0.34)',
      },
      spacing: Object.fromEntries([1, 2, 3, 4, 5, 6, 8, 10, 12, 16].map(size => [`lq-${size}`, `var(--ls-s-${size})`])),
      fontSize: Object.fromEntries(['display', 'title1', 'title2', 'title3', 'headline', 'body', 'callout', 'sub', 'footnote', 'caption'].map(size => [`lq-${size}`,
        [`var(--ls-t-${size})`, {lineHeight: `var(--ls-leading-${size})`, fontWeight: `var(--ls-weight-${size})`}],
      ])),
      zIndex: Object.fromEntries(['raised', 'nav', 'popover', 'drawer', 'modal', 'viewer', 'toast', 'explain'].map(level => [`lq-${level}`, `var(--ls-z-${level})`])),
    },
  },
  plugins: [
    require('tailwindcss-animate'),
  ],
};
