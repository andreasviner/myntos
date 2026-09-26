/**
 * Theme and navigation behaviour for the shared page chrome.
 *
 * The theme is light by default and stored only when the visitor actively
 * chooses one, so a fresh browser gets light without a flash and a returning
 * visitor gets what they picked. The *first* application happens in an inline
 * script in the document head, before first paint, because doing it here
 * would show the wrong palette for a frame. This module only handles the
 * toggle afterwards.
 */

export type Theme = 'dark' | 'light';

const THEME_KEY = 'myntos.theme';

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function currentTheme(): Theme {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}

export function applyTheme(theme: Theme, remember = true): void {
  // Light is the default, so it is the *absence* of the attribute. Keeping it
  // that way means the CSS has one palette in :root and one override, rather
  // than two overrides fighting each other.
  if (theme === 'dark') document.documentElement.dataset.theme = 'dark';
  else delete document.documentElement.dataset.theme;

  // Only an actual choice is stored. Writing on every page load would turn the
  // default into a decision the visitor never made, and make a later change of
  // default invisible to everyone who had ever loaded the site.
  if (remember) storage()?.setItem(THEME_KEY, theme);

  for (const button of document.querySelectorAll<HTMLButtonElement>('[data-theme-toggle]')) {
    const next = theme === 'dark' ? 'lyst' : 'mørkt';
    button.setAttribute('aria-label', `Bytt til ${next} tema`);
    button.title = `Bytt til ${next} tema`;
  }
}

export function initThemeToggle(): void {
  applyTheme(currentTheme(), false);
  for (const button of document.querySelectorAll<HTMLButtonElement>('[data-theme-toggle]')) {
    button.addEventListener('click', () => {
      applyTheme(currentTheme() === 'dark' ? 'light' : 'dark');
    });
  }
}

function triggers(): HTMLButtonElement[] {
  return [...document.querySelectorAll<HTMLButtonElement>('[data-menu-trigger]')];
}

function menuFor(trigger: HTMLButtonElement): HTMLElement | null {
  return document.getElementById(trigger.getAttribute('aria-controls') ?? '');
}

function setOpen(trigger: HTMLButtonElement, open: boolean): void {
  const menu = menuFor(trigger);
  trigger.setAttribute('aria-expanded', String(open));
  if (menu === null) return;
  // A panel marked data-collapse is only collapsible at narrow widths, and the
  // header nav is one. Using the `hidden` attribute on it would hide the
  // whole navigation from screen readers on desktop, where it is plainly
  // visible; a data attribute lets CSS decide per breakpoint instead.
  if (menu.dataset.collapse === undefined) menu.hidden = !open;
  else menu.dataset.open = String(open);
}

/**
 * Shut every dropdown.
 *
 * Exported because a menu whose contents are built elsewhere still has to close
 * the way the ones in the markup do. The company switcher fills itself in from
 * the API, and its one row that acts without navigating away (the company
 * already on screen) would otherwise leave the panel hanging open.
 */
export function closeMenus(): void {
  for (const trigger of triggers()) setOpen(trigger, false);
}

/**
 * Open the company switcher in the header from somewhere else on the page.
 *
 * The switcher is one control in one place, which is the whole reason the five
 * per-page pickers were removed. A page that has its own reason to offer the
 * switch (the dashboard says which company it is showing, and «Bytt selskap»
 * belongs next to that) therefore points at the real control rather than
 * growing a second one, and the trigger is focused so the keyboard lands in the
 * menu it just opened.
 *
 * The event has to stop here, and that is not a detail: the caller is a button
 * *outside* the menu, so the click that opens the panel goes on to reach the
 * dismiss-on-click-outside listener below, which closes it again in the same
 * tick. The menu opened and shut so fast that the button looked dead.
 */
export function openCompanyMenu(event?: Event): void {
  event?.stopPropagation();
  const trigger = document.querySelector<HTMLButtonElement>('[data-company-trigger]');
  if (trigger === null) return;
  closeMenus();
  // On a narrow screen the switcher itself is inside the collapsed navigation
  // panel, so opening its dropdown alone would reveal nothing at all. Open
  // whatever contains it first, from the markup rather than by name, so this
  // keeps working if the header is rearranged.
  const panel = trigger.closest<HTMLElement>('[data-collapse]');
  if (panel !== null) {
    const opener = triggers().find((item) => item.getAttribute('aria-controls') === panel.id);
    if (opener !== undefined) setOpen(opener, true);
  }
  setOpen(trigger, true);
  trigger.focus();
}

/**
 * The header menus: one dropdown per trigger, plus the mobile panel.
 *
 * Both are plain buttons with `aria-expanded`, so they work from the keyboard
 * and announce their state. Everything closes on Escape and on a click that
 * lands outside: the two things people expect and libraries usually forget.
 */
export function initNav(): void {
  for (const trigger of triggers()) {
    setOpen(trigger, false);
    trigger.addEventListener('click', (event) => {
      event.stopPropagation();
      const willOpen = trigger.getAttribute('aria-expanded') !== 'true';
      for (const other of triggers()) {
        if (other === trigger) continue;
        // Never close a panel that contains the trigger being used: on mobile
        // the Funksjoner dropdown lives inside the nav panel, and closing its
        // own container would make the menu impossible to open.
        const menu = menuFor(other);
        if (menu !== null && menu.contains(trigger)) continue;
        setOpen(other, false);
      }
      setOpen(trigger, willOpen);
    });
  }

  document.addEventListener('click', (event) => {
    const target = event.target;
    if (target instanceof Node) {
      // A click inside an open menu is navigation, not dismissal.
      const insideMenu = [...document.querySelectorAll('[data-menu]')].some((menu) =>
        menu.contains(target),
      );
      if (insideMenu) return;
    }
    closeMenus();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const open = triggers().find((trigger) => trigger.getAttribute('aria-expanded') === 'true');
    if (open === undefined) return;
    closeMenus();
    open.focus();
  });
}
