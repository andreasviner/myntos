/**
 * Pure helpers for the /ki/ chat page: starter suggestions, safe DOM markdown
 * rendering (zero innerHTML, safe against untrusted voucher text), and time
 * formatting.
 */

export const CHAT_SUGGESTIONS: readonly string[] = [
  'Hva er de største kostnadene våre så langt i år?',
  'Hvilke fakturaer er ubetalte eller forfalt nå?',
  'Hvordan ser resultatet og bankbeholdningen ut i år sammenlignet med i fjor?',
  'Når er neste mva- eller skattefrist, og hva gjenstår?',
];

export type InlineToken =
  | { kind: 'text'; value: string }
  | { kind: 'strong'; value: string }
  | { kind: 'code'; value: string };

/**
 * Parse inline **bold** and `code` spans without HTML parsing.
 * Unmatched markers stay literal text so streaming partial lines stay clean.
 */
export function parseInline(text: string): InlineToken[] {
  const tokens: InlineToken[] = [];
  const pattern = /(\*\*[^*\n]+?\*\*|`[^`\n]+?`)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      tokens.push({ kind: 'text', value: text.slice(lastIndex, match.index) });
    }
    const raw = match[0];
    if (raw.startsWith('**') && raw.endsWith('**')) {
      tokens.push({ kind: 'strong', value: raw.slice(2, -2) });
    } else if (raw.startsWith('`') && raw.endsWith('`')) {
      tokens.push({ kind: 'code', value: raw.slice(1, -1) });
    }
    lastIndex = match.index + raw.length;
  }
  if (lastIndex < text.length) {
    tokens.push({ kind: 'text', value: text.slice(lastIndex) });
  }
  return tokens;
}

export type Block =
  | { kind: 'heading'; level: 2 | 3 | 4; text: string }
  | { kind: 'paragraph'; text: string }
  | { kind: 'ul'; items: string[] }
  | { kind: 'ol'; items: string[] }
  | { kind: 'table'; headers: string[]; rows: string[][] };

function splitTableRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '');
  return trimmed.split('|').map((cell) => cell.trim());
}

function isTableSeparator(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed.includes('|')) return false;
  return /^\|?(\s*:?-{2,}:?\s*\|)+\s*:?-{2,}:?\s*\|?$/.test(trimmed);
}

/** Parse assistant markdown into simple, safe structural blocks. */
export function parseBlocks(markdown: string): Block[] {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n');
  const blocks: Block[] = [];
  let i = 0;

  while (i < lines.length) {
    const raw = lines[i] ?? '';
    const trimmed = raw.trim();
    if (trimmed === '') {
      i += 1;
      continue;
    }

    // Headings
    const headingMatch = /^(#{1,4})\s+(.+)$/.exec(trimmed);
    if (headingMatch !== null) {
      const hashes = headingMatch[1]?.length ?? 3;
      const level: 2 | 3 | 4 = hashes <= 2 ? 2 : hashes === 3 ? 3 : 4;
      blocks.push({ kind: 'heading', level, text: (headingMatch[2] ?? '').trim() });
      i += 1;
      continue;
    }

    // Markdown table (header line + separator line + body rows)
    const nextLine = lines[i + 1] ?? '';
    if (trimmed.includes('|') && isTableSeparator(nextLine)) {
      const headers = splitTableRow(trimmed);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length) {
        const rowLine = (lines[i] ?? '').trim();
        if (rowLine === '' || !rowLine.includes('|')) break;
        rows.push(splitTableRow(rowLine));
        i += 1;
      }
      blocks.push({ kind: 'table', headers, rows });
      continue;
    }

    // Unordered list
    if (/^[-*•]\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length) {
        const itemLine = (lines[i] ?? '').trim();
        const itemMatch = /^[-*•]\s+(.+)$/.exec(itemLine);
        if (itemMatch === null) break;
        items.push((itemMatch[1] ?? '').trim());
        i += 1;
      }
      blocks.push({ kind: 'ul', items });
      continue;
    }

    // Ordered list
    if (/^\d+\.\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length) {
        const itemLine = (lines[i] ?? '').trim();
        const itemMatch = /^\d+\.\s+(.+)$/.exec(itemLine);
        if (itemMatch === null) break;
        items.push((itemMatch[1] ?? '').trim());
        i += 1;
      }
      blocks.push({ kind: 'ol', items });
      continue;
    }

    // Paragraph (consecutive plain lines until blank or another block)
    const paraLines: string[] = [];
    while (i < lines.length) {
      const cur = (lines[i] ?? '').trim();
      if (
        cur === '' ||
        /^#{1,4}\s+/.test(cur) ||
        /^[-*•]\s+/.test(cur) ||
        /^\d+\.\s+/.test(cur) ||
        (cur.includes('|') && isTableSeparator(lines[i + 1] ?? ''))
      ) {
        break;
      }
      paraLines.push(cur);
      i += 1;
    }
    if (paraLines.length > 0) {
      blocks.push({ kind: 'paragraph', text: paraLines.join('\n') });
    }
  }

  return blocks;
}

function appendInline(parent: HTMLElement, text: string): void {
  const lines = text.split('\n');
  lines.forEach((line, lineIndex) => {
    if (lineIndex > 0) parent.append(document.createElement('br'));
    for (const token of parseInline(line)) {
      if (token.kind === 'strong') {
        const el = document.createElement('strong');
        el.textContent = token.value;
        parent.append(el);
      } else if (token.kind === 'code') {
        const el = document.createElement('code');
        el.textContent = token.value;
        parent.append(el);
      } else {
        parent.append(document.createTextNode(token.value));
      }
    }
  });
}

/**
 * Render markdown text safely into DOM nodes using `textContent` only.
 * Never sets `innerHTML`, so untrusted invoice or voucher text cannot inject HTML.
 */
export function renderMarkdown(markdown: string): DocumentFragment {
  const fragment = document.createDocumentFragment();
  for (const block of parseBlocks(markdown)) {
    if (block.kind === 'heading') {
      const tag = block.level === 2 ? 'h3' : block.level === 3 ? 'h4' : 'h5';
      const el = document.createElement(tag);
      appendInline(el, block.text);
      fragment.append(el);
    } else if (block.kind === 'paragraph') {
      const p = document.createElement('p');
      appendInline(p, block.text);
      fragment.append(p);
    } else if (block.kind === 'ul' || block.kind === 'ol') {
      const list = document.createElement(block.kind);
      for (const item of block.items) {
        const li = document.createElement('li');
        appendInline(li, item);
        list.append(li);
      }
      fragment.append(list);
    } else if (block.kind === 'table') {
      const wrap = document.createElement('div');
      wrap.className = 'chat-table-wrap';
      const table = document.createElement('table');
      const thead = document.createElement('thead');
      const headRow = document.createElement('tr');
      for (const header of block.headers) {
        const th = document.createElement('th');
        appendInline(th, header);
        headRow.append(th);
      }
      thead.append(headRow);
      table.append(thead);
      const tbody = document.createElement('tbody');
      for (const row of block.rows) {
        const tr = document.createElement('tr');
        for (const cell of row) {
          const td = document.createElement('td');
          appendInline(td, cell);
          tr.append(td);
        }
        tbody.append(tr);
      }
      table.append(tbody);
      wrap.append(table);
      fragment.append(wrap);
    }
  }
  return fragment;
}

/** Compact Norwegian date/time label for saved chat threads in the sidebar. */
export function formatThreadTime(iso: string, now: Date = new Date()): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const fmtDay = new Intl.DateTimeFormat('nb-NO', {
    day: 'numeric',
    month: 'short',
    timeZone: 'Europe/Oslo',
  });
  const today = fmtDay.format(now);
  const target = fmtDay.format(date);
  if (today === target) {
    return new Intl.DateTimeFormat('nb-NO', {
      hour: '2-digit',
      minute: '2-digit',
      timeZone: 'Europe/Oslo',
    }).format(date);
  }
  return target;
}
