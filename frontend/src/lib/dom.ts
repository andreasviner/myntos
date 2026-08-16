/** Small DOM helpers, so pages read as logic rather than as null checks. */

/** Find an element that the page is known to contain, or fail loudly. */
export function el<T extends HTMLElement>(selector: string, root: ParentNode = document): T {
  const node = root.querySelector<T>(selector);
  if (node === null) throw new Error(`Fant ikke elementet ${selector}`);
  return node;
}

/** Find an element that may legitimately be absent on this page. */
export function maybe<T extends HTMLElement>(selector: string, root: ParentNode = document): T | null {
  return root.querySelector<T>(selector);
}

export function show(node: HTMLElement, visible: boolean): void {
  node.hidden = !visible;
}

/**
 * Put a message in a status box.
 *
 * `textContent`, never `innerHTML`: some of these messages come from the API
 * and one of them could one day contain something a user typed.
 */
export function setStatus(node: HTMLElement, message: string, tone: 'error' | 'ok' = 'error'): void {
  node.textContent = message;
  node.dataset.tone = tone;
  node.hidden = message === '';
}

export function clearStatus(node: HTMLElement): void {
  setStatus(node, '');
}
