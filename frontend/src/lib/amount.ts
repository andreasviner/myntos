/**
 * Turning an amount a person typed into the decimal string the API reads.
 *
 * Text in, text out: nothing here makes a number of it, for the reason
 * lib/format.ts gives. A float would round 0.1 + 0.2, and an opening balance
 * that is an øre out does not post. The backend parses the string as a
 * Decimal and is the one that decides whether it balances.
 *
 * Norwegian input is the case that matters: «1 234,56» with a space or a
 * no-break space between the thousands and a comma before the øre. A dot as
 * the decimal mark is accepted too, because that is what a pasted figure from
 * an English spreadsheet looks like. What cannot be read returns null rather
 * than a guess, and the page says which field it was.
 */

const SPACES = /[\s   ]/g;

/**
 * `decimals` is 2 for money. A count of feed in kilos, or a value per kilo
 * from the takseringsregler (0,45 kr), is not money and may need more; the
 * backend still says how many it accepts.
 */
export function normaliseAmount(raw: string, decimals = 2): string | null {
  let text = raw.replace(SPACES, '').replace('−', '-');
  if (text === '') return '0';
  let negative = false;
  if (text.startsWith('-')) {
    negative = true;
    text = text.slice(1);
  }
  if (text.includes(',') && text.includes('.')) {
    // Whichever comes last is the decimal mark.
    text =
      text.lastIndexOf(',') > text.lastIndexOf('.')
        ? text.replaceAll('.', '').replace(',', '.')
        : text.replaceAll(',', '');
  } else if (text.includes(',')) {
    if (text.split(',').length > 2) return null;
    text = text.replace(',', '.');
  }
  if (!new RegExp(`^\\d+(\\.\\d{1,${decimals}})?$`).test(text)) return null;
  return negative ? `-${text}` : text;
}
