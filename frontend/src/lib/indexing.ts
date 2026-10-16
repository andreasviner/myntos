/**
 * Which pages a search engine should index, decided in one place.
 *
 * Most of the site is the application. Those pages send a signed-out visitor
 * to /logg-inn/, and Google runs the script before it indexes, so to a crawler
 * each of them is a redirect to the login page with a paragraph in front of
 * it. A handful more are the far end of a link Myntos mails (address
 * confirmation, a password reset, an invitation) and say nothing without the
 * token in the fragment. astro.config.mjs always meant to keep both out of the
 * sitemap, but it did so with a hand list of the five application pages that
 * existed then, and every page added after them went in by default: on
 * 2026-10-16 the sitemap offered Google eighteen login redirects and four
 * token pages beside the pages that say what Myntos is.
 *
 * So the list runs the other way. A page is indexed only when it is named
 * here, and a new page stays out until somebody decides it is public.
 * Forgetting a public page costs a page nobody finds, which shows in Search
 * Console; forgetting an application page put login screens in the index,
 * which shows nowhere.
 *
 * Read by BaseLayout (the robots meta on every page) and by astro.config.mjs
 * (the sitemap filter), so the two cannot disagree. `indexing.test.ts` fails
 * if a page that redirects to the login is ever listed here.
 */

/** Pages a signed-out visitor can read in full. Paths end in a slash, as the site serves them. */
export const PUBLIC_PAGES: readonly string[] = [
  '/',
  '/about/',
  '/funksjoner/',
  '/priser/',
  '/bankpriser/',
  '/ki/',
  '/kom-i-gang/',
  '/personvern/',
  '/vilkar/',
  '/ansvar/',
  // Kept although there is nothing to read on them: "myntos logg inn" is
  // something people type into a search box, and the answer should be the
  // page itself rather than the front page.
  '/logg-inn/',
  '/registrer/',
];

/**
 * Sections where every page is public. The moving guides are built from
 * data/flytting.ts, one per program, so naming each one here would be a second
 * list to keep in step with the first.
 */
const PUBLIC_SECTIONS: readonly string[] = ['/kom-i-gang/'];

/** Whether the page at this path belongs in the sitemap and the index. */
export function isIndexable(pathname: string): boolean {
  const path = pathname.endsWith('/') ? pathname : `${pathname}/`;
  return PUBLIC_PAGES.includes(path) || PUBLIC_SECTIONS.some((section) => path.startsWith(section));
}
