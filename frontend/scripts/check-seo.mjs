/**
 * Check what the build hands a search engine, after `pnpm build`.
 *
 *     pnpm build && pnpm check:seo
 *
 * Reads dist/client the way a crawler that does not run JavaScript reads it,
 * which is how most AI search crawlers read a page. Each rule below is here
 * because the site once broke it without anything else noticing:
 *
 * - The front page shipped the signed-in overview in the same HTML as the
 *   description of the product, hidden only by CSS. A crawler that reads the
 *   text saw two h1s («Myntos: …» and «Oversikt ubekreftet»), «Hei, der» and
 *   «Henter oversikten …» in the middle of the pitch, until 2026-10-22.
 * - The sitemap offered Google eighteen login redirects, because the list that
 *   kept application pages out was written before most of them existed
 *   (lib/indexing.ts has that story). Here the sitemap is held to the robots
 *   meta the pages actually carry.
 * - The front page's title was the bare name, so it described nothing.
 *
 * Rules for every indexable page (one without `noindex`): one non-empty title
 * and description, both unique across the site, one h1 outside a <template>,
 * a canonical that is the page's own https://myntos.no address, Open Graph
 * that agrees with it, parseable JSON-LD, a place in the sitemap, and internal
 * links (anchors included) that lead somewhere. A noindex page has no
 * canonical and is not in the sitemap. robots.txt refuses nothing.
 *
 * No dependencies: regular expressions over HTML Astro wrote itself, which is
 * regular enough. Exits 1 and lists every failure.
 */

import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const SITE = 'https://myntos.no';
const DIST = join(dirname(fileURLToPath(import.meta.url)), '..', 'dist', 'client');

/** Google cuts a title at roughly 60 characters and a description at roughly 160. */
const MAX_TITLE = 70;
const MAX_DESCRIPTION = 170;

/** Text the signed-in overview carries, which must never reach a crawler. */
const DASHBOARD_TEXT = ['Henter oversikten', 'Siste bevegelser', 'Ingen regnskap ennå', 'Hei, der'];

const failures = [];
const fail = (where, message) => failures.push(`${where}: ${message}`);

if (!existsSync(DIST)) {
  console.error(`${DIST} does not exist. Run pnpm build first.`);
  process.exit(1);
}

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

/** dist/client/kom-i-gang/fiken/index.html → /kom-i-gang/fiken/ */
function urlPathOf(file) {
  const rel = relative(DIST, file).split(sep).join('/');
  if (rel === 'index.html') return '/';
  if (rel.endsWith('/index.html')) return `/${rel.slice(0, -'index.html'.length)}`;
  return `/${rel}`;
}

const decode = (text) =>
  text
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&#x27;/g, "'");

function attr(tag, name) {
  const match = tag.match(new RegExp(`\\s${name}="([^"]*)"`));
  return match === null ? null : decode(match[1]);
}

function metas(html, key, value) {
  return [...html.matchAll(/<meta\b[^>]*>/g)].map((m) => m[0]).filter((tag) => attr(tag, key) === value);
}

/** The document as a crawler that ignores <template>, <script> and <style> sees it. */
function visibleMarkup(html) {
  return html
    .replace(/<template\b[\s\S]*?<\/template>/g, '')
    .replace(/<script\b[\s\S]*?<\/script>/g, '')
    .replace(/<style\b[\s\S]*?<\/style>/g, '');
}

const files = walk(DIST);
const htmlFiles = files.filter((file) => file.endsWith('.html') && !/google[0-9a-f]+\.html$/.test(file));
const filePaths = new Set(files.map(urlPathOf));

const pages = htmlFiles.map((file) => {
  const html = readFileSync(file, 'utf8');
  const path = urlPathOf(file);
  const robots = metas(html, 'name', 'robots').map((tag) => attr(tag, 'content') ?? '');
  return {
    path,
    html,
    indexable: !robots.some((content) => content.includes('noindex')),
    titles: [...html.matchAll(/<title>([\s\S]*?)<\/title>/g)].map((m) => decode(m[1]).trim()),
    descriptions: metas(html, 'name', 'description').map((tag) => attr(tag, 'content') ?? ''),
    canonicals: [...html.matchAll(/<link\b[^>]*rel="canonical"[^>]*>/g)].map((m) => attr(m[0], 'href')),
    ids: new Set([...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1])),
  };
});
const byPath = new Map(pages.map((page) => [page.path, page]));

// --- The sitemap ------------------------------------------------------------

const sitemapUrls = new Set(
  files
    .filter((file) => /sitemap-\d+\.xml$/.test(file))
    .flatMap((file) => [...readFileSync(file, 'utf8').matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1])),
);
if (sitemapUrls.size === 0) fail('sitemap', 'no addresses in sitemap-*.xml');
for (const url of sitemapUrls) {
  if (!url.startsWith(`${SITE}/`)) {
    fail('sitemap', `${url} is not on ${SITE}`);
    continue;
  }
  const page = byPath.get(url.slice(SITE.length));
  if (page === undefined) fail('sitemap', `${url} is not in the build`);
  else if (!page.indexable) fail('sitemap', `${url} is noindex but listed`);
}

