"""What the invoice form does with a model's proposed buyer, terms and lines.

Same stance as ``test_bilag_classification``: the model is never trusted to be
available, correct or coherent, and every test here is about our side of the
conversation. The proposal reaches a draft only through a person, so what
matters is that a bad answer costs the user a blank field rather than a wrong
number, or a stranger's name, on a document they then issue.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import pytest

from myntos.domain.faktura import drafting
from myntos.domain.faktura.catalogue import SALES_TAX_OPTIONS

SALE = "Solgte 10 timer rådgivning i mars til 1 500 kroner timen, pluss to kursmapper à 450."
TODAY = date(2026, 10, 23)

# The shape of a real e-mail a customer sent with their invoice details
# (2026-10-23), with the names, the address and the number replaced. It has a
# buyer, an orgnr, an invoice address over four lines, what to mark the invoice
# with, where to send it, a greeting to the seller, a signature and a «|» of its
# own, and no sale at all: the lines come from the second box.
CUSTOMER_EMAIL = """Hei Ola,

Beklager treghet fra meg!
Her kommer faktura info:

Faktura sendes til faktura@fjordaluminium.no


Fakturaadresse:

Fjord Aluminium AS

Avdeling Nord

Postboks 53

6601 Sunndalsøra


Org.nr:
910000039.



Faktura merkes:

GS-HR00003-02-05, Att: Kari Nordmann

Og her finner dere logo, her kan dere velge den filtypen som passer best: Primary logo | Fjord
Er det noe mer du trenger fra meg nå? 🙂

Beste hilsen,

Kari
"""

# What the live model answered for the real e-mail, names replaced the same way.
CUSTOMER_EMAIL_REPLY = """kunde: Fjord Aluminium AS
adresse: Postboks 53, 6601 Sunndalsøra
referanse: GS-HR00003-02-05, Att: Kari Nordmann
leveringsdato: -
leveringssted: -
forfall: -
"""


class Answering:
    """A stand-in for the model that always says the same thing."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def __call__(self, *, system: str, prompt: str, max_tokens: int) -> str:
        del system, max_tokens
        self.prompts.append(prompt)
        return self.reply


def menu() -> tuple[drafting.TaxChoice, ...]:
    return drafting.build_tax_menu()


# --- The menu ---------------------------------------------------------------------


def test_the_menu_offers_every_sellable_treatment_exactly_once() -> None:
    choices = menu()

    assert [choice.number for choice in choices] == list(range(1, len(SALES_TAX_OPTIONS) + 1))
    assert {choice.tax_code for choice in choices} == {o.tax_code for o in SALES_TAX_OPTIONS}


def test_a_company_outside_the_vat_register_is_offered_no_taxable_rate() -> None:
    """§ 15-14: charging output VAT unregistered is not a formality to catch later."""
    choices = drafting.build_tax_menu(("0",))

    assert [choice.tax_code for choice in choices] == ["0"]


def test_a_code_outside_the_catalogue_cannot_be_asked_onto_the_menu() -> None:
    """``1`` is inngående mva. A caller asking for it gets the full menu, not it."""
    assert {choice.tax_code for choice in drafting.build_tax_menu(("3", "1"))} == {"3"}


def test_the_prompt_holds_the_description_the_format_and_the_menu() -> None:
    prompt = drafting.build_prompt(SALE, menu(), today=TODAY, seller_name="Nordmann AS")

    assert "10 timer rådgivning" in prompt
    assert "kunde: " in prompt
    assert "beskrivelse | antall | enhet | pris per enhet | mva" in prompt
    assert "1. 25 % (alminnelig sats)" in prompt
    assert prompt.rstrip().endswith("Svar med kun linjene.")


