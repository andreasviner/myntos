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

/**
 * The header menus: one dropdown per trigger, plus the mobile panel.
 *
 * Both are plain buttons with `aria-expanded`, so they work from the keyboard
 * and announce their state. Everything closes on Escape and on a click that
 * lands outside: the two things people expect and libraries usually forget.
 */
export function initNav(): void {
  const triggers = [...document.querySelectorAll<HTMLButtonElement>('[data-menu-trigger]')];

  const menuFor = (trigger: HTMLButtonElement): HTMLElement | null =>
    document.getElementById(trigger.getAttribute('aria-controls') ?? '');

  const setOpen = (trigger: HTMLButtonElement, open: boolean): void => {
    const menu = menuFor(trigger);
    trigger.setAttribute('aria-expanded', String(open));
    if (menu === null) return;
    // A panel marked data-collapse is only collapsible at narrow widths, and the
    // header nav is one. Using the `hidden` attribute on it would hide the
    // whole navigation from screen readers on desktop, where it is plainly
    // visible; a data attribute lets CSS decide per breakpoint instead.
    if (menu.dataset.collapse === undefined) menu.hidden = !open;
    else menu.dataset.open = String(open);
  };

  const closeAll = (): void => {
    for (const trigger of triggers) setOpen(trigger, false);
  };

  for (const trigger of triggers) {
    setOpen(trigger, false);
    trigger.addEventListener('click', (event) => {
      event.stopPropagation();
      const willOpen = trigger.getAttribute('aria-expanded') !== 'true';
      for (const other of triggers) {
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
    closeAll();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const open = triggers.find((trigger) => trigger.getAttribute('aria-expanded') === 'true');
    if (open === undefined) return;
    closeAll();
    open.focus();
  });
}
