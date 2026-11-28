/**
 * The KI text box every page shares (components/KiInput.astro) and standalone
 * level pickers (components/KiLevelPicker.astro).
 *
 * Every box where a person writes something for Myntos's KI has the same three
 * things under its text: a button that puts a picture in it, the choice of
 * level («Gratis», «Medium», «Smart»), and the page's own button. The rules
 * are here rather than in each page, because the same box on two pages that
 * disagreed about which levels the company has would be one of them lying.
 *
 * What a box knows about the company comes from `GET …/ki`, asked once per page
 * load (`mountKiInputs().refresh()`), and again after any paid request so the
 * remaining percentage and disabled state stay in step when the weekly quota
 * runs out.
 *
 * The chosen level is remembered in this browser (`myntos.ki-niva`). It is a
 * preference about how to ask, not a fact about any company's books, so it is
 * not cleared with the company record; a level the company cannot use now is
 * shown as «Gratis» instead, and the remembered choice comes back once it can.
 */

import { ApiError, getKiStatus, readKiFile, type KiLevel, type KiStatus } from './api';

export const KI_LEVELS: readonly KiLevel[] = ['free', 'medium', 'thinking'];

/** The names the page shows, the same as the backend's `name` on each level. */
export const KI_LEVEL_NAMES: Readonly<Record<KiLevel, string>> = {
  free: 'Gratis',
  medium: 'Medium',
  thinking: 'Smart',
};

export interface KiLevelChoice {
  value: KiLevel;
  name: string;
  hint: string;
}

/** Single source of truth for the radiogroup rendered by `KiLevelPicker.astro`. */
export const KI_LEVEL_CHOICES: readonly KiLevelChoice[] = [
  { value: 'free', name: KI_LEVEL_NAMES.free, hint: 'Løynds egen modell. Alltid med.' },
  { value: 'medium', name: KI_LEVEL_NAMES.medium, hint: 'En større modell. Krever KI-plan.' },
  {
    value: 'thinking',
    name: KI_LEVEL_NAMES.thinking,
    hint: 'Den største modellen, som tenker før den svarer. Tregere. Krever KI-plan.',
  },
];

const QUOTA_SPENT_HINT = 'Ukekvoten i KI-planen er brukt opp.';

const STORAGE_KEY = 'myntos.ki-niva';

export function isKiLevel(value: unknown): value is KiLevel {
  return typeof value === 'string' && (KI_LEVELS as readonly string[]).includes(value);
}

/** Whether `level` answers now. Unknown status: assume it does, the server falls back. */
export function isAvailable(level: KiLevel, status: KiStatus | null): boolean {
  if (level === 'free' || status === null) return true;
  return status.levels.some((row) => row.level === level && row.available);
}

/** The level a box should show: the wanted one if the company can use it now, otherwise free. */
export function pickLevel(wanted: KiLevel | null, status: KiStatus | null): KiLevel {
  if (wanted === null) return 'free';
  return isAvailable(wanted, status) ? wanted : 'free';
}

/**
 * Whether the three levels are shown.
 *
 * Shown whenever KI is on and a model is available, even when plans are not yet
 * sold on this installation: «Medium» and «Smart» appear disabled for companies
 * without an active plan or with 0 % quota left, so every KI surface presents
 * the same three levels consistently.
 */
export function offersChoice(status: KiStatus | null): boolean {
  if (status === null) return false;
  return status.ki_enabled && status.model_available;
}

/** «85 %», whole percent clamped to 0..100 with Norwegian spacing. */
export function formatPercent(percent: number): string {
  const clamped = Math.max(0, Math.min(100, Math.round(percent)));
  return `${clamped} %`;
}

/** «25. desember», in Norway's time, for when a month ends. */
export function formatDay(iso: string | null): string {
  if (iso === null) return '';
  return new Intl.DateTimeFormat('nb-NO', {
    day: 'numeric',
    month: 'long',
    timeZone: 'Europe/Oslo',
  }).format(new Date(iso));
}

