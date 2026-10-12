/**
 * Reading a token out of a link Myntos mailed to somebody.
 *
 * The confirmation and reset links carry their token in the fragment
 * (`/nytt-passord/#token=…`) rather than the query string. A fragment never
 * leaves the browser: it is not in the frontend host's access log, not in a
 * Referer header, and not in anything a proxy records. A reset link is a
 * password for half an hour, so that matters.
 *
 * Kept here rather than in the pages so it is tested: a page that reads the
 * wrong half of the URL fails silently, with every link "invalid".
 */

/** The `token` in a `#token=…` fragment, or null when there is none. */
export function tokenFromFragment(hash: string): string | null {
  const fragment = hash.startsWith('#') ? hash.slice(1) : hash;
  const token = new URLSearchParams(fragment).get('token');
  return token === null || token.trim() === '' ? null : token.trim();
}

/**
 * Take the token off the address bar once it has been read.
 *
 * So it does not sit in the browser's history, or get copied along with the
 * URL by somebody sharing their screen to ask for help.
 */
export function forgetFragment(): void {
  try {
    window.history.replaceState(null, '', window.location.pathname + window.location.search);
  } catch {
    // An address bar we may not rewrite is not a reason to stop: the token is
    // already read, and the page works either way.
  }
}
