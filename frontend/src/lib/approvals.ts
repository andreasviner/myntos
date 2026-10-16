/**
 * What /godkjenning/ says about a request a key is waiting on.
 *
 * A key whose person chose «Spør meg først» does not write on its own: the
 * request is stored, and the person approves or declines it here
 * (docs/agents-and-api.md rule 13). The same for an AI assistant and for a
 * program somebody wrote against the API, because a key is a key and the page
 * cannot tell them apart, nor should it need to.
 *
 * Most of this is translation of what the API returns into words a person
 * reads correctly, which is why it lives here and is tested: a request shown
 * as harmless when it posts a voucher is a person approving something blind.
 */

import type { Approval, ApprovalState } from './api';

/** How a request stands, in a few words. */
const STATE_LABELS: Record<ApprovalState, string> = {
  pending: 'Venter på godkjenning',
  running: 'Kjøres nå',
  done: 'Utført',
  failed: 'Avvist av Myntos',
  declined: 'Avvist',
  withdrawn: 'Trukket tilbake av agenten',
  expired: 'Gikk ut uten svar',
  key_stopped: 'Nøkkelen er stoppet',
};

export function approvalStateLabel(state: ApprovalState): string {
  return STATE_LABELS[state];
}

/** Whether a person still has something to decide. */
export function isWaiting(approval: Pick<Approval, 'status'>): boolean {
  return approval.status === 'pending';
}

/**
 * The heading of a request: who wants to do what.
 *
 * «Claude vil føre en betaling». The label comes from the route itself, in
 * bokmål (`key_post("føre en betaling")`), so it is Myntos saying what the
 * request does, not the agent. A request to file reads as a request, because
 * that is all it is: the person files on the MVA page.
 */
export function approvalHeading(approval: Pick<Approval, 'kind' | 'key_name' | 'label'>): string {
  if (approval.kind === 'filing') return `«${approval.key_name}» ber deg om ${approval.label}`;
  return `«${approval.key_name}» vil ${approval.label}`;
}

/** Whether what is asked can be undone afterwards, for the line under the heading. */
export function isFinal(approval: Pick<Approval, 'scope'>): boolean {
  return approval.scope === 'post';
}

// Where in the app the thing a request touches is shown, by the path it went
// to. Longest first, so /mva/spesifikasjoner wins over /mva.
const PAGES: [string, string, string][] = [
  ['/mva/spesifikasjoner', '/mva-justeringer/', 'Mva-justeringer'],
  ['/inngaende-balanse', '/inngaende-balanse/', 'Inngående balanse'],
  ['/manuelt-bilag', '/manuelt-bilag/', 'Manuelt bilag'],
  ['/anlegg', '/anlegg/', 'Anlegg og avskrivning'],
  ['/saft/bilag', '/tidligere-program/', 'Tidligere regnskapsprogram'],
  ['/arsavslutning', '/arsavslutning/', 'Årsavslutning'],
  ['/landbruk/telling', '/buskap/', 'Buskap og lager'],
  ['/landbruk/uttak', '/private-uttak/', 'Private uttak'],
  ['/kontoplan', '/kontoplan/', 'Kontoplanen'],
  ['/dimensjoner', '/avdelinger/', 'Avdelinger'],
  ['/dimensjonsverdier', '/avdelinger/', 'Avdelinger'],
  ['/cashflow', '/kontantstrom/', 'Betaling'],
  ['/faktura', '/faktura/', 'Faktura'],
  ['/parties', '/parter/', 'Kunder og leverandører'],
  ['/bank', '/bank/', 'Bankavstemming'],
  ['/mva', '/mva/', 'MVA'],
  ['/hent', '/organisasjon/#selskap', 'Selskapet'],
];

/** The page where the result of a request can be seen, or null for none. */
export function pageFor(path: string): { href: string; label: string } | null {
  const tail = path.replace(/^\/api\/v1\/companies\/\d+/, '');
  for (const [prefix, href, label] of PAGES) {
    if (tail === prefix || tail.startsWith(`${prefix}/`)) return { href, label };
  }
  return null;
}

/**
 * The fields a request sent, as label and value, for a person to read.
 *
 * The API's own field names: translating them would be a second description
 * of every route that writes, which is the thing the design avoids
 * (myntos.auth.approvals). Nested values are shown as JSON, money stays the
 * decimal string it was sent as, and an empty value is left out because it
 * says nothing.
 */
export function requestFields(body: unknown): [string, string][] {
  if (typeof body !== 'object' || body === null || Array.isArray(body)) return [];
  const rows: [string, string][] = [];
  for (const [name, value] of Object.entries(body as Record<string, unknown>)) {
    if (value === null || value === undefined || value === '') continue;
    const shown =
      typeof value === 'string'
        ? value
        : typeof value === 'number' || typeof value === 'boolean'
          ? String(value)
          : JSON.stringify(value, null, 2);
    rows.push([name, shown]);
  }
  return rows;
}

/**
 * What happened when an approved request ran, in a sentence.
 *
 * A refusal carries the backend's own Norwegian `detail`, which is exactly
 * what the person needs: the route said no, and why.
 */
export function outcomeText(approval: Pick<Approval, 'status' | 'result' | 'attachments'>): string {
  if (approval.status === 'done') {
    const failed = approval.attachments.filter((item) => item.status >= 300);
    if (failed.length === 0) return 'Utført.';
    const names = failed.map((item) => `«${item.filename}»`).join(', ');
    return `Utført, men ${names} kom ikke i bilagsarkivet. Legg det ved selv på Betaling.`;
  }
  if (approval.status === 'failed') {
    const result = approval.result;
    const detail =
      typeof result === 'object' &&
      result !== null &&
      typeof (result as { detail?: unknown }).detail === 'string'
        ? (result as { detail: string }).detail
        : null;
    return detail === null ? 'Myntos avviste det.' : `Myntos avviste det: ${detail}`;
  }
  if (approval.status === 'running') {
    return 'Den ble startet, men vi vet ikke om den ble fullført. Sjekk regnskapet før du ber om den igjen.';
  }
  return approvalStateLabel(approval.status);
}

/** «2 venter», for the menu. Empty when nothing waits, so the badge hides. */
export function waitingBadge(count: number): string {
  return count > 0 ? String(count) : '';
}
