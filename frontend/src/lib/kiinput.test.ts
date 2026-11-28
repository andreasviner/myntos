import { describe, expect, it } from 'vitest';

import type { KiPeriod, KiStatus } from './api';
import {
  formatPercent,
  forwardFilesToInput,
  isAvailable,
  isKiLevel,
  KI_LEVEL_CHOICES,
  KI_LEVEL_NAMES,
  levelHint,
  namePastedFile,
  offersChoice,
  pastedFiles,
  pickLevel,
  rememberLevel,
  rememberedLevel,
  withReadText,
  type ClipboardDataLike,
  type ClipboardItemLike,
} from './kiinput';

function period(overrides: Partial<KiPeriod> = {}): KiPeriod {
  return {
    reference: 'ref',
    plan: 'pluss',
    plan_name: 'KI Pluss',
    status: 'paid',
    remaining_percent: 75,
    used_percent: 25,
    amount: '186.25',
    starts_at: '2026-11-25T10:00:00Z',
    ends_at: '2026-12-25T10:00:00Z',
    test: false,
    ...overrides,
  };
}

function status(overrides: Partial<KiStatus> = {}): KiStatus {
  const current = overrides.current === undefined ? null : overrides.current;
  const subscription = overrides.subscription === undefined ? null : overrides.subscription;
  const weekly = overrides.weekly === undefined ? null : overrides.weekly;
  const paid =
    (subscription !== null && (weekly === null || weekly.remaining_percent > 0)) ||
    (current !== null &&
      current.remaining_percent > 0 &&
      (weekly === null || weekly.remaining_percent > 0));
  return {
    ki_enabled: true,
    model_available: true,
    plans_on_sale: true,
    can_buy: true,
    subscription_available: false,
    can_manage_subscription: false,
    levels: [
      { level: 'free', name: 'Gratis', available: true },
      { level: 'medium', name: 'Medium', available: paid },
      { level: 'thinking', name: 'Smart', available: paid },
    ],
    subscription,
    weekly,
    current,
    upcoming: [],
    ...overrides,
  };
}

describe('the level a box shows', () => {
  it('is free when nothing was chosen', () => {
    expect(pickLevel(null, status({ current: period() }))).toBe('free');
  });

  it('is the chosen paid level while the company has quota left', () => {
    expect(pickLevel('thinking', status({ current: period() }))).toBe('thinking');
  });

  it('falls to free without a plan, so the box never claims a level it will not get', () => {
    expect(pickLevel('medium', status())).toBe('free');
  });

  it('falls to free when the quota is spent', () => {
    const spent = status({ current: period({ remaining_percent: 0, used_percent: 100 }) });
    expect(pickLevel('thinking', spent)).toBe('free');
  });

  it('keeps the choice while the status is unknown, since the server falls back itself', () => {
    expect(pickLevel('medium', null)).toBe('medium');
    expect(isAvailable('thinking', null)).toBe(true);
  });

  it('knows only the three levels, named Gratis, Medium and Smart', () => {
    expect(isKiLevel('free')).toBe(true);
    expect(isKiLevel('thinking')).toBe(true);
    expect(isKiLevel('high')).toBe(false);
    expect(isKiLevel(null)).toBe(false);
    expect(KI_LEVEL_CHOICES.map((level) => level.name)).toEqual(['Gratis', 'Medium', 'Smart']);
    expect(KI_LEVEL_NAMES).toEqual({ free: 'Gratis', medium: 'Medium', thinking: 'Smart' });
  });

  it('disables medium and thinking for non-paying companies and exhausted quotas', () => {
    const unpaid = status();
    expect(isAvailable('free', unpaid)).toBe(true);
    expect(isAvailable('medium', unpaid)).toBe(false);
    expect(isAvailable('thinking', unpaid)).toBe(false);

    const spent = status({ current: period({ remaining_percent: 0, used_percent: 100 }) });
    expect(isAvailable('free', spent)).toBe(true);
    expect(isAvailable('medium', spent)).toBe(false);
    expect(isAvailable('thinking', spent)).toBe(false);

    const paying = status({ current: period() });
    expect(isAvailable('medium', paying)).toBe(true);
    expect(isAvailable('thinking', paying)).toBe(true);
  });
});

