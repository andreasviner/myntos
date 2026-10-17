/**
 * Answering the questions a proposal asks.
 *
 * The rules here decide which questions stay on screen and what goes back to
 * the model, and each one is a way of being asked twice if it is wrong: an
 * answer that does not hide its question, an edit taken for an answer, an
 * answer about the other direction sent after the person turned it around.
 */

import { describe, expect, it } from 'vitest';

import type { SuggestionQuestion } from './api';
import {
  MAX_ANSWERS,
  afterDirectionChange,
  answeredByText,
  howToAnswer,
  remember,
  unanswered,
} from './questions';

function question(key: string, options: string[] = []): SuggestionQuestion {
  return {
    text: `Spørsmål ${key}?`,
    field: key === 'belop' ? 'gross_amount' : null,
    asked_by_model: key === 'ki',
    key,
    options: options.map((label) => ({ label, type_code: null, tax_code: null, split: false })),
  };
}

describe('howToAnswer', () => {
  it('offers buttons when the question names its answers', () => {
    expect(howToAnswer(question('driftsmiddel', ['Én ting', 'Flere ting']))).toBe('options');
  });

  it('sends the amount to the amount field', () => {
    expect(howToAnswer(question('belop'))).toBe('amount');
  });

  it('answers anything else in the description', () => {
    expect(howToAnswer(question('ki'))).toBe('description');
    expect(howToAnswer(question('kategori'))).toBe('description');
  });
});

describe('remember', () => {
  it('replaces an earlier answer to the same question', () => {
    const first = remember([], { key: 'ki', question: 'Q', answer: 'Privat' });
    const second = remember(first, { key: 'ki', question: 'Q', answer: 'Til drift' });
    expect(second).toEqual([{ key: 'ki', question: 'Q', answer: 'Til drift' }]);
  });

  it('keeps answers to different questions, in the order given', () => {
    const answers = remember(remember([], { key: 'ki', question: 'A', answer: '1' }), {
      key: 'driftsmiddel',
      question: 'B',
      answer: '2',
    });
    expect(answers.map((answer) => answer.key)).toEqual(['ki', 'driftsmiddel']);
  });

  it('never holds more than the server takes', () => {
    let answers = remember([], { key: null, question: 'Q', answer: '0' });
    for (let n = 1; n < 20; n += 1) {
      answers = remember(answers, { key: null, question: 'Q', answer: String(n) });
    }
    expect(answers).toHaveLength(MAX_ANSWERS);
    expect(answers.at(-1)?.answer).toBe('19');
  });
});

describe('unanswered', () => {
  it('hides a question once it has an answer', () => {
    const asked = [question('ki'), question('driftsmiddel', ['Én', 'Flere'])];
    const left = unanswered(asked, [{ key: 'ki', question: 'Spørsmål ki?', answer: 'Ja' }]);
    expect(left.map((q) => q.key)).toEqual(['driftsmiddel']);
  });

  it('hides the model asking again in other words', () => {
    // The server stops the model asking a second question; a full read that
    // did not know the answer yet must not flash one up either.
    const reworded = { ...question('ki'), text: 'Er dette til selskapet?' };
    expect(unanswered([reworded], [{ key: 'ki', question: 'Til drift?', answer: 'Ja' }])).toEqual([]);
  });
});

describe('answeredByText', () => {
  it('answers the open questions with the sentence written', () => {
    const answers = answeredByText(
      [question('ki'), question('driftsmiddel', ['Én', 'Flere']), question('belop')],
      '  Skjermkort til kontoret, betalt med eget kort ',
    );
    expect(answers).toEqual([
      { key: 'ki', question: 'Spørsmål ki?', answer: 'Skjermkort til kontoret, betalt med eget kort' },
    ]);
  });

  it('is not an answer to a question with buttons', () => {
    expect(answeredByText([question('innbetaling', ['Innskudd fra eier'])], 'Retter et ord')).toEqual(
      [],
    );
  });

  it('is no answer when the box is empty', () => {
    expect(answeredByText([question('ki')], '   ')).toEqual([]);
  });
});

describe('afterDirectionChange', () => {
  it('drops the answers about the other side of the ledger', () => {
    const kept = afterDirectionChange([
      { key: 'ki', question: 'A', answer: 'Til drift' },
      { key: 'kategori_tvil', question: 'B', answer: 'Varekjøp' },
      { key: 'innbetaling', question: 'C', answer: 'Et lån' },
      { key: 'belop', question: 'D', answer: '4990' },
    ]);
    expect(kept.map((answer) => answer.key)).toEqual(['ki', 'belop']);
  });
});
