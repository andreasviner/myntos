/**
 * Regenerate the favicon set from the logo component.
 *
 *     pnpm favicons
 *
 * The mark lives in one place, src/components/Logo.astro, and this script
 * reads its paths from there, so the icons cannot drift from the logo on the
 * site. Run it whenever the mark or the icon colours change.
 *
 * Two things are worth knowing before editing the numbers below:
 *
 * 1. **The authored viewBox is not the ink.** `371 68 650 650` carries slack on
 *    every side, so scaling by the viewBox leaves the mark floating in the
 *    middle of the tile. The real bounds are measured here by rasterising the
 *    paths and trimming, rather than guessed.
 * 2. **The mark is 4% wider than it is tall.** Filling the box exactly would
 *    push the width past it and flatten the outer edge of the ring, so the
 *    width goes edge to edge instead and the leftover 4% becomes a hair of
 *    padding above and below.
 *
 * Google Search requires a favicon that is square and a multiple of 48px, which
 * is what SIZES holds.
 *
 * The two formats are deliberately different. The SVG is transparent and
 * inverts with the browser chrome, so it never punches a tile into whatever
 * surface it lands on. The PNGs are opaque, because the surfaces that take a
 * PNG composite it themselves: Google Search draws the icon on its own light
 * card, and iOS composites black behind a transparent apple-touch-icon. A
 * transparent mark leaves those consumers to fill the space around the ink,
 * and they fill it with padding, which is what makes the logo read as small.
 * BACKGROUND takes that decision back: the tile is ours, so the mark occupies
 * it fully.
 *
 * Do not add padding here. The ink goes edge to edge on purpose, and the tile
 * is what absorbs whatever the consumer does to the corners. The one cost is
 * measured: against a circular mask the M's upper-right tip reaches 8.5% of the
 * tile past the inscribed circle, so 2.3% of the ink can clip. Insetting to
 * save it shrinks the mark everywhere, which is the problem it was meant to
 * solve.
 */

import { readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import sharp from 'sharp';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const PUBLIC_DIR = join(ROOT, 'public');
const LOGO = join(ROOT, 'src', 'components', 'Logo.astro');

/** The authored viewBox of the mark. */
const VIEWBOX = { x: 371, y: 68, w: 650, h: 650 };

/** Icon canvas in SVG user units. */
const CANVAS = 32;

/** Multiples of 48, per Google Search's favicon requirement. */
const SIZES = [48, 96, 144, 192];

/**
 * The raster tile, matching --surface and the theme-color meta in BaseLayout,
 * so the icon reads as one piece with the site rather than as a floating mark.
 */
const BACKGROUND = '#ffffff';

/**
 * Two colourways, because the icon sits on a surface it cannot see.
 *
 * These are the --logo-ring / --logo-coin tokens from BaseLayout, and the SVG
 * carries both behind a prefers-color-scheme query, so it inverts with the
 * browser chrome exactly as the mark inverts on the page.
 *
 * A PNG has no such escape hatch, but it does not need one: it brings its own
 * BACKGROUND, so the `light` pair is on the surface it was picked for.
 */
const THEMES = {
  light: { ring: '#1c1f1e', coin: '#0f8a68' },
  dark: { ring: '#eceeed', coin: '#5ee0b4' },
};

/** Resolution used only for measuring; higher just means a finer bound. */
const PROBE = 2000;

async function readPaths() {
  const source = await readFile(LOGO, 'utf8');
  const paths = [...source.matchAll(/ d="(M[^"]+)"/g)].map((match) => match[1]);
  if (paths.length !== 2) {
    throw new Error(`Expected 2 paths in Logo.astro, found ${paths.length}`);
  }
  return paths;
}

/** Measure where the ink actually is, in the authored viewBox's units. */
async function measure(paths) {
  const probe = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${VIEWBOX.x} ${VIEWBOX.y} ${VIEWBOX.w} ${VIEWBOX.h}" width="${PROBE}" height="${PROBE}">${paths
    .map((d) => `<path d="${d}" fill="#000"/>`)
    .join('')}</svg>`;

  const { info } = await sharp(Buffer.from(probe))
    .trim({ threshold: 0 })
    .toBuffer({ resolveWithObject: true });

  const perUnit = PROBE / VIEWBOX.w;
  return {
    x: VIEWBOX.x + -info.trimOffsetLeft / perUnit,
    y: VIEWBOX.y + -info.trimOffsetTop / perUnit,
    w: info.width / perUnit,
    h: info.height / perUnit,
  };
}

function buildSvg(paths, ink, { adaptive, background = null }) {
  // Contain, not cover: scale by the larger dimension so nothing is clipped.
  const scale = CANVAS / Math.max(ink.w, ink.h);
  const round = (value) => Number(value.toFixed(4));
  const tx = round(-ink.x * scale + (CANVAS - ink.w * scale) / 2);
  const ty = round(-ink.y * scale + (CANVAS - ink.h * scale) / 2);

  // The fill attributes are not redundant with the stylesheet: they are what a
  // renderer that ignores the CSS falls back to, and Safari is one of those,
  // since it takes the SVG but not the media query, so it must land on `base`.
  const base = THEMES.light;
  const style = adaptive
    ? `
  <style>
    .ring { fill: ${THEMES.light.ring} }
    .coin { fill: ${THEMES.light.coin} }
    @media (prefers-color-scheme: dark) {
      .ring { fill: ${THEMES.dark.ring} }
      .coin { fill: ${THEMES.dark.coin} }
    }
  </style>`
    : '';
  const tile = background
    ? `\n  <rect width="${CANVAS}" height="${CANVAS}" fill="${background}"/>`
    : '';

  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${CANVAS} ${CANVAS}">
  <!-- Generated by scripts/generate-favicons.mjs. Edit the logo, not this file. -->${style}${tile}
  <g transform="matrix(${round(scale)} 0 0 ${round(scale)} ${tx} ${ty})">
    <path class="ring" d="${paths[0]}" fill="${base.ring}"/>
    <path class="coin" d="${paths[1]}" fill="${base.coin}"/>
  </g>
</svg>
`;
}

const paths = await readPaths();
const ink = await measure(paths);
const svg = buildSvg(paths, ink, { adaptive: true });
// Rasterised separately: sharp renders with no colour-scheme preference, so
// feeding it the adaptive SVG would silently bake the media query's absence in
// as a decision, and the raster set wants the tile besides.
const pngSvg = buildSvg(paths, ink, { adaptive: false, background: BACKGROUND });

await writeFile(join(PUBLIC_DIR, 'favicon.svg'), svg, 'utf8');
console.log(
  `favicon.svg: ink ${ink.w.toFixed(1)}×${ink.h.toFixed(1)} of viewBox ${VIEWBOX.w}, ` +
    `aspect ${(ink.w / ink.h).toFixed(3)}`,
);

for (const size of SIZES) {
  const file = join(PUBLIC_DIR, `favicon-${size}.png`);
  // density scales the SVG rasteriser so the render is sharp rather than an
  // upscaled 32px bitmap. flatten is a belt-and-braces pass over the tile the
  // SVG already paints, so the file can never ship an alpha channel.
  await sharp(Buffer.from(pngSvg), { density: (72 * size) / CANVAS })
    .resize(size, size, { background: BACKGROUND })
    .flatten({ background: BACKGROUND })
    .png({ compressionLevel: 9 })
    .toFile(file);
  console.log(`favicon-${size}.png`);
}