describe('whether the choice is shown', () => {
  it('is shown whenever KI is enabled and a model is available, even without plans_on_sale', () => {
    expect(offersChoice(status())).toBe(true);
    expect(offersChoice(status({ plans_on_sale: false }))).toBe(true);
    expect(offersChoice(status({ plans_on_sale: false, current: period() }))).toBe(true);
  });

  it('is hidden with KI off or no model: there is nothing to choose between', () => {
    expect(offersChoice(status({ ki_enabled: false }))).toBe(false);
    expect(offersChoice(status({ model_available: false }))).toBe(false);
  });

  it('is hidden until the status is known', () => {
    expect(offersChoice(null)).toBe(false);
  });
});

describe('the line under the levels', () => {
  it('points at the plans on /ki/about/#planer when the company has none', () => {
    const hint = levelHint(status());
    expect(hint?.text).toBe('Medium og Smart krever en KI-plan.');
    expect(hint?.link).toEqual({ href: '/ki/about/#planer', label: 'Se KI-planene' });
  });

  it('shows weekly percentage remaining for an active monthly subscription', () => {
    const hint = levelHint(
      status({
        subscription_available: true,
        can_manage_subscription: true,
        subscription: {
          plan: 'pluss',
          plan_name: 'KI Pluss',
          status: 'active',
          quota_label: '3× ukekvote',
          price_excl_vat: '149.00',
          vat_amount: '37.25',
          price_incl_vat: '186.25',
          currency: 'NOK',
          billing_email: null,
          started_at: '2026-11-25T10:00:00Z',
          current_period_start: '2026-11-25T10:00:00Z',
          current_period_end: '2026-12-25T10:00:00Z',
          cancel_at_period_end: false,
          canceled_at: null,
        },
        weekly: {
          week_start: '2026-11-23',
          resets_at: '2026-11-29T23:00:00Z',
          remaining_percent: 85,
          used_percent: 15,
        },
      }),
    );
    expect(hint?.text).toBe('KI Pluss: 85 % igjen denne uken.');
    expect(hint?.link).toEqual({ href: '/ki/about/#planer', label: 'Om KI-nivåene' });
  });

  it('offers plan upgrade and Monday reset date when weekly quota is spent', () => {
    const hint = levelHint(
      status({
        subscription_available: true,
        can_manage_subscription: true,
        subscription: {
          plan: 'start',
          plan_name: 'KI Start',
          status: 'active',
          quota_label: 'Standard ukekvote',
          price_excl_vat: '49.00',
          vat_amount: '12.25',
          price_incl_vat: '61.25',
          currency: 'NOK',
          billing_email: null,
          started_at: '2026-11-25T10:00:00Z',
          current_period_start: '2026-11-25T10:00:00Z',
          current_period_end: '2026-12-25T10:00:00Z',
          cancel_at_period_end: false,
          canceled_at: null,
        },
        weekly: {
          week_start: '2026-11-23',
          resets_at: '2026-11-29T23:00:00Z',
          remaining_percent: 0,
          used_percent: 100,
        },
      }),
    );
    expect(hint?.text).toContain('Ukekvoten i KI Start er brukt opp (0 % igjen).');
    expect(hint?.text).toContain('mandag 30. november');
    expect(hint?.link).toEqual({ href: '/ki/about/#planer', label: 'Bytt plan' });
  });

  it('says what is left and until when for legacy one-off periods', () => {
    const hint = levelHint(status({ current: period() }));
    expect(hint?.text).toBe('KI Pluss: 75 % igjen, til 25. desember.');
    expect(hint?.link).toEqual({ href: '/ki/about/#planer', label: 'Om KI-nivåene' });
  });

  it('offers a new plan when legacy quota is spent and plans are on sale', () => {
    const hint = levelHint(status({ current: period({ remaining_percent: 0, used_percent: 100 }) }));
    expect(hint?.text).toContain('brukt opp');
    expect(hint?.link).toEqual({ href: '/ki/about/#planer', label: 'Velg en ny plan' });
  });

  it('links to the level overview when quota is spent and plans are not on sale', () => {
    const hint = levelHint(
      status({ plans_on_sale: false, current: period({ remaining_percent: 0, used_percent: 100 }) }),
    );
    expect(hint?.text).toContain('brukt opp');
    expect(hint?.link).toEqual({ href: '/ki/about/#planer', label: 'Om KI-nivåene' });
  });

  it('does not sell a third month when the next one is already paid for', () => {
    const hint = levelHint(
      status({
        current: period({ remaining_percent: 0, used_percent: 100 }),
        upcoming: [period({ reference: 'neste' })],
      }),
    );
    expect(hint?.text).toContain('Neste måned starter');
    expect(hint?.link).toEqual({ href: '/ki/about/#planer', label: 'Om KI-nivåene' });
  });

  it('says nothing where there is no choice', () => {
    expect(levelHint(status({ ki_enabled: false }))).toBeNull();
  });
});

