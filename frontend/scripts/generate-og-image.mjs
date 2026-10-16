/**
 * Regenerate the link-preview image, public/og-image.png.
 *
 *     pnpm og-image
 *
 * Until 2026-10-16 every page's og:image was favicon-192.png, so a link to
 * Myntos pasted into Slack, Teams, LinkedIn or Messenger showed a 192px coin
 * with no words, cropped to whatever shape that client uses. Those cards are
 * 1200×630 (1.91:1); anything else is letterboxed or cut. This draws one at
 * that size, with the product's name and what it is, from the same logo paths
 * the favicons are made from, so the mark cannot drift from the one on the
 * site.
 *
 * One image for every page, deliberately. Each page's title and description
 * already travel in og:title and og:description, which every client prints
 * under the picture; a per-page image would only repeat them in a font the
 * client did not choose.
 *
 * The text is set in whatever sans-serif fontconfig finds on the machine that
 * runs this (Liberation Sans or DejaVu Sans on the Ubuntu box), not the site's
 * Segoe UI. The PNG is committed, so that is decided once, here, and never by
 * the build. Look at the result before committing it.
 */

import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import sharp from 'sharp';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const LOGO = join(ROOT, 'src', 'components', 'Logo.astro');
const OUT = join(ROOT, 'public', 'og-image.png');

const WIDTH = 1200;
const HEIGHT = 630;

/** The light theme's tokens from BaseLayout: --bg, --accent-soft, --fg, --muted, --accent, --gold, --logo-*. */
const C = {
  bg: '#ffffff',
  tint: '#e4f3ee',
  fg: '#0e1a17',
  muted: '#55665f',
  accent: '#0b7a61',
  gold: '#9a6c06',
  ring: '#1c1f1e',
  coin: '#0f8a68',
};

const FONT = "'Segoe UI', 'Helvetica Neue', Arial, 'Liberation Sans', 'DejaVu Sans', sans-serif";

/** The authored viewBox of the mark, as in generate-favicons.mjs. */
const VIEWBOX = { x: 371, y: 68, w: 650, h: 650 };

async function readPaths() {
  const source = await readFile(LOGO, 'utf8');
  const paths = [...source.matchAll(/ d="(M[^"]+)"/g)].map((match) => match[1]);
  if (paths.length !== 2) {
    throw new Error(`Expected 2 paths in Logo.astro, found ${paths.length}`);
  }
  return paths;
}

function buildSvg([ring, coin]) {
  const mark = 132;
  const scale = mark / VIEWBOX.w;
  const left = 96;

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}">
  <defs>
    <linearGradient id="wash" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="${C.tint}"/>
      <stop offset="0.7" stop-color="${C.bg}"/>
    </linearGradient>
    <linearGradient id="grad" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="${C.accent}"/>
      <stop offset="1" stop-color="${C.gold}"/>
    </linearGradient>
  </defs>
  <rect width="${WIDTH}" height="${HEIGHT}" fill="url(#wash)"/>
  <rect x="0" y="${HEIGHT - 10}" width="${WIDTH}" height="10" fill="url(#grad)"/>

  <g transform="translate(${left} 92) scale(${scale}) translate(${-VIEWBOX.x} ${-VIEWBOX.y})">
    <path d="${ring}" fill="${C.ring}"/>
    <path d="${coin}" fill="${C.coin}"/>
  </g>
  <text x="${left + mark + 28}" y="${92 + mark / 2 + 26}" font-family="${FONT}" font-size="76" font-weight="700" fill="${C.fg}">Myntos</text>

  <text font-family="${FONT}" font-weight="700" font-size="62" fill="${C.fg}">
    <tspan x="${left}" y="356">Norsk regnskapsprogram</tspan>
    <tspan x="${left}" y="432" fill="${C.accent}">med offentlig kildekode</tspan>
  </text>
  <text x="${left}" y="526" font-family="${FONT}" font-size="34" fill="${C.muted}">Bilag, faktura, mva og SAF-T. 0 kr.  ·  myntos.no</text>
</svg>
`;
}

const svg = buildSvg(await readPaths());
await sharp(Buffer.from(svg))
  .flatten({ background: C.bg })
  .png({ compressionLevel: 9 })
  .toFile(OUT);
console.log(`og-image.png: ${WIDTH}×${HEIGHT}`);
