"""Answering the register's questions without reading the bilag again.

A proposal comes back with questions (:mod:`myntos.domain.bilag.questions`),
and an answer used to mean starting over: the browser sent every file again,
the photographs were converted again, the model was handed the whole bilag
again with the answer folded into the sentence at the top, and an app's name
was looked for in the pixels again. The text of each file was remembered
(:mod:`myntos.domain.bilag.reading_cache`), but everything around it was not,
and an answer took most of the time the first read did.

It also lost the thread. The model was never told which question it had asked,
so a user who answered «la ut privat» could be asked the same question again,
which was the report on 2026-10-17: *why repeat the question?*

So an answer continues the conversation instead. The two questions the first
read put to the model (which category, and what the document says) are kept
with the model's answers to them, as :class:`Exchange`, and the follow-up is
those two turns repeated exactly, then one new turn saying what the user
answered (:func:`render`). Two things follow from repeating them exactly:

**The model knows what it asked.** It sees its own question and the answer
under it, and is told not to ask again what has been answered.

**The model server does not read the bilag again.** Løynd keeps the work it did
on a prompt and reuses it for any later prompt that begins the same way
(vLLM's prefix cache). The bilag is nearly all of the first question, so a
follow-up that starts byte for byte where the first one did pays for the new
turn and the answer, not for the document. That is why the follow-up is always
the first exchange plus one turn, never a growing chat: every answer the user
has given goes in that one turn, so the part that is reused never changes.

Nothing about what is believed changes. The follow-up is parsed by the same
functions under the same rules, so a date or a seller still has to be quoted
from the documents, and an answer typed by the user can put words in neither.
"""

from __future__ import annotations

from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from typing import Protocol

#: How many answers one follow-up carries. A bilag is asked at most three
#: questions at a time; this leaves room for several rounds without letting a
#: caller fill the model's window with them.
MAX_ANSWERS = 12

#: Longest a question or an answer is repeated to the model. An answer is a
#: button's label or a sentence of the description, which is 255 at most.
MAX_QUESTION_CHARS = 400
MAX_ANSWER_CHARS = 255


@dataclass(frozen=True)
class Exchange:
    """One question put to the model, and its answer, exactly as they were."""

    system: str
    prompt: str
    reply: str


@dataclass(frozen=True)
class Answer:
    """What the user said to one question.

    ``key`` is the question's own (``questions.MODEL``, ``questions.ASSET`` …),
    which is what keeps an answered question from being asked again. The text
    of the question goes to the model with the answer, so it knows what the
    answer was an answer to.
    """

    question: str
    answer: str
    key: str | None = None


class AskAfter(Protocol):
    """Something that continues a conversation with a model.

    ``history`` is the earlier turns, as ``(question, answer)`` pairs, oldest
    first; ``prompt`` is the new question. :func:`myntos.integrations.loynd.complete`
    in the running application.
    """

    def __call__(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int,
        history: Sequence[tuple[str, str]],
    ) -> Awaitable[str]: ...


def _clip(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit]


def render(answers: Sequence[Answer], *, note: str | None = None) -> str:
    """What the user has said since the bilag was read, as the model reads it.

    Every answer so far, not just the newest: the follow-up is always the first
    exchange plus this one turn (see the module docstring), so this turn is the
    only place an earlier answer can be. ``note`` is the description box as it
    stands now, which the user may have rewritten since the first read.

    Labelled as the user's, in the same words :mod:`myntos.domain.bilag.context`
    uses for the note, because the model has to keep telling the two sources
    apart: what the documents say, and what the person uploading them says.
    """
    lines: list[str] = []
    kept = [answer for answer in answers if answer.answer.strip()][:MAX_ANSWERS]
    if kept:
        lines.append("Brukeren har svart på spørsmål om bilaget:")
        lines.extend(
            f"- Spørsmål: «{_clip(answer.question, MAX_QUESTION_CHARS)}» "
            f"Svar: «{_clip(answer.answer, MAX_ANSWER_CHARS)}»"
            for answer in kept
        )
    if note is not None and note.strip():
        lines.append(
            f"Brukerens egen forklaring på bilaget er nå: «{_clip(note, MAX_ANSWER_CHARS)}»"
        )
    return "\n".join(lines)


def folded(answers: Sequence[Answer], *, note: str | None = None) -> str | None:
    """The same, as one note, for a model that has no first exchange to continue.

    That happens when the first question was never answered: Løynd was down for
    it, or the bilag had no text to ask about. The answers then go where the
    user's sentence goes on a first read, in the context above the document.
    """
    parts = [
        f"{_clip(answer.question, MAX_QUESTION_CHARS)} {_clip(answer.answer, MAX_ANSWER_CHARS)}"
        for answer in answers[:MAX_ANSWERS]
        if answer.answer.strip()
    ]
    if note is not None and note.strip():
        parts.insert(0, note.strip())
    joined = " ".join(parts)
    return joined or None