def test_the_prompt_says_who_is_selling_and_what_day_it_is() -> None:
    """In a customer's e-mail the greeting is to the seller, who must not become the buyer."""
    prompt = drafting.build_prompt(SALE, menu(), today=TODAY, seller_name="Nordmann AS")

    assert "Selgeren, som skal sende fakturaen, er Nordmann AS." in prompt
    assert "I dag er 2026-10-23." in prompt


def test_only_the_first_part_of_a_long_description_is_sent() -> None:
    prompt = drafting.build_prompt("x" * 9000, menu(), today=TODAY)

    assert "x" * drafting.MAX_REQUEST_CHARS in prompt
    assert "x" * (drafting.MAX_REQUEST_CHARS + 1) not in prompt


# --- Reading the answer -------------------------------------------------------------


def test_a_well_formed_answer_becomes_lines() -> None:
    proposal = drafting.parse_lines(
        "Rådgivning mars | 10 | timer | 1 500,00 | 1\nKursmappe | 2 | stk | 450 | 1",
        menu(),
    )

    assert [line.description for line in proposal.lines] == ["Rådgivning mars", "Kursmappe"]
    first = proposal.lines[0]
    assert first.quantity == Decimal(10)
    assert first.unit_code == "HUR"
    assert first.unit_price == Decimal("1500.00")
    assert first.tax_code == "3"
    assert proposal.warnings == []


@pytest.mark.parametrize(
    ("word", "code"),
    [("time", "HUR"), ("timer", "HUR"), ("stk", "EA"), ("måned", "MON"), ("mnd", "MON")],
)
def test_the_unit_is_read_as_the_word_a_person_would_write(word: str, code: str) -> None:
    assert drafting.unit_for(word) == code


def test_an_unknown_unit_falls_back_rather_than_losing_the_line() -> None:
    proposal = drafting.parse_lines("Rådgivning | 3 | klumper | 900 | 1", menu())

    assert proposal.lines[0].unit_code == "EA"
    assert proposal.lines[0].description == "Rådgivning"


def test_prose_around_the_answer_is_ignored_without_costing_a_line() -> None:
    reply = (
        "Her er fakturalinjene:\n"
        "- Rådgivning mars | 10 | timer | 1500 | 1\n"
        "\n"
        "Si fra om du vil ha dem satt opp annerledes."
    )
    proposal = drafting.parse_lines(reply, menu())

    assert len(proposal.lines) == 1
    assert proposal.warnings == []


def test_the_echoed_format_line_is_not_a_sale() -> None:
    reply = "beskrivelse | antall | enhet | pris | mva\nRådgivning | 10 | timer | 1500 | 1"
    proposal = drafting.parse_lines(reply, menu())

    assert [line.description for line in proposal.lines] == ["Rådgivning"]


def test_a_row_with_the_wrong_number_of_fields_is_dropped_and_counted() -> None:
    """Which field is missing is unknowable, and guessing would misplace a price."""
    proposal = drafting.parse_lines("Rådgivning | 10 | 1500 | 1", menu())

    assert proposal.lines == []
    assert any("1 av linjene" in warning for warning in proposal.warnings)


def test_an_unreadable_price_keeps_the_line_and_says_so() -> None:
    """The description is the part the user would otherwise have to retype."""
    proposal = drafting.parse_lines("Rådgivning mars | 10 | timer | etter avtale | 1", menu())

    assert proposal.lines[0].unit_price == Decimal(0)
    assert any("prisen" in warning for warning in proposal.warnings)


def test_a_missing_quantity_becomes_one() -> None:
    proposal = drafting.parse_lines("Nettside | | stk | 25000 | 1", menu())

    assert proposal.lines[0].quantity == Decimal(1)