describe('text read off a picture', () => {
  it('fills an empty box', () => {
    expect(withReadText('  ', ' Bygg AS\nOrg.nr 123 ', null)).toEqual({
      text: 'Bygg AS\nOrg.nr 123',
      cut: false,
    });
  });

  it('goes after what the person already wrote, never instead of it', () => {
    expect(withReadText('Faktura til denne:', 'Bygg AS', null).text).toBe(
      'Faktura til denne:\n\nBygg AS',
    );
  });

  it('is cut at the box limit, and says so', () => {
    expect(withReadText('abc', 'defghij', 8)).toEqual({ text: 'abc\n\ndef', cut: true });
  });
});

describe('the remembered level', () => {
  it('reads back what was stored', () => {
    const store = new Map<string, string>();
    const storage = {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
    };
    rememberLevel(storage, 'thinking');
    expect(rememberedLevel(storage)).toBe('thinking');
  });

  it('ignores anything that is not a level', () => {
    expect(rememberedLevel({ getItem: () => 'high' })).toBeNull();
  });

  it('never throws, in a private window or with storage blocked', () => {
    const blocked = {
      getItem: (): string | null => {
        throw new Error('blocked');
      },
      setItem: (): void => {
        throw new Error('blocked');
      },
    };
    expect(rememberedLevel(blocked)).toBeNull();
    expect(() => rememberLevel(blocked, 'medium')).not.toThrow();
    expect(rememberedLevel(null)).toBeNull();
  });
});

describe('quota percentages', () => {
  it('are formatted with Norwegian spacing and clamped to 0..100', () => {
    expect(formatPercent(85)).toBe('85 %');
    expect(formatPercent(-5)).toBe('0 %');
    expect(formatPercent(140)).toBe('100 %');
  });
});

function fileItem(file: File): ClipboardItemLike {
  return { kind: 'file', type: file.type, getAsFile: () => file };
}

function stringItem(type: string): ClipboardItemLike {
  return { kind: 'string', type, getAsFile: () => null };
}

function clipboard(
  items: ClipboardItemLike[],
  textByType: Record<string, string> = {},
): ClipboardDataLike {
  return {
    items,
    getData: (format: string) => textByType[format] ?? '',
  };
}

