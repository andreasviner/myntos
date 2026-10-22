/**
 * The questions a proposal asks, and the answers given to them on one bilag.
 *
 * Answering used to mean writing in the description box and waiting for the
 * whole bilag to be read again, and nothing on the page said so. The person
 * who reported it had answered, and was asked the same question again. Now a
 * question is answered right under it: with a button when it names its own
 * answers, and in a line of its own when it does not. The answer goes to the
 * server as a follow-up (`answerSuggestion`), and an answered question is not
 * shown again.
 *
 * One question at a time. For a while every question stood on screen at once,
 * each pointing at the description box, and the description is the text the
 * payment is filed under; the person who tried it (2026-10-17) wanted to send
 * the model an answer, not write one into the books. So the page shows
 * «Spørsmål 1 av 2», «Neste» brings the next, and the answers go to the model
 * together after the last.
 *
 * Together, and not one by one, because one by one was tried the same day: the
 * model answered the first, decided its own question had been answered with
 * it, and «Spørsmål 2 av 2» never came. A list that says two and shows one
 * reads as broken. The questions of a round are fixed when it starts, and only
 * the proposal that comes back after the round decides whether there is a next.
 *
 * The page owns the list of answers, not the server. The server keeps the
 * reading for ten minutes and no longer; the page sends every answer with every
 * follow-up, so a reading that is gone is read again and answered exactly as
 * before. That is why these rules live here, where they can be tested.
 */

import type { SuggestionAnswer, SuggestionQuestion } from './api';

/** As many answers as the server takes in one follow-up. */
export const MAX_ANSWERS = 12;

/** The key of the one question the model composes; see the backend's questions.py. */
export const MODEL_KEY = 'ki';

/** The key of the question whose answer is a number: what the account was charged. */
export const AMOUNT_KEY = 'belop';

/**
 * How a question is answered on the page.
 *
 * `options`: it names its answers, and they are buttons. `amount`: the answer
 * is the amount the account was charged, typed under the question and put in
 * the amount field. `text`: the answer is the person's own words, typed under
 * the question and sent to the model.
 */
export type AnswerWay = 'options' | 'amount' | 'text';

export function howToAnswer(question: SuggestionQuestion): AnswerWay {
  if (question.options.length > 0) return 'options';
  if (question.key === AMOUNT_KEY) return 'amount';
  return 'text';
}

/**
 * Add an answer, replacing an earlier one to the same question.
 *
 * The newest answer to a question is the answer. Kept in the order given,
 * because the model reads them in that order.
 */
export function remember(answers: SuggestionAnswer[], answer: SuggestionAnswer): SuggestionAnswer[] {
  const others = answers.filter((given) => answer.key === null || given.key !== answer.key);
  return [...others, answer].slice(-MAX_ANSWERS);
}

/** The questions still to show: those whose key has not been answered. */
export function unanswered(
  questions: SuggestionQuestion[],
  answers: SuggestionAnswer[],
): SuggestionQuestion[] {
  const answered = new Set(answers.map((answer) => answer.key));
  return questions.filter((question) => !answered.has(question.key));
}

/**
 * The questions still to ask: not answered, and not passed over.
 *
 * `skipped` holds the keys passed over with «Hopp over». They stay unanswered
 * and are not sent, but they are not asked again either: the person has said
 * they will not answer, and a question that came back after every proposal
 * would be the box that never goes away.
 */
export function stillOpen(
  questions: SuggestionQuestion[],
  answers: SuggestionAnswer[],
  skipped: ReadonlySet<string>,
): SuggestionQuestion[] {
  return unanswered(questions, answers).filter((question) => !skipped.has(question.key));
}

/**
 * The line over a question: where it stands among all asked on this bilag.
 *
 * `before` is how many earlier rounds asked, so a question the next proposal
 * adds is «Spørsmål 3 av 3» and not a second «1 av 1». A lone question is just
 * «Spørsmål»: «1 av 1» counts nothing.
 */
export function countLabel(before: number, at: number, length: number): string {
  const of = before + length;
  return of <= 1 ? 'Spørsmål' : `Spørsmål ${before + at + 1} av ${of}`;
}

/**
 * What the button under a written answer says.
 *
 * «Neste» while questions follow, which is what says there are more. On the
 * last, «Send svar» when the answers then go to the model, and «Ferdig» when
 * nothing does: an amount goes in the amount field, and a button saying «send»
 * would promise a wait that never comes.
 */
export function nextLabel(at: number, length: number, sends: boolean): string {
  if (at < length - 1) return 'Neste';
  return sends ? 'Send svar' : 'Ferdig';
}

/**
 * The answers that still hold after the person turned the direction around.
 *
 * Money in or money out decides which categories exist at all, so an answer
 * that picked a category, a VAT code or whether something is an asset was an
 * answer about the other side of the ledger. What the person said about the
 * payment itself (the model's question) and the amount still stand.
 */
export function afterDirectionChange(answers: SuggestionAnswer[]): SuggestionAnswer[] {
  return answers.filter((answer) => answer.key === MODEL_KEY || answer.key === AMOUNT_KEY);
}