// --- Every page -------------------------------------------------------------

const seenTitles = new Map();
const seenDescriptions = new Map();

for (const page of pages) {
  const where = page.path;
  if (page.titles.length !== 1 || page.titles[0] === '') fail(where, `${page.titles.length} non-empty <title>, expected one`);
  if (page.descriptions.length !== 1 || page.descriptions[0] === '') {
    fail(where, `${page.descriptions.length} meta descriptions, expected one`);
  }

  if (!page.indexable) {
    if (page.canonicals.length > 0) fail(where, 'noindex page with a canonical');
    continue;
  }

  const title = page.titles[0] ?? '';
  const description = page.descriptions[0] ?? '';
  if (title.length > MAX_TITLE) fail(where, `title is ${title.length} characters (max ${MAX_TITLE}): «${title}»`);
  if (description.length > MAX_DESCRIPTION) {
    fail(where, `description is ${description.length} characters (max ${MAX_DESCRIPTION})`);
  }
  if (seenTitles.has(title)) fail(where, `same title as ${seenTitles.get(title)}: «${title}»`);
  seenTitles.set(title, where);
  if (seenDescriptions.has(description)) fail(where, `same description as ${seenDescriptions.get(description)}`);
  seenDescriptions.set(description, where);

  const expected = `${SITE}${page.path}`;
  if (page.canonicals.length !== 1 || page.canonicals[0] !== expected) {
    fail(where, `canonical is ${JSON.stringify(page.canonicals)}, expected ${expected}`);
  }
  const ogUrl = metas(page.html, 'property', 'og:url').map((tag) => attr(tag, 'content'));
  if (ogUrl.length !== 1 || ogUrl[0] !== expected) fail(where, `og:url is ${JSON.stringify(ogUrl)}`);
  const ogTitle = metas(page.html, 'property', 'og:title').map((tag) => attr(tag, 'content'));
  if (ogTitle.length !== 1 || ogTitle[0] !== title) fail(where, 'og:title is not the page title');

  if (!sitemapUrls.has(expected)) fail(where, 'indexable but missing from the sitemap');

  const visible = visibleMarkup(page.html);
  const h1s = visible.match(/<h1\b/g)?.length ?? 0;
  if (h1s !== 1) fail(where, `${h1s} <h1> outside <template>, expected one`);

  for (const match of page.html.matchAll(/<script type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/g)) {
    try {
      const data = JSON.parse(match[1]);
      if (!Array.isArray(data['@graph']) || data['@graph'].length === 0) fail(where, 'JSON-LD without @graph');
    } catch (error) {
      fail(where, `JSON-LD does not parse: ${error.message}`);
    }
  }

  for (const match of visible.matchAll(/<a\b[^>]*\shref="([^"]+)"/g)) {
    const href = decode(match[1]);
    if (!href.startsWith('/') || href.startsWith('//')) continue;
    const [pathAndQuery, fragment] = href.split('#');
    const path = pathAndQuery.split('?')[0];
    const target = byPath.get(path);
    if (target === undefined && !filePaths.has(path)) {
      fail(where, `links to ${href}, which does not exist`);
    } else if (target !== undefined && fragment && !target.ids.has(fragment)) {
      fail(where, `links to ${href}, but ${path} has no id="${fragment}"`);
    }
  }
}

// --- The front page ---------------------------------------------------------

const home = byPath.get('/');
if (home === undefined) {
  fail('/', 'no front page');
} else {
  const text = visibleMarkup(home.html).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
  for (const phrase of DASHBOARD_TEXT) {
    if (text.includes(phrase)) fail('/', `«${phrase}», from the signed-in overview, is in the HTML outside <template>`);
  }
  if (!/<section\b[^>]*id="dashboard"[^>]*><\/section>/.test(home.html)) {
    fail('/', '<section id="dashboard"> must ship empty');
  }
}

if (!byPath.get('/404.html')?.html.includes('noindex')) fail('/404.html', 'missing, or not noindex');

// --- robots.txt -------------------------------------------------------------

const robots = readFileSync(join(DIST, 'robots.txt'), 'utf8');
if (!robots.includes(`Sitemap: ${SITE}/sitemap-index.xml`)) fail('robots.txt', 'does not name sitemap-index.xml');
for (const line of robots.split('\n')) {
  if (/^\s*Disallow:\s*\S/i.test(line)) fail('robots.txt', `refuses something: «${line.trim()}» (see the comment in the file)`);
}

// --- Result -----------------------------------------------------------------

const indexed = pages.filter((page) => page.indexable).length;
if (failures.length > 0) {
  console.error(`${failures.length} problems in what search engines are given:\n`);
  for (const failure of failures) console.error(`  ${failure}`);
  process.exit(1);
}
console.log(`SEO: ${indexed} indexable pages and ${pages.length - indexed} noindex, no problems.`);
