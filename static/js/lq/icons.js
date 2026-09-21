import { ICON_NODES, ICON_ALIASES } from './icons.generated.js';
import { attributesMarkup } from './html.js';

const svgAttributes = Object.freeze({ class: 'lq-icon', xmlns: 'http://www.w3.org/2000/svg', viewBox: '0 0 24 24', fill: 'none',
  stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
  'aria-hidden': 'true', focusable: 'false' });

export function resolveIcon(name) {
  const key = Object.hasOwn(ICON_ALIASES, name) ? ICON_ALIASES[name] : name;
  return Object.hasOwn(ICON_NODES, key) ? key : 'circle-question-mark';
}

export function iconMarkup(name) {
  return `<svg${attributesMarkup(svgAttributes)}>${ICON_NODES[resolveIcon(name)].map(([tag, attrs]) =>
    `<${tag}${attributesMarkup(attrs)}></${tag}>`).join('')}</svg>`;
}

export function createIcon(name, doc = document) {
  const svg = doc.createElementNS('http://www.w3.org/2000/svg', 'svg');
  for (const [key, value] of Object.entries(svgAttributes)) svg.setAttribute(key, value);
  for (const [tag, attrs] of ICON_NODES[resolveIcon(name)]) {
    const node = doc.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    svg.append(node);
  }
  return svg;
}