/** The line under the levels: what the company has, or what it would take. */
export interface LevelHint {
  text: string;
  /**
   * Always to the levels and plans on /ki/. The levels are named under every
   * box but explained only there, so this line is the way to them from where
   * KI is used, whether or not anything needs buying. One link, labelled for
   * what the reader would do next: buy when that is what would change the
   * line, otherwise read about the levels.
   */
  link: { href: string; label: string };
}

const PLANS = '/ki/about/#planer';

export function levelHint(status: KiStatus | null): LevelHint | null {
  if (!offersChoice(status) || status === null) return null;
  const about = { href: PLANS, label: 'Om KI-nivåene' };

  const sub = status.subscription;
  const weekly = status.weekly;
  if (sub !== null && weekly !== null) {
    const resetDay = formatDay(weekly.resets_at);
    if (weekly.remaining_percent <= 0) {
      return {
        text: `Ukekvoten i ${sub.plan_name} er brukt opp (0 % igjen). Nullstilles mandag ${resetDay}.`,
        link: status.can_manage_subscription ? { href: PLANS, label: 'Bytt plan' } : about,
      };
    }
    return {
      text: `${sub.plan_name}: ${formatPercent(weekly.remaining_percent)} igjen denne uken.`,
      link: about,
    };
  }

  const current = status.current;
  if (current === null) {
    return {
      text: 'Medium og Smart krever en KI-plan.',
      link: { href: PLANS, label: 'Se KI-planene' },
    };
  }
  const until = formatDay(current.ends_at);
  if (weekly !== null && weekly.remaining_percent <= 0 && current.remaining_percent > 0) {
    const resetDay = formatDay(weekly.resets_at);
    return {
      text: `Ukekvoten i ${current.plan_name} er brukt opp (0 % igjen). Nullstilles mandag ${resetDay}.`,
      link: about,
    };
  }
  if (current.remaining_percent <= 0) {
    // A month already paid for and waiting starts when this one ends, so the
    // honest next step is to wait for it, not to buy a third.
    const next = status.upcoming[0];
    if (next !== undefined) {
      return {
        text: `Kvoten i ${current.plan_name} er brukt opp. Neste måned starter ${until}.`,
        link: about,
      };
    }
    return {
      text: `Kvoten i ${current.plan_name} er brukt opp til ${until}.`,
      link: status.plans_on_sale ? { href: PLANS, label: 'Velg en ny plan' } : about,
    };
  }
  if (weekly !== null) {
    return {
      text: `${current.plan_name}: ${formatPercent(weekly.remaining_percent)} igjen denne uken.`,
      link: about,
    };
  }
  return {
    text: `${current.plan_name}: ${formatPercent(current.remaining_percent)} igjen, til ${until}.`,
    link: about,
  };
}

/**
 * The box's text with what was read off a picture added at the end.
 *
 * Added, never replacing: a person who wrote «faktura til denne kunden» and
 * then attached the customer's e-mail wants both. Cut at the box's own limit,
 * and `cut` says so, because a box that silently dropped the end of an e-mail
 * would send the model half an address.
 */
export function withReadText(
  existing: string,
  read: string,
  maxLength: number | null,
): { text: string; cut: boolean } {
  const before = existing.trimEnd();
  const joined = before === '' ? read.trim() : `${before}\n\n${read.trim()}`;
  if (maxLength !== null && maxLength > 0 && joined.length > maxLength) {
    return { text: joined.slice(0, maxLength), cut: true };
  }
  return { text: joined, cut: false };
}

// --- Pasting images from the clipboard -------------------------------------------

/** Minimal shape of `ClipboardEvent.clipboardData` so tests need no browser DOM. */
export interface ClipboardItemLike {
  readonly kind: string;
  readonly type: string;
  getAsFile(): File | null;
}

