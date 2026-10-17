/**
 * The questions a proposal asks, and the answers given to them on one bilag.
 *
 * Answering used to mean writing in the description box and waiting for the
 * whole bilag to be read again, and nothing on the page said so. The person
 * who reported it had answered, and was asked the same question again. Now a
 * question is answered where it is asked: with a button when it names its own
 * answers, in the description box under it when it does not, and in the
 * amount field when the answer is a number. The answer goes to the server as a
 * follow-up (`answerSuggestion`), and an answered question is not shown again.
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

/** The key of the question whose answer is a number, typed in the amount field. */
export const AMOUNT_KEY = 'belop';

/**
 * How a question is answered on the page.
 *
 * `options`: it names its answers, and they are buttons. `amount`: the answer
 * is the amount the account was charged, and the amount field is where it
 * goes. `description`: the answer is a sentence, and it is written in the
 * description box, which is also what the payment is filed under.
 */
export type AnswerWay = 'options' | 'amount' | 'description';

export function howToAnswer(question: SuggestionQuestion): AnswerWay {
  if (question.options.length > 0) return 'options';
  if (question.key === AMOUNT_KEY) return 'amount';
  return 'description';
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
 * The answers a sentence in the description box gives to the questions on screen.
 *
 * Only the questions answered with a sentence. One with buttons is answered by
 * pressing one: somebody correcting a word in the description has not thereby
 * said whether 31 000 kroner was one thing or several, and taking the edit as
 * an answer would quietly drop the question. The amount has its own field.
 */
export function answeredByText(
  questions: SuggestionQuestion[],
  text: string,
): SuggestionAnswer[] {
  const sentence = text.trim();
  if (sentence === '') return [];
  return questions
    .filter((question) => howToAnswer(question) === 'description')
    .map((question) => ({ key: question.key, question: question.text, answer: sentence }));
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