def test_a_vat_number_outside_the_menu_falls_back_and_is_flagged() -> None:
    proposal = drafting.parse_lines("Rådgivning | 1 | stk | 1000 | 99", menu())

    assert proposal.lines[0].tax_code == SALES_TAX_OPTIONS[0].tax_code
    assert any("Mva-satsen" in warning for warning in proposal.warnings)


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("1", "3"),  # the menu number, which is what was asked for
        ("25%", "3"),  # the rate, which is what a model shown "1. 25 %" often answers
        ("25 %", "3"),
        ("25", "3"),  # a bare rate: no menu entry has that number
        ("mva 2", "31"),  # menu number inside prose
        ("12 %", "33"),
        ("0 %", "5"),  # the literal reading: avgiftsfritt salg
    ],
)
def test_the_vat_treatment_is_read_as_a_menu_number_or_as_a_rate(field: str, code: str) -> None:
    """Observed against the real model, which answers "25%" rather than "1"."""
    chosen = drafting.tax_for(field, menu())

    assert chosen is not None
    assert chosen.tax_code == code


def test_a_percent_sign_means_the_number_is_a_rate_and_not_a_choice() -> None:
    """Otherwise "3 %" would silently become menu entry 3, which is 12 %."""
    assert drafting.tax_for("3 %", menu()) is None
    assert drafting.tax_for("3", menu()) is not None


def test_a_rate_is_only_read_from_the_menu_on_offer() -> None:
    """An unregistered company answering "25 %" gets no taxable line out of it."""
    restricted = drafting.build_tax_menu(("0",))

    assert drafting.tax_for("25 %", restricted) is None
    assert drafting.tax_for("1", restricted) == restricted[0]


def test_numbers_are_rounded_to_what_the_columns_hold() -> None:
    """Otherwise the service layer refuses the line and blames the user for it."""
    proposal = drafting.parse_lines("Strøm | 1,1234567 | stk | 0,123456 | 1", menu())

    assert proposal.lines[0].quantity == Decimal("1.123457")
    assert proposal.lines[0].unit_price == Decimal("0.1235")


def test_an_absurd_quantity_is_not_a_quantity() -> None:
    proposal = drafting.parse_lines("Rådgivning | 99999999 | timer | 1500 | 1", menu())

    assert proposal.lines[0].quantity == Decimal(1)


def test_no_more_lines_come_back_than_we_take() -> None:
    reply = "\n".join(f"Vare {n} | 1 | stk | 100 | 1" for n in range(drafting.MAX_LINES + 5))
    proposal = drafting.parse_lines(reply, menu())

    assert len(proposal.lines) == drafting.MAX_LINES
    assert any("5 av linjene" in warning for warning in proposal.warnings)


# --- Asking ---------------------------------------------------------------------------


async def test_the_answer_comes_back_as_a_proposal() -> None:
    model = Answering("Rådgivning mars | 10 | timer | 1500 | 1")
    proposal = await drafting.draft_invoice(SALE, ask=model, today=TODAY)

    assert len(proposal.lines) == 1
    assert "10 timer rådgivning" in model.prompts[0]


async def test_an_empty_description_is_not_worth_asking_about() -> None:
    model = Answering("Noe | 1 | stk | 100 | 1")

    assert await drafting.draft_invoice("   \n", ask=model, today=TODAY) == (
        drafting.InvoiceProposal()
    )
    assert model.prompts == []


async def test_an_answer_that_is_not_lines_leaves_the_form_to_the_user() -> None:
    model = Answering("Jeg er en språkmodell og kan dessverre ikke hjelpe med dette.")
    proposal = await drafting.draft_invoice(SALE, ask=model, today=TODAY)

    assert proposal.lines == []
    assert proposal.warnings[0].startswith("Vi fikk ikke laget fakturalinjer")


async def test_a_restricted_menu_is_the_one_the_model_is_shown() -> None:
    model = Answering("Klipp | 1 | stk | 500 | 1")
    proposal = await drafting.draft_invoice(SALE, ask=model, today=TODAY, allowed_tax_codes=("0",))

    assert "25 % (alminnelig sats)" not in model.prompts[0]
    assert proposal.lines[0].tax_code == "0"