export interface ClipboardDataLike {
  readonly items?: ArrayLike<ClipboardItemLike> | null;
  readonly files?: ArrayLike<File> | null;
  getData?(format: string): string;
}

const PASTED_FILE_EXT = /\.(?:png|jpe?g|webp|gif|heic|heif|pdf)$/i;

/**
 * Placeholder names browsers synthesize for a screenshot or copied bitmap.
 * Replaced with `skjermbilde-<n>.<ext>` so multiple pasted screenshots do not
 * share one name in the bilag list or archive, and so the backend's camera-roll
 * check recognises them rather than reading «image» as a counterparty name.
 */
const GENERIC_CLIPBOARD_NAME = /^(?:|blob|image(?:\.[a-z0-9]+)?)$/i;

const MIME_EXTENSIONS: Readonly<Record<string, string>> = {
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/jpg': 'jpg',
  'image/webp': 'webp',
  'image/gif': 'gif',
  'image/heic': 'heic',
  'image/heif': 'heif',
  'application/pdf': 'pdf',
};

let pastedSequence = 0;

function defaultPasteNumber(): number {
  pastedSequence += 1;
  return pastedSequence;
}

function isSupportedPasteFile(file: File): boolean {
  const type = file.type.toLowerCase();
  if (type.startsWith('image/') || type === 'application/pdf') return true;
  // OS file managers sometimes omit MIME type for HEIC/HEIF files.
  return type === '' && PASTED_FILE_EXT.test(file.name);
}

/**
 * Whether `plain` is merely the pasted files' own names or file paths.
 *
 * Copying cells from Excel or formatted text from Word places BOTH `text/plain`
 * and an `image/png` bitmap on the clipboard: that is a text paste and must not
 * be hijacked into OCR. Copying image files from macOS Finder or Linux file
 * managers also puts `text/plain` on the clipboard, but only as the filenames or
 * `file://` URIs of those very files, so the file itself is what was pasted.
 */
