#!/usr/bin/env node
/**
 * scale.mjs - print fluid type and space tokens as CSS custom properties.
 *
 * Type sizes grow smoothly from a small screen to a large one with clamp(), and
 * the ratio between steps can grow too, so headings get more dramatic on big
 * screens without being huge on phones.
 *
 *   node scale.mjs [--min-base 16] [--max-base 18] [--min-ratio 1.2] [--max-ratio 1.333]
 *                  [--min-vw 360] [--max-vw 1440] [--steps -2,6]
 *
 * Common ratios: 1.125 major second (dense UI), 1.2 minor third, 1.25 major third,
 * 1.333 perfect fourth, 1.414 augmented fourth, 1.5 perfect fifth (dramatic editorial).
 * Paste the output into :root and use var(--step-3), var(--space-m), ...
 */
const a = process.argv.slice(2);
const get = (k, d) => { const i = a.indexOf(k); return i >= 0 ? Number(a[i + 1]) : d; };
if (a.includes('-h') || a.includes('--help')) {
  console.log('usage: node scale.mjs [--min-base 16] [--max-base 18] [--min-ratio 1.2] [--max-ratio 1.333] [--min-vw 360] [--max-vw 1440] [--steps -2,6]');
  process.exit(0);
}
const minBase = get('--min-base', 16), maxBase = get('--max-base', 18);
const minRatio = get('--min-ratio', 1.2), maxRatio = get('--max-ratio', 1.333);
const minVw = get('--min-vw', 360), maxVw = get('--max-vw', 1440);
const si = a.indexOf('--steps');
const [lo, hi] = si >= 0 ? a[si + 1].split(',').map(Number) : [-2, 6];

const r = (n, p = 4) => +n.toFixed(p);
// A value that is `min` px at minVw and `max` px at maxVw, linear in between.
function fluid(min, max) {
  const slope = (max - min) / (maxVw - minVw);
  const intercept = min - slope * minVw;
  const lower = Math.min(min, max), upper = Math.max(min, max);
  if (Math.abs(max - min) < 0.05) return `${r(min / 16)}rem`;
  const sign = slope < 0 ? '-' : '+';
  return `clamp(${r(lower / 16)}rem, ${r(intercept / 16)}rem ${sign} ${r(Math.abs(slope) * 100)}vw, ${r(upper / 16)}rem)`;
}

const out = [];
out.push(':root {');
out.push(`  /* Type: ${minBase}px @${minVw}px -> ${maxBase}px @${maxVw}px, ratio ${minRatio} -> ${maxRatio} */`);
for (let s = hi; s >= lo; s--) {
  const min = minBase * minRatio ** s, max = maxBase * maxRatio ** s;
  out.push(`  --step-${s < 0 ? '-' + Math.abs(s) : s}: ${fluid(min, max)}; /* ${r(min, 1)}px -> ${r(max, 1)}px */`);
}
out.push('');
out.push('  /* Space: multiples of the base size, fluid like the type */');
const space = [['3xs', 0.25], ['2xs', 0.5], ['xs', 0.75], ['s', 1], ['m', 1.5], ['l', 2], ['xl', 3], ['2xl', 4], ['3xl', 6], ['4xl', 8]];
for (const [name, m] of space) out.push(`  --space-${name}: ${fluid(minBase * m, maxBase * m)}; /* ${r(minBase * m, 1)}px -> ${r(maxBase * m, 1)}px */`);
out.push('');
out.push('  /* One-up pairs: grow faster than either end, good for section padding */');
for (const [x, y] of [['s', 'l'], ['m', 'xl'], ['l', '2xl'], ['xl', '3xl'], ['2xl', '4xl']]) {
  const mx = space.find((p) => p[0] === x)[1], my = space.find((p) => p[0] === y)[1];
  out.push(`  --space-${x}-${y}: ${fluid(minBase * mx, maxBase * my)};`);
}
out.push('}');
console.log(out.join('\n'));
