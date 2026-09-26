"""What the model is told about the company, and what it must never be told.

The tests below split cleanly in two. The first half is about the block being
useful — the formål, the aktivitet and the user's sentence reaching the prompt
in a shape a model can act on. The second half is about it being *bounded*: it
shares a context window with the bilag, and a formål long enough to push the
invoice out of that window would make the feature worse than its absence.
"""

from __future__ import annotations

from myntos.domain.bilag import classification
from myntos.domain.bilag.context import (
    MAX_CONTEXT_CHARS,
    MAX_NOTE_CHARS,
    NO_CONTEXT,
    BilagContext,
)
from myntos.domain.ledger.cashflow import CashFlowDirection


def test_nothing_known_renders_nothing():
    """An absent context has to reproduce the prompt we sent before it existed."""
    assert NO_CONTEXT.is_empty
    assert classification.preamble(NO_CONTEXT) == ""
    assert classification.preamble(None) == ""


def test_the_register_answer_reaches_the_prompt():
    context = BilagContext.build(
        purpose=["Utvikling og salg av programvare", "og det som står i forbindelse med dette"],
        activity=["Utvikling av programvare"],
        industry_code="62.010",
        industry_description="Programmeringstjenester",
        vat_registered=True,
    )
    rendered = context.render()
    assert "Vedtektsfestet formål: Utvikling og salg av programvare" in rendered
    assert "Registrert aktivitet: Utvikling av programvare" in rendered
    assert "Næringskode: 62.010 Programmeringstjenester" in rendered
    assert "Mva-registrert: ja" in rendered


def test_not_being_in_the_vat_register_is_said_plainly():
    """A company outside the register may not reclaim input VAT.

    Rendered as a sentence rather than as a missing line, because "nothing was
    said about VAT" and "this company cannot deduct it" want different answers.
    """
    rendered = BilagContext.build(vat_registered=False).render()
    assert "nei, ikke i Merverdiavgiftsregisteret" in rendered


def test_an_unknown_vat_status_says_nothing_rather_than_guessing():
    assert "Mva-registrert" not in BilagContext.build(vat_registered=None).render()


def test_the_users_note_is_labelled_as_the_users():
    """The state's register and a sentence somebody typed are not equal evidence.

    Labelling them apart is what lets the model weigh them apart; run together
    in one paragraph, a claim in the box reads exactly like a fact from
    Brønnøysund.
    """
    rendered = BilagContext.build(
        purpose=["Kjøp og salg av datautstyr"],
        note="kjøpt powersupply, la ut 3000 av pengene privat",
    ).render()
    assert "Brukerens egen forklaring på bilaget: kjøpt powersupply" in rendered
    assert rendered.index("Vedtektsfestet formål") < rendered.index("Brukerens egen")


def test_the_note_is_clipped():
    context = BilagContext.build(note="x" * (MAX_NOTE_CHARS * 3))
    assert context.note is not None
    assert len(context.note) == MAX_NOTE_CHARS


def test_the_whole_block_is_clipped_even_when_every_part_fits():
    """Two ceilings, not one sum.

    A later field could otherwise widen the total silently, and the total is
    what competes with the bilag for the model's attention.
    """
    context = BilagContext.build(
        purpose=["a" * 1000],
        activity=["b" * 1000],
        industry_code="62.010",
        industry_description="c" * 500,
        vat_registered=True,
        note="d" * 1000,
    )
    assert len(context.render()) <= MAX_CONTEXT_CHARS


def test_whitespace_is_collapsed_so_a_pasted_paragraph_costs_what_it_says():
    context = BilagContext.build(note="  linje  én\n\n\n   linje to   ")
    assert context.note == "linje én linje to"


def test_the_context_goes_above_the_document_and_stays_out_of_it():
    """The separation the whole design rests on.

    `excerpt` is the document, and the document is what every quoted answer is
    checked against. If the context were folded into it, a model repeating the
    user's sentence back would have that answer accepted as something the bilag
    said.
    """
    context = BilagContext.build(note="Kjøpt fra Oppdiktet Leverandør AS")
    menu = classification.build_menu(CashFlowDirection.UT)
    prompt = classification.build_prompt("Totalt 315,00 kr", menu, context=context)

    assert "Oppdiktet Leverandør AS" in prompt
    assert "Oppdiktet Leverandør AS" not in classification.excerpt("Totalt 315,00 kr")
    # And it precedes the document, so the model reads the bilag already knowing
    # whose books it is filing it in.
    assert prompt.index("Oppdiktet") < prompt.index("Totalt 315,00 kr")


def test_an_empty_note_is_the_same_as_no_note():
    """A user who tabs through the box must not change the prompt."""
    assert BilagContext.build(note="   ").is_empty
    assert BilagContext.build(note=None).is_empty
