"""What the register still needs to know, phrased as a question.

A warning tells the user something is wrong. A question tells them what to type.
The difference matters here because the register already has somewhere for the
answer to go: the "Beskriv bilaget" box that
:mod:`myntos.domain.bilag.context` feeds to the model. Answer the question,
re-read, and the proposal comes back better. Without a question that box is a
blank field with no prompt, and a blank field with no prompt gets left blank.

**Most of these are not asked of a model.** The register knows perfectly well
when it failed to find a total, when a document printed two VAT rates, and when
an amount crossed the driftsmiddel threshold — those are facts about its own
reading, and asking a language model to notice them would be asking it for
something the software already determined. Each question below therefore comes
from a condition in code, and says exactly which one.

The model contributes **two**, and in neither case does it write the question.

The first is a choice between two categories, and it comes from the model naming
a runner-up on the menu it was given (see
:class:`myntos.domain.bilag.classification.Classification`). The two numbers are
its; both sentences around them are ours. This is the question the "Batteri"
invoice needed: one word on an item line, filed under motoring, with nothing
anywhere on the page to say whether that is right.

The second is prose, and it is the one thing here the model actually composes:
the thing the documents do not say at all. That is the genuinely open case — a
bank transfer of 3 000 kroner from a private account, where the paper is
identical whether it was an owner's deposit, a repaid loan or a sale — and it is
the one case where no condition in this module could know what to ask. It
arrives through :mod:`myntos.domain.bilag.extraction`, on the same round trip as
everything else the model is asked, and is checked before it is shown.

Three rules:

**A question is asked once and answered by the user.** Nothing here changes a
field, and nothing here is stored. The answer goes in the box, the documents are
read again, and the proposal that comes back is built the same way every other
proposal is.

**Ask about what would change the entry.** A missing invoice number is not worth
a question: it is prose in a description. A missing amount, an unknown or
doubtful category, an ambiguous direction and a mixed VAT rate each decide an
account, a posting or a deduction, and those are worth interrupting for.

**Few, and most-consequential first.** A form that opens with six questions is a
form nobody answers. :data:`MAX_QUESTIONS` is three.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from myntos.domain.bilag.interpretation import (
    CAPITALISATION_THRESHOLD,
    InvoiceReading,
    InvoiceSuggestion,
)
from myntos.domain.ledger import landbruk
from myntos.domain.ledger.cashflow import (
    CASH_FLOW_TYPES,
    CashFlowDirection,
    CashFlowType,
    get_type,
)

#: How many are shown. Three is what fits above a text box without the box
#: looking like an exam.
MAX_QUESTIONS = 3

#: Longest a question may be. One sentence — anything longer is an explanation,
#: and the explanation belongs in the warning next to it.
MAX_QUESTION_CHARS = 160


@dataclass(frozen=True)
class Question:
    """One thing the register would like answered before this is posted.

    ``field`` names the form field the answer would settle, so the interface can
    put the question beside it rather than in a list at the top. ``asked_by_model``
    separates the one question that was composed from the ones this module
    derived — the same distinction ``sources`` draws for every proposed value,
    and for the same reason: the two do not deserve equal trust.
    """

    text: str
    field: str | None = None
    asked_by_model: bool = False


def _written(value: Decimal) -> str:
    """A number the Norwegian way: ``25 278,00``, thousands non-breaking."""
    # The same spelling myntos.domain.bilag.interpretation._written uses, so a
    # figure quoted in a question reads identically to the one in the field.
    return f"{value:,.2f}".replace(",", "\u00a0").replace(".", ",")


def _never_an_asset(type_code: str | None) -> bool:
    entry = CASH_FLOW_TYPES.get(type_code) if type_code is not None else None
    if entry is None:
        return False
    if entry.reverses is not None or entry.deducts_from_sale:
        return True
    return entry.segment == landbruk.SEGMENT and entry.account_number.startswith("4")


def compose(
    suggestion: InvoiceSuggestion,
    reading: InvoiceReading,
    *,
    model_question: str | None = None,
    alternative: CashFlowType | None = None,
    has_note: bool = False,
) -> list[Question]:
    """What to ask, in the order it is worth asking.

    ``has_note`` says whether the user already wrote something. It does not
    suppress the questions — their answer may well not be in what was written —
    but it does change the one about the direction, because asking "what is this
    payment?" of somebody who has just told you is how a form loses trust.

    ``alternative`` is the category the model named as its runner-up, from
    :class:`myntos.domain.bilag.classification.Classification`. It is the one
    question here that comes out of the model's own uncertainty rather than out
    of a condition in code, and it is still ours to phrase: the model supplies
    two menu numbers and this module supplies the sentence, so what the user
    reads is two category names they recognise from the dropdown next to it.
    """
    asked: list[Question] = []

    # 1. The amount. Nothing can be posted without it, so it outranks
    #    everything else that might be missing.
    if suggestion.gross_amount is None:
        if suggestion.foreign_amount is not None and suggestion.currency_code:
            asked.append(
                Question(
                    text=(
                        f"Bilaget er på {_written(suggestion.foreign_amount)} "
                        f"{suggestion.currency_code}. Hvor mange kroner ble faktisk "
                        "belastet kontoen?"
                    ),
                    field="gross_amount",
                )
            )
        else:
            asked.append(
                Question(
                    text="Vi fant ikke totalbeløpet. Hvilket beløp ble belastet kontoen?",
                    field="gross_amount",
                )
            )

    # 2. Money in from somebody who is not a registered customer. The paper is
    #    the same for three very different postings, and this is the question
    #    the `finn/` fixture exists to demonstrate: an owner covering a purchase
    #    out of pocket looks exactly like a sale on a bank statement.
    #
    #    Not on a settlement the buyer wrote: the document has already said it
    #    is a sale, and asking a farmer whether the dairy's melkeoppgjør might
    #    be an owner's deposit reads as not having read it.
    elif (
        suggestion.direction is CashFlowDirection.INN
        and suggestion.party_id is None
        and not suggestion.issued_by_buyer
        and not has_note
    ):
        asked.append(
            Question(
                text=(
                    "Pengene kom inn fra noen som ikke er registrert som kunde. Er dette "
                    "innskudd fra eier, tilbakebetalt utlegg, et lån eller et salg?"
                ),
                field="type_code",
            )
        )

    # 3. The category the model was torn about. High up, because it decides the
    #    account, the næringsspesifikasjon post and — through the type's default
    #    — whether input VAT is deducted at all, and because a wrong category
    #    proposed confidently is the one error a user is least likely to catch:
    #    the field is filled, so it reads as read rather than as guessed. The
    #    invoice that occasioned it says "Batteri" and nothing else, and
    #    "Varekjøp or Drivstoff og bilkostnad?" is genuinely undecidable from
    #    the paper.
    if (
        alternative is not None
        and suggestion.type_code is not None
        and alternative.code != suggestion.type_code
    ):
        asked.append(
            Question(
                text=(
                    f"Er dette «{get_type(suggestion.type_code).label}» eller "
                    f"«{alternative.label}»? Skriv gjerne hva som ble kjøpt."
                ),
                field="type_code",
                # Composed here, but the doubt behind it is the model's, and the
                # interface says so for the same reason it does for a value: the
                # user is owed the difference between what we read and what was
                # guessed.
                asked_by_model=True,
            )
        )

    # 4. The category, which decides the account and through it the
    #    næringsspesifikasjon post. Asked only when neither the keyword reading
    #    nor the model produced one — otherwise there is a proposal to correct
    #    rather than a blank to fill.
    if suggestion.type_code is None:
        asked.append(
            Question(
                text="Hva ble kjøpt, og hva bruker selskapet det til?",
                field="type_code",
            )
        )

    # 5. Several VAT rates on one bilag. One entry carries one code, so either
    #    the parts belong to separate entries or one rate governs.
    if suggestion.tax_code is None and len(reading.printed_rates) > 1:
        shown = " og ".join(f"{int(rate * 100)} %" for rate in reading.printed_rates)
        asked.append(
            Question(
                text=(
                    f"Bilaget viser {shown} mva. Gjelder satsene ulike deler av kjøpet, "
                    "eller er hele beløpet på én sats?"
                ),
                field="tax_code",
            )
        )

    # 6. Over the driftsmiddel threshold. The threshold is **per asset**, and a
    #    single payment for two graphics cards at 12 639 each is over it while
    #    neither card is — which is precisely the `utlegg/` fixture. The register
    #    cannot tell one expensive thing from two ordinary ones, and the person
    #    who bought them can.
    #
    #    Not for what can never be an asset: a farm's feed, fertiliser and vet,
    #    which are used up within the season and come in invoices over the
    #    threshold every month (the pilot farm's first Coop samlefaktura, one
    #    month of kraftfôr, was asked whether it was one item), nor a kreditnota
    #    or a deduction on a settlement, which are not purchases at all.
    if (
        suggestion.gross_amount is not None
        and suggestion.gross_amount >= CAPITALISATION_THRESHOLD
        and suggestion.direction is CashFlowDirection.UT
        and not _never_an_asset(suggestion.type_code)
    ):
        asked.append(
            Question(
                text=(
                    f"{_written(suggestion.gross_amount)} kr er over grensen for "
                    "driftsmiddel. Er dette én gjenstand, eller flere som hver for seg "
                    "er under 15 000 kr?"
                ),
                field="type_code",
            )
        )

    # 7. Last, and only if there is room: the one question the documents could
    #    not answer at all. It goes at the end because everything above is a
    #    fact about our own reading and this is a judgement about theirs.
    if model_question is not None:
        asked.append(Question(text=model_question, asked_by_model=True))

    return asked[:MAX_QUESTIONS]


def clean(value: str) -> str | None:
    """One model-composed question, or nothing.

    Checked rather than trusted, exactly as every other model answer here is.
    The bar is deliberately low and entirely mechanical — it must be a single
    short question — because there is nothing to check a question *against*:
    unlike a date or a seller, it is not a claim about the document and cannot
    be looked up in it. What makes that acceptable is that a question changes
    nothing on its own. The worst a bad one does is waste a line.
    """
    text = " ".join(value.split())
    if not text.endswith("?"):
        return None
    if not 10 <= len(text) <= MAX_QUESTION_CHARS:
        return None
    # More than one question is a list, and a list is what MAX_QUESTIONS is for.
    if text.count("?") > 1:
        return None
    return text