async def test_a_gross_price_in_the_description_is_pointed_out() -> None:
    """A line priced inclusive of VAT is 25 % too high, and nothing else would say so."""
    model = Answering("Klipp | 1 | stk | 500 | 1")
    proposal = await drafting.draft_invoice("Klipp til 500 kr inkl. mva", ask=model, today=TODAY)

    assert any("inkludert mva" in warning for warning in proposal.warnings)


# --- The buyer and the terms ----------------------------------------------------------


def header(reply: str, text: str, **kwargs: object) -> drafting.DraftedHeader:
    found, _ = drafting.parse_header(reply, text=text, today=TODAY, **kwargs)  # type: ignore[arg-type]
    return found


async def test_a_customers_email_fills_in_the_buyer_and_proposes_no_lines() -> None:
    """The real case: the customer's invoice details, and nothing about the sale."""
    model = Answering(CUSTOMER_EMAIL_REPLY)
    proposal = await drafting.draft_invoice(
        CUSTOMER_EMAIL,
        ask=model,
        today=TODAY,
        seller_name="Ola Rådgivning AS",
        seller_orgnr="910000004",
    )

    assert proposal.header == drafting.DraftedHeader(
        buyer_name="Fjord Aluminium AS",
        buyer_orgnr="910000039",
        buyer_address_line="Postboks 53",
        buyer_postal_code="6601",
        buyer_city="Sunndalsøra",
        invoice_email="faktura@fjordaluminium.no",
        buyer_reference="GS-HR00003-02-05, Att: Kari Nordmann",
    )
    assert proposal.lines == []
    # The address to send it to is said, since nothing on the invoice holds it.
    assert any("faktura@fjordaluminium.no" in w for w in proposal.header_warnings)
    # The «Primary logo | Fjord» in the e-mail never reached the answer, so no
    # line was dropped either: the only line warning is that there are none.
    assert len(proposal.warnings) == 1


def test_the_e_mails_blank_lines_are_not_sent() -> None:
    """A mail client puts one after every line; they are room in the prompt and nothing else."""
    assert "\n\n" not in drafting.sent_text(CUSTOMER_EMAIL)


def test_a_buyer_the_text_never_names_is_not_proposed() -> None:
    """A model asked for a customer will produce one. A plausible stranger is the worst answer."""
    found = header("kunde: Bygg og Anlegg AS", SALE)

    assert found.buyer_name is None


def test_the_legal_form_the_model_added_is_left_off() -> None:
    """«Bygg» in the sentence is the buyer; «Bygg AS» from the model is that, not more."""
    found = header("kunde: Bygg AS", "10 timer rådgivning til Bygg, 1500 timen")

    assert found.buyer_name == "Bygg"


def test_the_orgnr_after_the_name_is_not_part_of_the_name() -> None:
    text = "Faktura til Fjord Aluminium AS, org.nr 910000039, for 3 dager"
    found = header("kunde: Fjord Aluminium AS (910000039)", text)

    assert found.buyer_name == "Fjord Aluminium AS"
    assert found.buyer_orgnr == "910000039"


def test_the_seller_is_never_the_buyer() -> None:
    """The greeting in a customer's e-mail is to the seller."""
    text = "Hei Nordmann Consulting AS, send faktura til Fjord Aluminium AS"
    found = header("kunde: Nordmann Consulting", text, seller_name="Nordmann Consulting AS")

    assert found.buyer_name is None


def test_the_orgnr_is_read_from_the_text_and_never_from_the_model() -> None:
    """The check digit reads it; a number the model wrote that the text lacks is ignored."""
    found = header("kunde: Fjord Aluminium AS\norgnr: 910000047", "Fjord Aluminium AS 910 000 039")

    assert found.buyer_orgnr == "910000039"


def test_the_sellers_own_orgnr_is_not_the_buyers() -> None:
    text = "Fra 910000004 til Fjord Aluminium AS, org.nr. 910000039"
    found = header("", text, seller_orgnr="910000004")

    assert found.buyer_orgnr == "910000039"


