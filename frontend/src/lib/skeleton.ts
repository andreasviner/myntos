/**
 * Placeholders that hold a page's shape while its figures are on their way.
 *
 * The rule this file exists to keep: a page that is loading should be the page,
 * at its finished size, with the unknown parts marked unknown. Not a smaller
 * page that grows into the real one when the last request lands. The reader is
 * looking at the layout before they can read the numbers, and a layout that
 * assembles itself under them moves whatever they were about to click.
 *
 * Most placeholders are markup: the ones a page starts with ship in the HTML,
 * so they are on screen with the first paint and no script is needed to put
 * them there. This module is for the other case — a page that already has
 * figures and is about to fetch different ones, such as the overview when the
 * company picker changes. The numbers on screen belong to the company being
 * left, and leaving them under the new company's name is worse than showing
 * nothing.
 *
 * Nothing here announces itself. A screen reader is told once, by `aria-busy`
 * on the region, which is the fact; a dozen unlabelled boxes read out one by
 * one is noise. See `.skeleton` in BaseLayout for the styling, including what
 * it does when the reader has asked for less motion.
 */

/** One placeholder, as wide as the value it stands in for. */
export function skeleton(width: string): HTMLSpanElement {
  const node = document.createElement('span');
  node.className = 'skeleton';
  node.style.setProperty('--skeleton-width', width);
  return node;
}

/** Replace an element's content with a single placeholder. */
export function skeletonInto(node: HTMLElement, width: string): void {
  node.replaceChildren(skeleton(width));
}

/** One column of a placeholder row: how wide, and whether it is a figure. */
export interface SkeletonColumn {
  width: string;
  /** Right-aligned and tabular, matching `td.num` in the real table. */
  num?: boolean;
}

/**
 * Fill a table body with placeholder rows.
 *
 * Marked `data-skeleton-row` for the same reason the ones in the markup are:
 * whatever finally clears them should be able to find them without knowing
 * which of the two put them there.
 */
export function skeletonRows(
  body: HTMLTableSectionElement,
  columns: SkeletonColumn[],
  rows: number,
): void {
  const built: HTMLTableRowElement[] = [];
  for (let index = 0; index < rows; index += 1) {
    const row = document.createElement('tr');
    row.dataset.skeletonRow = '';
    for (const column of columns) {
      const cell = document.createElement('td');
      if (column.num === true) cell.className = 'num';
      cell.append(skeleton(column.width));
      row.append(cell);
    }
    built.push(row);
  }
  body.replaceChildren(...built);
}
