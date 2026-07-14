/**
 * RunFlow design tokens.
 *
 * Calm, editorial, data-first. The rules that keep it that way:
 *
 *   - ONE accent colour. Orange means "this is the thing to act on" and nothing
 *     else. The moment a second element competes for it, neither reads.
 *   - Colour is data, not decoration. Green/amber/red are reserved for verdicts
 *     (in-zone, caution, over-limit). Never use them to make a page look lively.
 *   - Hierarchy comes from SIZE and SPACE, not from borders and boxes. If a card
 *     needs an outline to feel separate, it needs more room instead.
 *   - Numbers are the hero. Set them large, tabular, and unadorned.
 */

export const color = {
  // Canvas — near-black, warm enough not to feel clinical.
  bg: '#0b0f14',
  surface: '#111820',
  surfaceRaised: '#161f2a',
  hairline: '#1e2936',

  // Text — a real ramp, so hierarchy needs no colour tricks.
  text: '#e8edf2',
  textSecondary: '#93a1b1',
  textMuted: '#5d6b7a',

  // The single accent.
  accent: '#ff5a1f',
  accentMuted: '#ff5a1f22',

  // Verdicts. These carry meaning — never use them decoratively.
  good: '#3ddc84',
  warn: '#f5a623',
  bad: '#ff4d4f',
  neutral: '#5d6b7a',
};

// Type scale (1.25 ratio, rounded to whole px).
export const font = {
  hero: { fontSize: '40px', fontWeight: 300, letterSpacing: '-0.02em', lineHeight: 1.1 },
  h1: { fontSize: '24px', fontWeight: 600, letterSpacing: '-0.01em', lineHeight: 1.25 },
  h2: { fontSize: '18px', fontWeight: 600, lineHeight: 1.3 },
  body: { fontSize: '14px', fontWeight: 400, lineHeight: 1.6 },
  small: { fontSize: '13px', fontWeight: 400, lineHeight: 1.5 },
  // Labels: the quiet uppercase kickers that let headings stay unshouty.
  label: {
    fontSize: '11px',
    fontWeight: 600,
    letterSpacing: '0.08em',
    textTransform: 'uppercase',
  },
  // Any figure the eye needs to compare down a column.
  numeric: { fontVariantNumeric: 'tabular-nums' },
};

// 4px rhythm. Everything on the page lands on this.
export const space = (n) => `${n * 4}px`;

export const radius = { sm: '6px', md: '10px', lg: '14px' };

// Cards carry no border by default — separation is done with space and surface.
export const card = {
  backgroundColor: color.surface,
  borderRadius: radius.lg,
  padding: space(6),
};

export const cardQuiet = {
  ...card,
  backgroundColor: 'transparent',
  padding: 0,
};

// Touch targets: 44px minimum, always.
export const button = {
  primary: {
    backgroundColor: color.accent,
    color: '#fff',
    border: 'none',
    borderRadius: radius.sm,
    padding: `${space(3)} ${space(5)}`,
    minHeight: '44px',
    fontSize: '14px',
    fontWeight: 600,
    cursor: 'pointer',
  },
  quiet: {
    background: 'none',
    color: color.textSecondary,
    border: 'none',
    padding: `${space(2)} 0`,
    minHeight: '44px',
    fontSize: '13px',
    fontWeight: 500,
    cursor: 'pointer',
  },
};