function isOnlyFileReferences(plain: string, files: readonly File[]): boolean {
  const lines = plain
    .split(/\r?\n/)
    .map((line) => line.trim().replace(/^["']|["']$/g, ''))
    .filter((line) => line !== '');
  if (lines.length === 0) return true;
  const names = new Set(
    files.map((file) => file.name.trim().toLowerCase()).filter((name) => name !== ''),
  );
  if (names.size === 0) return false;
  return lines.every((line) => {
    let decoded = line;
    try {
      decoded = decodeURIComponent(line);
    } catch {
      // A literal `%` in a path is not a URI escape; compare as-is.
    }
    const base = decoded.split(/[\\/]/).pop()?.toLowerCase() ?? '';
    return names.has(base);
  });
}

function extensionFor(file: File): string {
  const fromMime = MIME_EXTENSIONS[file.type.toLowerCase()];
  if (fromMime !== undefined) return fromMime;
  const fromName = file.name.match(/\.([a-z0-9]+)$/i)?.[1]?.toLowerCase();
  if (fromName !== undefined) return fromName;
  const sub = file.type.toLowerCase().match(/^image\/([a-z0-9]+)$/)?.[1];
  return sub ?? 'png';
}

/** Give an unnamed or generic clipboard screenshot a distinct Norwegian name. */
export function namePastedFile(file: File, nextNumber: () => number = defaultPasteNumber): File {
  const trimmed = file.name.trim();
  if (!GENERIC_CLIPBOARD_NAME.test(trimmed)) return file;
  const name = `skjermbilde-${nextNumber()}.${extensionFor(file)}`;
  return new File([file], name, { type: file.type, lastModified: file.lastModified });
}

/**
 * Supported images (and copied PDFs) from a `paste` event's `clipboardData`.
 *
 * Returns `[]` when the clipboard carries real text alongside a preview image
 * (Excel, Word) so normal text pasting is never interrupted.
 */
export function pastedFiles(
  data: ClipboardDataLike | null | undefined,
  nextNumber: () => number = defaultPasteNumber,
): File[] {
  if (data === null || data === undefined) return [];
  const raw: File[] = [];
  if (data.items !== null && data.items !== undefined && data.items.length > 0) {
    for (let i = 0; i < data.items.length; i += 1) {
      const item = data.items[i];
      if (item === undefined || item.kind !== 'file') continue;
      const file = item.getAsFile();
      if (file !== null && isSupportedPasteFile(file)) raw.push(file);
    }
  } else if (data.files !== null && data.files !== undefined) {
    for (let i = 0; i < data.files.length; i += 1) {
      const file = data.files[i];
      if (file !== undefined && isSupportedPasteFile(file)) raw.push(file);
    }
  }
  if (raw.length === 0) return [];

  const plain = (data.getData?.('text/plain') ?? '').trim();
  if (plain !== '' && !isOnlyFileReferences(plain, raw)) return [];

  return raw.map((file) => namePastedFile(file, nextNumber));
}

/**
 * Hand pasted files to an existing `<input type="file">` as if picked in its
 * dialog, so the page's own `change` handler attaches and reads them.
 */
export function forwardFilesToInput(
  input: HTMLInputElement,
  files: readonly File[],
  makeTransfer: () => DataTransfer = () => new DataTransfer(),
): boolean {
  if (input.disabled || files.length === 0) return false;
  const list = input.multiple ? files : files.slice(0, 1);
  const transfer = makeTransfer();
  for (const file of list) transfer.items.add(file);
  input.files = transfer.files;
  input.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}

/** The level remembered in this browser, or null. Never throws. */
export function rememberedLevel(storage: Pick<Storage, 'getItem'> | null): KiLevel | null {
  try {
    const value = storage?.getItem(STORAGE_KEY) ?? null;
    return isKiLevel(value) ? value : null;
  } catch {
    // A private window or blocked site data: no memory, and no failure.
    return null;
  }
}

export function rememberLevel(storage: Pick<Storage, 'setItem'> | null, level: KiLevel): void {
  try {
    storage?.setItem(STORAGE_KEY, level);
  } catch {
    // As above: the choice still holds for this page.
  }
}

function browserStorage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

// --- The boxes on a page ---------------------------------------------------------

/** What a page does with its KI boxes. */
export interface KiInputs {
  /** Ask the server what the company has, and draw every box from it. */
  refresh(): Promise<void>;
  /** The level chosen in the box `id` (the component's `id` prop). */
  level(id: string): KiLevel;
  /**
   * Call with what an answer says it was answered at. Lower than asked means
   * the weekly quota ran out in between, so the boxes are drawn again.
   */
  answered(asked: KiLevel, answeredAt: KiLevel | null | undefined): void;
  /** The last status read, or null before the first. */
  status(): KiStatus | null;
}

/**
 * Wire every `[data-ki-input]` box on the page.
 *
 * `companyId` is a function, not a number, because the company on screen can
 * move (docs/frontend-conventions.md: ask where you draw it), and a picture
 * read for the previous company would be a request `lib/api.ts` refuses anyway.
 */
export function mountKiInputs(companyId: () => number): KiInputs {
  const roots = [...document.querySelectorAll<HTMLElement>('[data-ki-input]')];
  let status: KiStatus | null = null;
  let wanted: KiLevel | null = rememberedLevel(browserStorage());

  const radios = (root: HTMLElement): HTMLInputElement[] => [
    ...root.querySelectorAll<HTMLInputElement>('[data-ki-levels] input[type="radio"]'),
  ];

  function select(level: KiLevel): void {
    for (const root of roots) {
      for (const radio of radios(root)) {
        radio.checked = radio.value === level;
        // So a form's reset() puts back the person's choice, not «Gratis».
        radio.defaultChecked = radio.checked;
      }
    }
  }

  function draw(): void {
    const choice = offersChoice(status);
    const hint = levelHint(status);
    const quotaSpent =
      (status?.weekly !== null && (status?.weekly.remaining_percent ?? 1) <= 0) ||
      (status?.current !== null && (status?.current.remaining_percent ?? 1) <= 0);
    for (const root of roots) {
      const group = root.querySelector<HTMLElement>('[data-ki-levels]');
      if (group !== null) group.hidden = !choice;
      for (const radio of radios(root)) {
        const disabled = !isKiLevel(radio.value) || !isAvailable(radio.value, status);
        radio.disabled = disabled;
        const label = radio.closest('label');
        const spec = KI_LEVEL_CHOICES.find((item) => item.value === radio.value);
        if (label !== null && spec !== undefined) {
          label.title = disabled && quotaSpent && radio.value !== 'free' ? QUOTA_SPENT_HINT : spec.hint;
        }
      }
      // A box that reads pictures itself needs the model; one whose button is
      // the page's own file picker (the bilag) does not, and keeps it.
      const own = root.querySelector<HTMLElement>('[data-ki-attach]');
      if (own !== null) {
        own.hidden = status !== null && (!status.ki_enabled || !status.model_available);
      }
      const line = root.querySelector<HTMLElement>('[data-ki-hint]');
      if (line !== null) {
        line.replaceChildren();
        if (hint !== null) {
          // Styled by the global `a` rule: a page's scoped style never reaches
          // a node built here.
          const anchor = document.createElement('a');
          anchor.href = hint.link.href;
          anchor.textContent = hint.link.label;
          line.append(hint.text, ' ', anchor);
        }
        line.hidden = hint === null;
      }
    }
    select(pickLevel(wanted, status));
  }

  for (const root of roots) {
    const group = root.querySelector<HTMLElement>('[data-ki-levels]');
    // Not a field of the page's form. Stopped here, so a form that marks a
    // field as the person's own on any input inside it (/kontantstrom/'s
    // description) does not take a change of level for an edit of the text.
    for (const event of ['input', 'change']) {
      group?.addEventListener(event, (changed) => {
        changed.stopPropagation();
        const target = changed.target;
        if (event !== 'change' || !(target instanceof HTMLInputElement)) return;
        if (!isKiLevel(target.value)) return;
        wanted = target.value;
        rememberLevel(browserStorage(), target.value);
        select(target.value);
      });
    }

    const file = root.querySelector<HTMLInputElement>('[data-ki-file]');
    const textarea = root.querySelector<HTMLTextAreaElement>('textarea');
    const note = root.querySelector<HTMLElement>('[data-ki-status]');
    const attach = root.querySelector<HTMLElement>('[data-ki-attach]');
    const attachFor = root.querySelector<HTMLElement>('[data-ki-attach-for]')?.dataset.kiAttachFor;
    if (textarea === null) continue;
    const box = root.querySelector<HTMLElement>('.ki-input-box') ?? textarea;

    if (file !== null) {
      for (const event of ['input', 'change']) {
        file.addEventListener(event, (changed) => changed.stopPropagation());
      }
      file.addEventListener('change', () => {
        const picked = file.files?.[0];
        // Emptied at once, so the same picture can be picked again after an edit.
        file.value = '';
        if (picked === undefined) return;
        void readFilesInto([picked], textarea, note, attach);
      });
      box.addEventListener('paste', (event) => {
        if (attach?.hidden === true) return;
        const images = pastedFiles(event.clipboardData);
        if (images.length === 0) return;
        event.preventDefault();
        if (textarea.disabled) return;
        void readFilesInto(images, textarea, note, attach);
      });
    } else if (attachFor !== undefined) {
      box.addEventListener('paste', (event) => {
        const target = document.getElementById(attachFor);
        if (!(target instanceof HTMLInputElement) || target.type !== 'file' || target.disabled) {
          return;
        }
        const images = pastedFiles(event.clipboardData);
        if (images.length === 0) return;
        event.preventDefault();
        forwardFilesToInput(target, images);
      });
    }
  }

  async function readFilesInto(
    files: readonly File[],
    textarea: HTMLTextAreaElement,
    note: HTMLElement | null,
    attach: HTMLElement | null,
  ): Promise<void> {
    if (files.length === 0) return;
    const say = (message: string, tone: 'ok' | 'error' = 'ok'): void => {
      if (note === null) return;
      note.textContent = message;
      note.dataset.tone = tone;
      note.hidden = message === '';
    };
    const many = files.length > 1;
    attach?.setAttribute('aria-disabled', 'true');
    textarea.disabled = true;
    let added = 0;
    let emptyCount = 0;
    let cut = false;
    try {
      for (const [index, picked] of files.entries()) {
        say(
          many
            ? `Leser bilde ${index + 1} av ${files.length} (${picked.name}) …`
            : `Leser ${picked.name} …`,
        );
        const read = await readKiFile(companyId(), picked);
        if (read.empty || read.text === '') {
          emptyCount += 1;
          continue;
        }
        const limit = textarea.maxLength > 0 ? textarea.maxLength : null;
        const joined = withReadText(textarea.value, read.text, limit);
        textarea.value = joined.text;
        added += 1;
        if (joined.cut) cut = true;
        // As if typed, so whatever the page does on input (a counter, a mark)
        // happens for read text too.
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
      }
      if (added === 0) {
        say(
          many
            ? 'Vi fant ingen tekst i bildene. Skriv det inn selv.'
            : 'Vi fant ingen tekst i bildet. Skriv det inn selv.',
          'error',
        );
        return;
      }
      if (cut) {
        say(
          many
            ? 'Teksten fra bildene er lagt inn, men ble for lang og er kuttet. Se over den før du sender.'
            : 'Teksten fra bildet er lagt inn, men ble for lang og er kuttet. Se over den før du sender.',
        );
        return;
      }
      if (emptyCount > 0) {
        const missed =
          emptyCount === 1
            ? 'ett bilde hadde ingen lesbar tekst'
            : `${emptyCount} bilder hadde ingen lesbar tekst`;
        say(`Teksten fra bildene er lagt inn, men ${missed}. Se over før du sender.`);
        return;
      }
      say(
        many
          ? 'Teksten fra bildene er lagt inn. Se over den før du sender.'
          : 'Teksten fra bildet er lagt inn. Se over den før du sender.',
      );
    } catch (error: unknown) {
      say(error instanceof ApiError ? error.message : 'Vi fikk ikke lest bildet.', 'error');
    } finally {
      textarea.disabled = false;
      attach?.removeAttribute('aria-disabled');
      textarea.focus();
    }
  }

  async function refresh(): Promise<void> {
    try {
      status = await getKiStatus(companyId());
    } catch {
      // Nothing worse than before: every level stays offered, and the server
      // answers a paid one the company cannot use at «Gratis», with a warning.
      status = null;
    }
    draw();
  }

  return {
    refresh,
    level(id: string): KiLevel {
      const root = roots.find((candidate) => candidate.dataset.kiInput === id);
      const checked = root?.querySelector<HTMLInputElement>('[data-ki-levels] input:checked');
      const group = root?.querySelector<HTMLElement>('[data-ki-levels]');
      // A hidden choice is no choice: «Gratis», whatever was remembered.
      if (group?.hidden === true) return 'free';
      return isKiLevel(checked?.value) ? checked.value : 'free';
    },
    answered(asked: KiLevel, answeredAt: KiLevel | null | undefined): void {
      if (asked !== 'free') {
        void refresh();
        return;
      }
      if (answeredAt === null || answeredAt === undefined || answeredAt === asked) return;
      void refresh();
    },
    status: () => status,
  };
}