describe('pasting images from the clipboard', () => {
  it('extracts a pasted screenshot and gives generic clipboard names a Norwegian name', () => {
    let seq = 0;
    const next = () => (seq += 1);
    const png = new File(['png-bytes'], 'image.png', { type: 'image/png' });
    const result = pastedFiles(clipboard([fileItem(png)]), next);
    expect(result).toHaveLength(1);
    expect(result[0]?.name).toBe('skjermbilde-1.png');
    expect(result[0]?.type).toBe('image/png');
    expect(result[0]?.size).toBe(png.size);
  });

  it('gives distinct names to consecutive screenshots', () => {
    let seq = 0;
    const next = () => (seq += 1);
    const first = namePastedFile(new File(['a'], '', { type: 'image/jpeg' }), next);
    const second = namePastedFile(new File(['b'], 'image.png', { type: 'image/png' }), next);
    expect(first.name).toBe('skjermbilde-1.jpg');
    expect(second.name).toBe('skjermbilde-2.png');
  });

  it('extracts right-click copied images that also carry text/html', () => {
    let seq = 0;
    const png = new File(['img'], 'image.png', { type: 'image/png' });
    const data = clipboard([stringItem('text/html'), fileItem(png)], {
      'text/html': '<img src="https://example.com/a.png">',
    });
    const result = pastedFiles(data, () => (seq += 1));
    expect(result).toHaveLength(1);
    expect(result[0]?.name).toBe('skjermbilde-1.png');
  });

  it('keeps real filenames when image files are copied from a file manager', () => {
    const file = new File(['jpg'], 'kvittering.jpg', { type: 'image/jpeg' });
    const data = clipboard([stringItem('text/plain'), fileItem(file)], {
      'text/plain': 'kvittering.jpg',
    });
    const result = pastedFiles(data);
    expect(result).toHaveLength(1);
    expect(result[0]?.name).toBe('kvittering.jpg');
  });

  it('accepts file:// URIs in text/plain from Linux or macOS file managers', () => {
    const one = new File(['a'], 'kvittering 1.png', { type: 'image/png' });
    const two = new File(['b'], 'faktura.pdf', { type: 'application/pdf' });
    const data = clipboard([stringItem('text/plain'), fileItem(one), fileItem(two)], {
      'text/plain': 'file:///home/user/kvittering%201.png\nfile:///home/user/faktura.pdf',
    });
    const result = pastedFiles(data);
    expect(result.map((f) => f.name)).toEqual(['kvittering 1.png', 'faktura.pdf']);
  });

  it('accepts HEIC images even when the OS leaves MIME type empty', () => {
    const heic = new File(['heic'], 'IMG_9362.HEIC', { type: '' });
    const result = pastedFiles(clipboard([fileItem(heic)]));
    expect(result).toHaveLength(1);
    expect(result[0]?.name).toBe('IMG_9362.HEIC');
  });

  it('does not hijack Excel or Word pastes that include both real text and a preview image', () => {
    let seq = 0;
    const preview = new File(['bitmap'], 'image.png', { type: 'image/png' });
    const data = clipboard(
      [stringItem('text/plain'), stringItem('text/html'), fileItem(preview)],
      { 'text/plain': 'Bygg AS\t1 500,00' },
    );
    expect(pastedFiles(data, () => (seq += 1))).toEqual([]);
    expect(seq).toBe(0);
  });

  it('ignores plain text and unsupported file types', () => {
    const doc = new File(['zip'], 'rapport.docx', {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    });
    expect(pastedFiles(clipboard([stringItem('text/plain')], { 'text/plain': 'hei' }))).toEqual([]);
    expect(pastedFiles(clipboard([fileItem(doc)]))).toEqual([]);
    expect(pastedFiles(null)).toEqual([]);
  });

  it('falls back to clipboardData.files when items is empty', () => {
    let seq = 0;
    const png = new File(['png'], 'image.png', { type: 'image/png' });
    const result = pastedFiles({ items: [], files: [png] }, () => (seq += 1));
    expect(result).toHaveLength(1);
    expect(result[0]?.name).toBe('skjermbilde-1.png');
  });

  it('forwards files to an input and dispatches a change event', () => {
    const one = new File(['a'], 'skjermbilde-1.png', { type: 'image/png' });
    const two = new File(['b'], 'skjermbilde-2.png', { type: 'image/png' });
    const added: File[] = [];
    const events: string[] = [];
    const fakeTransfer = {
      items: { add: (file: File) => void added.push(file) },
      get files() {
        return added as unknown as FileList;
      },
    } as unknown as DataTransfer;

    const multiInput = {
      disabled: false,
      multiple: true,
      files: null as FileList | null,
      dispatchEvent: (event: Event) => {
        events.push(event.type);
        return true;
      },
    } as unknown as HTMLInputElement;

    expect(forwardFilesToInput(multiInput, [one, two], () => fakeTransfer)).toBe(true);
    expect(added).toEqual([one, two]);
    expect(events).toEqual(['change']);

    // Respects multiple=false by forwarding only the first file.
    const singleAdded: File[] = [];
    const singleInput = {
      disabled: false,
      multiple: false,
      files: null as FileList | null,
      dispatchEvent: () => true,
    } as unknown as HTMLInputElement;
    forwardFilesToInput(singleInput, [one, two], () => ({
      items: { add: (file: File) => void singleAdded.push(file) },
      files: singleAdded as unknown as FileList,
    }) as unknown as DataTransfer);
    expect(singleAdded).toEqual([one]);

    // Does nothing when the input is disabled or there are no files.
    const disabledInput = { ...singleInput, disabled: true } as unknown as HTMLInputElement;
    expect(forwardFilesToInput(disabledInput, [one], () => fakeTransfer)).toBe(false);
    expect(forwardFilesToInput(multiInput, [], () => fakeTransfer)).toBe(false);
  });
});