def test_two_orgnrs_are_not_chosen_between() -> None:
    found, said = drafting.parse_header(
        "", text="910000039 og 910000047", today=TODAY, seller_orgnr="910000004"
    )

    assert found.buyer_orgnr is None
    assert any("flere organisasjonsnumre" in warning for warning in said)


def test_an_address_over_four_lines_is_one_line_a_postnummer_and_a_place() -> None:
    found = header("adresse: Avdeling Nord, Postboks 53, 6601 Sunndalsøra", CUSTOMER_EMAIL)

    assert found.buyer_address_line == "Avdeling Nord, Postboks 53"
    assert found.buyer_postal_code == "6601"
    assert found.buyer_city == "Sunndalsøra"


def test_a_part_of_the_address_the_text_lacks_is_dropped_and_the_rest_kept() -> None:
    found = header("adresse: Storgata 9, 6601 Sunndalsøra", CUSTOMER_EMAIL)

    assert found.buyer_address_line is None
    assert (found.buyer_postal_code, found.buyer_city) == ("6601", "Sunndalsøra")


def test_the_label_in_front_of_a_reference_is_not_the_reference() -> None:
    text = "Faktura merkes: PO-7781"
    found = header("referanse: Faktura merkes: PO-7781", text)

    assert found.buyer_reference == "PO-7781"


@pytest.mark.parametrize(
    ("text", "answer", "expected"),
    [
        ("levert 14. oktober", "2026-10-14", date(2026, 10, 14)),
        ("levert 14.10", "2026-10-14", date(2026, 10, 14)),
        ("levert i går", "2026-10-22", date(2026, 10, 22)),
        ("levert i dag", "2026-10-23", date(2026, 10, 23)),
        # Asked for one day, a model turns a month into its last.
        ("10 timer i mars", "2026-03-31", None),
        # The day is there, the month is not.
        ("14 timer rådgivning", "2026-10-14", None),
        # The text gives the year, and it is not the model's.
        ("levert 14. oktober 2025", "2026-10-14", None),
        ("levert 14. oktober", "-", None),
    ],
)
def test_a_delivery_date_is_kept_only_if_the_text_names_that_day(
    text: str, answer: str, expected: date | None
) -> None:
    assert header(f"leveringsdato: {answer}", text).delivery_date == expected


@pytest.mark.parametrize(
    ("text", "answer", "expected"),
    [
        ("forfall 30 dager", "30 dager", TODAY + timedelta(days=30)),
        ("betaling innen 10 dager", "10", TODAY + timedelta(days=10)),
        ("forfall 1. november", "2026-11-01", date(2026, 11, 1)),
        # Fourteen days is the form's own default, not something the text said.
        ("ingen betingelser nevnt", "14 dager", None),
    ],
)
def test_a_due_date_is_a_date_or_a_number_of_days_the_text_names(
    text: str, answer: str, expected: date | None
) -> None:
    assert header(f"forfall: {answer}", text).due_date == expected


def test_the_place_of_delivery_must_be_in_the_text() -> None:
    assert header("leveringssted: Bergen", "levert i Bergen").delivery_place == "Bergen"
    assert header("leveringssted: Oslo", "levert i Bergen").delivery_place is None


def test_the_address_to_send_to_is_taken_only_from_a_line_about_the_invoice() -> None:
    """A signature's address is the sender's own, not where the invoice goes."""
    signed = "Send gjerne fakturaen snart.\nMvh Kari\nkari@fjordaluminium.no"
    assert header("", signed).invoice_email is None
    assert header("", "Faktura til ap@fjord.no").invoice_email == "ap@fjord.no"


