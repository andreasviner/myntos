"""What the model is told about the company, and what the user typed.

Two questions are put to Løynd about an uploaded bilag — which category it
belongs to (:mod:`~myntos.domain.bilag.classification`) and what the document
says (:mod:`~myntos.domain.bilag.extraction`) — and until now both were asked in
a vacuum. The model saw a receipt and a menu of twenty-six categories, and
nothing at all about whose books it was filing them in.

That vacuum is expensive, and the expense is one-directional. A receipt for a
graphics card is *varekjøp* at a company that builds computers, *datakostnad* at
one that writes software, and a *privatuttak* at a hairdresser's; the document is
identical in all three cases and says nothing that would separate them. The
model was being asked a question it could not answer from the evidence it had,
and answering anyway is what a language model does.

So three things are put in front of it, all of them already known:

**Vedtektsfestet formål and aktivitet.** Enhetsregisteret holds both, Myntos
already fetches and stores them (see
:class:`~myntos.domain.ledger.models.CompanyRegisterSnapshot`), and between them
they are a description of what this business legitimately buys, written by the
business itself and filed with the state. Nothing else available to us describes
a company's costs so directly.

**Whether it is registered in Merverdiavgiftsregisteret.** Decides whether input
VAT is deductible at all, and therefore whether a proposed VAT code means
anything. A company outside the register that gets handed code 1 on a purchase
is being told it may reclaim VAT it may not.

**What the user typed.** The sentence in the entry form's description field,
sent with the upload as ``forklaring``, and the only one of the three the model
could not have got any other way. It exists because some bilag
are genuinely undecidable from the paper: a bank transfer of 3 000 kroner from a
private account into the company's is an owner's deposit, a loan repayment or a
sale, and only the person who made it knows which. A sentence resolves it.

Three rules govern all of it:

**It is context, never an answer.** Within this module the note is only shown
to the model: nothing here stores it, and it cannot set a field. Everything that
comes back is checked against the document exactly as before —
:func:`myntos.domain.bilag.extraction.parse_answer` still discards a date the
document does not contain, and the note is not part of the document. That is the
difference between helping the model read and letting the user dictate the
reading. The same sentence *is* stored and posted, but by another route: it is
the description field, so registering the payment files it as the bilagstekst,
which is what /ki and the personvernerklæring say.

**It is bounded.** Each part is truncated, because this text is prepended to a
document excerpt that already has a ceiling, and a formål paragraph long enough
to push the invoice out of the model's window would make the feature actively
harmful.

**It is optional, in every combination.** A company with no register snapshot, a
user with nothing to add, and a deployment with the model switched off all
produce an empty context and the behaviour Myntos had before this module
existed.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Ceilings per part. Small on purpose: this text competes with the bilag for
#: room in the model's window, and the bilag is the thing being read.
MAX_PURPOSE_CHARS = 400
MAX_ACTIVITY_CHARS = 300
MAX_NOTE_CHARS = 500

#: Longest the whole rendered block may be. A second ceiling rather than a sum
#: of the first three, so adding a fourth field later cannot quietly widen the
#: total that :func:`myntos.domain.bilag.classification.excerpt` has to share a
#: window with.
MAX_CONTEXT_CHARS = 1200


def _clip(value: str | None, limit: int) -> str | None:
    """One field, whitespace collapsed and cut to ``limit``. ``None`` if empty."""
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned[:limit] or None


def _joined(lines: tuple[str, ...] | list[str] | None, limit: int) -> str | None:
    """Register free-text, which arrives as a list of lines, as one sentence."""
    if not lines:
        return None
    return _clip(" ".join(str(line) for line in lines if line), limit)


@dataclass(frozen=True)
class BilagContext:
    """Everything the model is told that is not the document itself.

    Built by the API layer from the company row, its register snapshot and the
    upload form. The domain never reads a database or a request, so this arrives
    already assembled — the same arrangement as
    :class:`~myntos.domain.bilag.interpretation.PartyHint`.
    """

    #: Vedtektsfestet formål, as filed with Enhetsregisteret.
    purpose: str | None = None
    #: Registered aktivitet. Often more concrete than the formål, which tends to
    #: be drafted broadly enough to cover anything the company might ever do.
    activity: str | None = None
    #: Næringskode with its description, e.g. "62.010 Programmeringstjenester".
    industry: str | None = None
    #: Registered in Merverdiavgiftsregisteret. ``None`` means we do not know,
    #: which is said as "ukjent" rather than guessed at — an unregistered
    #: company and one we have no answer for want different proposals.
    vat_registered: bool | None = None
    #: What the user typed on the upload form.
    note: str | None = None
    #: Which way the money went, when the user said so by clicking it. Told to
    #: the model as the user's word, next to their note, because it is the same
    #: kind of statement: about this payment, by the person who made it. The
    #: category menu is narrowed to it as well (see the endpoint), so this line
    #: is about the sentence the model writes, "Kjøp av" or "Salg av".
    direction: str | None = None
    #: Whether the books have the farming add-on. Told to the model as a fact
    #: about the company, because the one thing it most needs to know about a
    #: farm's paperwork is the thing no single document says: that a settlement
    #: with Tine's name at the top is the farm's own sale.
    farm: bool = False

    @classmethod
    def build(
        cls,
        *,
        purpose: tuple[str, ...] | list[str] | None = None,
        activity: tuple[str, ...] | list[str] | None = None,
        industry_code: str | None = None,
        industry_description: str | None = None,
        vat_registered: bool | None = None,
        note: str | None = None,
        direction: str | None = None,
        farm: bool = False,
    ) -> BilagContext:
        """Assemble one, clipping every part on the way in.

        A constructor rather than raw field assignment so the ceilings cannot be
        skipped by a caller that builds the dataclass directly — every path that
        matters goes through here.
        """
        industry = " ".join(part for part in (industry_code, industry_description) if part)
        return cls(
            purpose=_joined(purpose, MAX_PURPOSE_CHARS),
            activity=_joined(activity, MAX_ACTIVITY_CHARS),
            industry=_clip(industry or None, 120),
            vat_registered=vat_registered,
            note=_clip(note, MAX_NOTE_CHARS),
            direction=direction if direction in _DIRECTION_SAID else None,
            farm=farm,
        )

    @property
    def is_empty(self) -> bool:
        """Nothing to say. The prompt then reads exactly as it did before."""
        return not self.render()

    def render(self) -> str:
        """The block that goes above the document, in Norwegian.

        Labelled lines rather than prose, and the user's note last under a label
        that says who wrote it. Both choices are about keeping the model able to
        tell the three sources apart: what the state's register says about this
        company, and what the person uploading claims about this particular
        payment, do not carry the same weight, and a model handed one
        undifferentiated paragraph cannot weigh them differently.
        """
        lines: list[str] = []
        if self.purpose is not None:
            lines.append(f"Vedtektsfestet formål: {self.purpose}")
        if self.activity is not None:
            lines.append(f"Registrert aktivitet: {self.activity}")
        if self.industry is not None:
            lines.append(f"Næringskode: {self.industry}")
        if self.vat_registered is not None:
            lines.append(
                "Mva-registrert: "
                + ("ja" if self.vat_registered else "nei, ikke i Merverdiavgiftsregisteret")
            )
        if self.direction is not None:
            lines.append(f"Brukeren har valgt: {_DIRECTION_SAID[self.direction]}")
        if self.note is not None:
            lines.append(f"Brukerens egen forklaring på bilaget: {self.note}")
        # The farm line is fixed text and sits outside the ceiling: it is the
        # same few hundred characters on every bilag, so it cannot grow the way
        # a formål or a note can, and clipping it would cut the one sentence
        # the add-on is there to say. Last, beside the note, as a statement
        # about this company rather than about the register's data.
        if not lines and not self.farm:
            return ""
        block = "\n".join(lines)[:MAX_CONTEXT_CHARS]
        if self.farm:
            block = f"{block}\n{_FARM_SAID}" if block else _FARM_SAID
        return block


#: The two answers to "which way did the money go", as the model is told them.
#: Keyed by the API's own values, and anything else is dropped rather than
#: passed through, so this line can only ever say one of two fixed sentences.
_DIRECTION_SAID = {
    "ut": "penger ut, altså et kjøp eller en annen betaling fra selskapet",
    "inn": "penger inn, altså et salg eller en annen innbetaling til selskapet",
}


#: What a farm's model is told. Fixed text, never assembled from anything a user
#: typed. Two facts the paperwork of a farm turns on: a settlement from the
#: buyer is a sale, and milk, slaughter animals and grain are sold at 25 %.
_FARM_SAID = (
    "Landbruk: selskapet driver jord- eller skogbruk. Oppgjør og avregninger fra Tine, "
    "Nortura, Felleskjøpet, mølla, slakteriet eller tømmerkjøperen er salg kjøperen har "
    "skrevet ut for selskapet, altså penger inn. Melk til meieri, slaktedyr og korn har "
    "25 % mva."
)


#: The empty context. Passing this is exactly equivalent to passing nothing, and
#: having a named value for it keeps callers from having to spell `or
#: BilagContext()` at four call sites.
NO_CONTEXT = BilagContext()