def test_a_row_about_the_buyer_is_never_counted_as_a_dropped_line() -> None:
    """A «|» in a reference must not turn into «Vi forsto ikke 1 av linjene»."""
    proposal = drafting.parse_lines("referanse: PO 1 | avd 2\nVare | 1 | stk | 100 | 1", menu())

    assert [line.description for line in proposal.lines] == ["Vare"]
    assert proposal.warnings == []


def test_what_the_model_could_not_see_it_cannot_be_said_to_have_read() -> None:
    """The check is against the text that was sent, so past the cap is never in it."""
    text = "x " * drafting.MAX_REQUEST_CHARS + "Fjord Aluminium AS"
    assert header("kunde: Fjord Aluminium AS", drafting.sent_text(text)).buyer_name is None


# --- Which registered customer ---------------------------------------------------------


@dataclass(frozen=True)
class Customer:
    name: str
    orgnr: str | None = None


REGISTER = [
    Customer("Fjord Aluminium AS", "910000039"),
    Customer("Kari Nordmann"),
    Customer("Bygg og Anlegg AS", "910000047"),
    Customer("Bygg og Rør AS"),
]


def test_the_orgnr_decides_whatever_the_name_says() -> None:
    party, how, said = drafting.match_customer(REGISTER, name="Fjord", orgnr="910000039")

    assert party == REGISTER[0]
    assert how == "orgnr"
    assert said == []


@pytest.mark.parametrize(
    "name", ["Kari Nordmann", "kari  nordmann", "Bygg og Anlegg", "BYGG OG ANLEGG AS"]
)
def test_the_whole_name_matches_give_or_take_case_and_legal_form(name: str) -> None:
    party, how, _ = drafting.match_customer(REGISTER, name=name, orgnr=None)

    assert party is not None
    assert how == "navn"


def test_part_of_one_customers_name_matches_and_says_so() -> None:
    """«Kari» for Kari Nordmann is how people write; the wrong customer costs a kreditnota."""
    party, how, said = drafting.match_customer(REGISTER, name="Kari", orgnr=None)

    assert party == REGISTER[1]
    assert how == "delvis"
    assert "Kontroller at det er riktig kunde" in said[0]


def test_part_of_two_customers_names_chooses_neither() -> None:
    party, how, said = drafting.match_customer(REGISTER, name="Bygg", orgnr=None)

    assert party is None
    assert how is None
    assert "«Bygg og Anlegg AS»" in said[0] and "«Bygg og Rør AS»" in said[0]


def test_a_short_part_of_a_name_matches_nothing() -> None:
    """«Ola» would be a third of a register."""
    register = [Customer("Ola Nordmann")]
    assert drafting.match_customer(register, name="Ola", orgnr=None) == (None, None, [])


def test_a_new_orgnr_is_a_new_buyer_even_with_a_familiar_name() -> None:
    register = [Customer("Fjord Aluminium AS")]
    party, _, said = drafting.match_customer(register, name="Fjord Aluminium AS", orgnr="910000039")

    assert party is None
    assert "uten organisasjonsnummer" in said[0]


def test_nothing_named_is_nothing_matched() -> None:
    assert drafting.match_customer(REGISTER, name=None, orgnr=None) == (None, None, [])


def test_a_new_invoice_address_for_a_registered_customer_is_pointed_out() -> None:
    """The draft prints the register's address, so a new one in the e-mail must be read."""
    said = drafting.DraftedHeader(
        buyer_address_line="Postboks 53", buyer_postal_code="6601", buyer_city="Sunndalsøra"
    )

    moved = drafting.differs_from(said, address_line="Storgata 1", postal_code="0155")
    assert moved is not None and "Postboks 53, 6601 Sunndalsøra" in moved
    assert drafting.differs_from(said, address_line="Postboks 53", postal_code="6601") is None
    # A postnummer alone says nothing about the street.
    only_code = drafting.DraftedHeader(buyer_postal_code="6601")
    assert drafting.differs_from(only_code, address_line="Storgata 1", postal_code="6601") is None
