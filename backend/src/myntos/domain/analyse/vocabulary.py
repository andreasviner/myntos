"""The tags a model may put on a line, and how a new one gets in.

docs/analyse.md «The facets and the vocabulary». One vocabulary per company
(rule 4), in ``ki_tag``: Myntos's seeds, copied in by :func:`ensure_seeds`,
plus what a model added for that company within the caps. Without one shared
list the model is shown before every batch, «Programvare», «Software»,
«Programvare og lisenser» and «IT-abonnement» arrive within a week and every
split by tag is worthless.

**Seeds are code.** Each has a slug, a bokmål label, one sentence telling the
model when to use it, a direction and the account ranges it normally sits on.
The ranges are NS 4102 groups, checked against Myntos's own chart
(:mod:`myntos.domain.ledger.chart`) so that every income and cost account the
chart ships with falls in some seed's range; WP7 reads them to spot a tag that
disagrees with its account. The direction is enforced when an answer is read:
an income tag on a cost line does not validate.

**Bump ``SEED_VERSION``** when a seed is added, removed or its meaning
changes. Labels and descriptions of ``origin = 'myntos'`` rows follow the
code on the next run either way; the version is in every stamp, so a change
of meaning can be found afterwards.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Before landbruk, which imports kontoplan: kontoplan and the ledger service
# import each other, and only an import that starts at the service completes.
# main.py reaches this package (through myntos.api.analyse_ki) before anything
# else has loaded the service, and so would a script that starts here.
import myntos.domain.ledger.service  # noqa: F401
from myntos.core.clock import utc_now
from myntos.domain.analyse.models import Facet, KiTag, TagOrigin
from myntos.domain.ledger import landbruk
from myntos.domain.ledger.models import Company

SEED_VERSION = 1

#: The side of the books a line is on, in the words the model is shown.
Direction = Literal["inntekt", "kostnad", "begge"]

#: At most this many new ``hva`` tags in one run, and this many active ones per
#: company. Beyond either the line gets ``annet``: a vocabulary that grows
#: without bound is no vocabulary, and a model that keeps inventing tags is
#: better stopped than followed.
MAX_NEW_HVA_PER_RUN = 10
MAX_ACTIVE_HVA = 150

#: Model-made counterparties shown in the prompt, the oldest first so the list
#: only ever grows at its end (the cached prefix, docs/analyse.md «The prompt»).
MAX_MOTPART_IN_PROMPT = 300

#: The catch-all, and what a line gets when the caps are reached.
ANNET = "annet"


@dataclass(frozen=True)
class Seed:
    """One tag Myntos writes into every company's vocabulary."""

    facet: Facet
    slug: str
    label: str
    #: For the model: when to use it. Bokmål, one sentence.
    description: str
    direction: Direction = "begge"
    #: The account ranges, inclusive, the tag normally sits on (NS 4102).
    accounts: tuple[tuple[int, int], ...] = ()
    #: Only for books with the farm add-on (docs/landbruk.md).
    landbruk: bool = False


def _hva(
    slug: str,
    label: str,
    description: str,
    direction: Direction,
    *accounts: tuple[int, int],
    farm: bool = False,
) -> Seed:
    return Seed(Facet.HVA, slug, label, description, direction, accounts, farm)


# The order here is the order the model is shown them in, and so part of the
# cached prompt prefix: append, do not sort.
_COSTS = (
    _hva(
        "varer-for-videresalg",
        "Varer for videresalg",
        "Varer kjøpt inn for å selges videre slik de er.",
        "kostnad",
        (4000, 4399),
    ),
    _hva(
        "raavarer-og-innsatsvarer",
        "Råvarer og innsatsvarer",
        "Materialer og råvarer som går inn i det bedriften lager eller utfører.",
        "kostnad",
        (4000, 4499),
    ),
    _hva(
        "frakt-og-toll",
        "Frakt og toll",
        "Frakt, porto, budtjenester, toll og fortolling.",
        "kostnad",
        (4000, 4999),
        (6100, 6199),
        (6940, 6949),
    ),
    _hva(
        "underleverandorer-og-innleie",
        "Underleverandører og innleie",
        "Arbeid kjøpt fra andre bedrifter som del av egne oppdrag, og innleid arbeidskraft.",
        "kostnad",
        (4500, 4599),
        (6700, 6799),
    ),
    _hva(
        "lonn-og-honorarer",
        "Lønn og honorarer",
        "Lønn, feriepenger, arbeidsgiveravgift, pensjon og honorarer til personer.",
        "kostnad",
        (5000, 5999),
    ),
    _hva(
        "velferd-for-ansatte",
        "Velferd for ansatte",
        "Julebord, sosiale tiltak, arbeidsklær og annen velferd for de ansatte.",
        "kostnad",
        (5900, 5999),
    ),
    _hva(
        "husleie-og-lokaler",
        "Husleie og lokaler",
        "Leie av kontor, lager eller verksted, felleskostnader, renhold og vakthold.",
        "kostnad",
        (6300, 6399),
    ),
    _hva(
        "strom-og-oppvarming",
        "Strøm og oppvarming",
        "Strøm, nettleie, fjernvarme og brensel til oppvarming.",
        "kostnad",
        (6300, 6399),
    ),
    _hva(
        "leie-av-maskiner-og-utstyr",
        "Leie av maskiner og utstyr",
        "Leie og leasing av maskiner, utstyr og inventar.",
        "kostnad",
        (6400, 6499),
    ),
    _hva(
        "verktoy-og-utstyr",
        "Verktøy og utstyr",
        "Verktøy, inventar, møbler og utstyr som ikke er datautstyr, ført som kostnad.",
        "kostnad",
        (6500, 6599),
    ),
    _hva(
        "datautstyr",
        "Datautstyr",
        "PC-er, skjermer, mobiltelefoner, nettbrett og tilbehør til dem.",
        "kostnad",
        (6500, 6599),
        (6800, 6899),
    ),
    _hva(
        "programvare-og-abonnementer",
        "Programvare og abonnementer",
        "Programvare, apper, nettjenester, lisenser og digitale abonnementer.",
        "kostnad",
        (6500, 6599),
        (6800, 6899),
    ),
    _hva(
        "reparasjon-og-vedlikehold",
        "Reparasjon og vedlikehold",
        "Reparasjon og vedlikehold av lokaler, maskiner og utstyr, ikke av bil.",
        "kostnad",
        (6600, 6699),
    ),
    _hva(
        "regnskap-og-revisjon",
        "Regnskap og revisjon",
        "Regnskapsfører og revisor.",
        "kostnad",
        (6700, 6799),
    ),
    _hva(
        "juridisk-hjelp-og-radgivning",
        "Juridisk hjelp og rådgivning",
        "Advokat, konsulenter og annen rådgivning.",
        "kostnad",
        (6700, 6799),
    ),
    _hva(
        "kontorrekvisita",
        "Kontorrekvisita",
        "Papir, skrivesaker, blekk og annet forbruksmateriell til kontoret.",
        "kostnad",
        (6800, 6899),
    ),
    _hva(
        "telefon-og-internett",
        "Telefon og internett",
        "Mobilabonnement, fasttelefon og bredbånd.",
        "kostnad",
        (6900, 6999),
    ),
    _hva(
        "drivstoff",
        "Drivstoff",
        "Bensin, diesel og lading til bil eller maskiner.",
        "kostnad",
        (7000, 7099),
    ),
    _hva(
        "bil-vedlikehold-og-forsikring",
        "Bil: vedlikehold og forsikring",
        "Service, reparasjon, dekk, vask, forsikring og årsavgift for bil.",
        "kostnad",
        (7000, 7099),
    ),
    _hva(
        "bompenger-og-parkering",
        "Bompenger og parkering",
        "Bompenger, ferje for bilen og parkering.",
        "kostnad",
        (7000, 7199),
    ),
    _hva(
        "reise-og-transport",
        "Reise og transport",
        "Fly, tog, buss, drosje og leiebil på reise.",
        "kostnad",
        (7100, 7199),
    ),
    _hva(
        "overnatting",
        "Overnatting",
        "Hotell og annen overnatting på reise.",
        "kostnad",
        (7100, 7199),
    ),
    _hva(
        "mat-pa-reise", "Mat på reise", "Måltider på reise, uten gjester.", "kostnad", (7100, 7199)
    ),
    _hva(
        "kurs-og-opplaering",
        "Kurs og opplæring",
        "Kurs, konferanser, faglitteratur og opplæring.",
        "kostnad",
        (6800, 6899),
    ),
    _hva(
        "markedsforing-og-annonser",
        "Markedsføring og annonser",
        "Annonser, reklame, trykksaker, nettside og sponsing.",
        "kostnad",
        (7300, 7399),
    ),
    _hva(
        "representasjon",
        "Representasjon",
        "Mat og drikke med kunder og forretningsforbindelser.",
        "kostnad",
        (7300, 7399),
    ),
    _hva(
        "gaver",
        "Gaver",
        "Gaver til kunder, forretningsforbindelser eller ansatte.",
        "kostnad",
        (7400, 7499),
        (5900, 5999),
    ),
    _hva(
        "kontingenter-og-medlemskap",
        "Kontingenter og medlemskap",
        "Medlemskap i foreninger, bransjeorganisasjoner og lignende.",
        "kostnad",
        (7400, 7499),
    ),
    _hva(
        "forsikring",
        "Forsikring",
        "Forsikringer for bedriften, ikke for bil.",
        "kostnad",
        (7500, 7599),
    ),
    _hva(
        "bankgebyrer",
        "Bankgebyrer",
        "Gebyrer for bank, betalingskort og betalingsløsninger, og øreavrunding.",
        "kostnad",
        (7700, 7799),
    ),
    _hva(
        "renter-og-finanskostnader",
        "Renter og finanskostnader",
        "Renter på lån og kreditt, og valutatap.",
        "kostnad",
        (8100, 8199),
    ),
    _hva(
        "tap-pa-fordringer",
        "Tap på fordringer",
        "Kunder som ikke har betalt, ført som tap.",
        "kostnad",
        (7800, 7899),
    ),
    _hva(
        "avskrivning",
        "Avskrivning",
        "Avskrivning av driftsmidler, og tap når et driftsmiddel går ut.",
        "kostnad",
        (6000, 6099),
        (7880, 7889),
    ),
)

_INCOME = (
    _hva("salg-av-varer", "Salg av varer", "Salg av varer og produkter.", "inntekt", (3000, 3199)),
    _hva(
        "salg-av-tjenester",
        "Salg av tjenester",
        "Salg av arbeid, oppdrag og tjenester.",
        "inntekt",
        (3000, 3199),
    ),
    _hva(
        "abonnementsinntekter",
        "Abonnementsinntekter",
        "Faste betalinger for abonnement eller avtaler som løper over tid.",
        "inntekt",
        (3000, 3199),
    ),
    _hva(
        "provisjon",
        "Provisjon",
        "Provisjon og formidlingsinntekter.",
        "inntekt",
        (3000, 3199),
        (3900, 3999),
    ),
    _hva(
        "leieinntekter",
        "Leieinntekter",
        "Utleie av lokaler, eiendom, jord, maskiner eller utstyr.",
        "inntekt",
        (3600, 3699),
    ),
    _hva("tilskudd", "Tilskudd", "Offentlige tilskudd og støtte.", "inntekt", (3400, 3499)),
    _hva(
        "renteinntekter",
        "Renteinntekter",
        "Renter fra bank og andre, og valutagevinst.",
        "inntekt",
        (8000, 8099),
    ),
    _hva(
        "gevinst-og-andre-inntekter",
        "Gevinst og andre inntekter",
        "Gevinst ved salg av driftsmidler, erstatninger og andre inntekter som ikke er salg.",
        "inntekt",
        (3800, 3999),
        (8000, 8099),
    ),
)

# The farm add-on's own (docs/landbruk.md): an add-on, never a different
# product, so only books with it are shown these. Ranges are the farm accounts
# in chart.LANDBRUK_ACCOUNTS.
_FARM = (
    _hva(
        "melk",
        "Melk",
        "Melk levert til meieri, med tilskudd på melken.",
        "inntekt",
        (3070, 3074),
        farm=True,
    ),
    _hva(
        "kjott",
        "Kjøtt",
        "Slakt levert til slakteri, med tilskudd på slaktet.",
        "inntekt",
        (3040, 3049),
        farm=True,
    ),
    _hva("livdyr", "Livdyr", "Salg av levende dyr.", "inntekt", (3050, 3059), farm=True),
    _hva(
        "korn-og-planteprodukter",
        "Korn og planteprodukter",
        "Salg av korn, frø, høy, poteter, grønnsaker, frukt og bær.",
        "inntekt",
        (3000, 3029),
        farm=True,
    ),
    _hva(
        "egg-ull-og-skinn",
        "Egg, ull og skinn",
        "Salg av egg, ull, skinn og andre husdyrprodukter.",
        "inntekt",
        (3075, 3079),
        farm=True,
    ),
    _hva(
        "skog-og-ved",
        "Skog og ved",
        "Salg av tømmer, ved og annet skogsvirke, og utbetaling fra skogfond.",
        "inntekt",
        (3094, 3094),
        (3910, 3910),
        farm=True,
    ),
    _hva(
        "leiekjoring",
        "Leiekjøring",
        "Leiekjøring og maskinarbeid gjort for andre.",
        "inntekt",
        (3091, 3091),
        farm=True,
    ),
    _hva(
        "produksjonstilskudd",
        "Produksjonstilskudd",
        "Produksjonstilskudd, avløsertilskudd, miljøtilskudd og andre tilskudd til gården.",
        "inntekt",
        (3400, 3499),
        farm=True,
    ),
    _hva(
        "uttak-til-privat-bruk",
        "Uttak til privat bruk",
        "Gårdens egne produkter tatt ut til privat bruk.",
        "inntekt",
        (3160, 3169),
        farm=True,
    ),
    _hva(
        "sakorn-og-settepoteter",
        "Såkorn og settepoteter",
        "Såvarer og settepoteter.",
        "kostnad",
        (4000, 4009),
        farm=True,
    ),
    _hva(
        "for",
        "Fôr",
        "Kraftfôr, grovfôr, halm, mineraler og tilskuddsfôr.",
        "kostnad",
        (4020, 4049),
        farm=True,
    ),
    _hva(
        "gjodsel-og-kalk",
        "Gjødsel og kalk",
        "Handelsgjødsel og kalk.",
        "kostnad",
        (4450, 4459),
        farm=True,
    ),
    _hva(
        "plantevernmidler",
        "Plantevernmidler",
        "Plantevernmidler.",
        "kostnad",
        (4410, 4419),
        farm=True,
    ),
    _hva(
        "dyrlege-og-medisin",
        "Dyrlege og medisin",
        "Veterinær og medisin til dyr.",
        "kostnad",
        (4420, 4429),
        (4521, 4521),
        farm=True,
    ),
    _hva(
        "inseminering-og-husdyrkontroll",
        "Inseminering og husdyrkontroll",
        "Inseminering, sædavgift og husdyrkontroll.",
        "kostnad",
        (4522, 4522),
        (4531, 4531),
        farm=True,
    ),
    _hva(
        "avlosning",
        "Avløsning",
        "Avløserlag og landbruksvikar.",
        "kostnad",
        (4570, 4589),
        farm=True,
    ),
    _hva(
        "kjopt-leiekjoring",
        "Kjøpt leiekjøring",
        "Pressing, tresking, jordarbeid og annet maskinarbeid kjøpt fra andre.",
        "kostnad",
        (4559, 4559),
        farm=True,
    ),
    _hva(
        "forbruksmateriell",
        "Forbruksmateriell",
        "Plast, strø, garn, konserveringsmidler og annet forbruksmateriell på gården.",
        "kostnad",
        (4430, 4439),
        (4480, 4489),
        farm=True,
    ),
    _hva(
        "kjop-av-dyr",
        "Kjøp av dyr",
        "Dyr kjøpt inn til buskapen.",
        "kostnad",
        (4600, 4609),
        farm=True,
    ),
    _hva(
        "leie-av-jord-og-kvote",
        "Leie av jord og kvote",
        "Leie av jord og beite, forpaktningsavgift og leie av melkekvote.",
        "kostnad",
        (6470, 6479),
        farm=True,
    ),
    _hva(
        "avsetning-til-skogfond",
        "Avsetning til skogfond",
        "Avsetning til skogfond, trukket i tømmeroppgjøret.",
        "kostnad",
        (7760, 7769),
        farm=True,
    ),
    _hva(
        "beholdningsendring",
        "Beholdningsendring",
        "Endring i beholdning av fôr, gjødsel, såvarer, egne produkter og buskap.",
        "kostnad",
        (4090, 4090),
        (4290, 4290),
        (4690, 4690),
        farm=True,
    ),
)

_ANNET = _hva(ANNET, "Annet", "Bare når ingen av de andre passer.", "begge")


def _flagg(slug: str, label: str, description: str) -> Seed:
    return Seed(Facet.FLAGG, slug, label, description)


_FLAGS = (
    _flagg("mulig-privat", "Mulig privat", "Kan være et privat kjøp som bedriften har betalt."),
    _flagg(
        "representasjon",
        "Representasjon",
        "Mat, drikke eller underholdning med kunder eller forbindelser.",
    ),
    _flagg("gave", "Gave", "En gave til noen utenfor bedriften eller til ansatte."),
    _flagg(
        "mulig-driftsmiddel",
        "Mulig driftsmiddel",
        "Et kjøp som koster minst 30 000 kroner og varer i minst tre år, "
        "og som kanskje skulle vært ført som driftsmiddel.",
    ),
    _flagg("velferd", "Velferd", "Et velferdstiltak for ansatte."),
    _flagg(
        "utenlandsk-tjeneste", "Utenlandsk tjeneste", "En tjeneste kjøpt fra et selskap i utlandet."
    ),
)

#: Every seed, in the order the model is shown them: general costs, general
#: income, the farm's, then ``annet``; the flags after.
SEEDS: tuple[Seed, ...] = (*_COSTS, *_INCOME, *_FARM, _ANNET, *_FLAGS)

_SEED_ORDER = {(seed.facet.value, seed.slug): index for index, seed in enumerate(SEEDS)}
_SEEDS_BY_KEY = {(seed.facet.value, seed.slug): seed for seed in SEEDS}


def seed(facet: Facet | str, slug: str) -> Seed | None:
    """The seed with this slug in this facet, if there is one."""
    return _SEEDS_BY_KEY.get((str(facet), slug))


def seeds_for(company: Company) -> tuple[Seed, ...]:
    """The seeds these books get: the farm's only with the add-on."""
    farm = landbruk.is_enabled(company)
    return tuple(seed for seed in SEEDS if farm or not seed.landbruk)


@dataclass(frozen=True)
class Tag:
    """A row of the vocabulary as the run reads it, detached from any session.

    A run reads and writes in one short session per batch, and a plain value
    survives every one of them where an ORM row would be expired or detached.
    """

    id: int
    facet: str
    slug: str
    label: str
    description: str | None
    origin: str
    merged_into_id: int | None = None
    retired_at: datetime | None = None

    @classmethod
    def of(cls, row: KiTag) -> Tag:
        return cls(
            id=row.id,
            facet=row.facet,
            slug=row.slug,
            label=row.label,
            description=row.description,
            origin=row.origin,
            merged_into_id=row.merged_into_id,
            retired_at=row.retired_at,
        )

    @property
    def active(self) -> bool:
        return self.retired_at is None and self.merged_into_id is None


def direction_fits(tag: Tag, retning: str) -> bool:
    """Whether a tag may go on a line on this side of the books.

    A seed says which side it belongs on; a model's own tag does not, so it
    fits either.
    """
    known = seed(tag.facet, tag.slug) if tag.origin == TagOrigin.MYNTOS else None
    return known is None or known.direction in ("begge", retning)


async def ensure_seeds(db: AsyncSession, company: Company) -> None:
    """Put the seeds into the company's vocabulary, and bring their words up to date.

    Runs at the start of every run. Inserts the missing ones; updates the label
    and description of rows Myntos wrote (``origin = 'myntos'``), never a
    model's. A farm seed stays once inserted, even if the add-on is switched
    off later: lines already carry it, and the prompt shows only the seeds the
    books have now (:func:`ordered`).
    """
    wanted = seeds_for(company)
    existing = {
        (tag.facet, tag.slug): tag
        for tag in (await db.scalars(select(KiTag).where(KiTag.company_id == company.id))).all()
    }
    for item in wanted:
        tag = existing.get((item.facet.value, item.slug))
        if tag is None:
            db.add(
                KiTag(
                    company_id=company.id,
                    facet=item.facet.value,
                    slug=item.slug,
                    label=item.label,
                    description=item.description,
                    origin=TagOrigin.MYNTOS.value,
                    created_at=utc_now(),
                )
            )
        elif tag.origin == TagOrigin.MYNTOS.value:
            tag.label = item.label
            tag.description = item.description
    await db.flush()


async def load(db: AsyncSession, company_id: int) -> list[Tag]:
    """The company's whole vocabulary, retired and merged tags included, by id."""
    rows = await db.scalars(select(KiTag).where(KiTag.company_id == company_id).order_by(KiTag.id))
    return [Tag.of(row) for row in rows.all()]


def ordered(tags: Iterable[Tag], company: Company) -> list[Tag]:
    """The tags the model is shown, in the order it is shown them.

    Seeds in their code order, then the model's own by id, so a tag added
    during a run lands at the end and the prompt before it stays byte for byte
    the same (the cached prefix). Retired and merged tags are left out: the
    model should not reuse them. Farm seeds only while the books have the
    add-on.
    """
    farm = landbruk.is_enabled(company)
    seeds: list[tuple[int, Tag]] = []
    own: list[Tag] = []
    motparter = 0
    for tag in sorted(tags, key=lambda item: item.id):
        if not tag.active:
            continue
        if tag.origin == TagOrigin.MYNTOS.value:
            known = seed(tag.facet, tag.slug)
            if known is None or (known.landbruk and not farm):
                continue
            seeds.append((_SEED_ORDER[(tag.facet, tag.slug)], tag))
            continue
        if tag.facet == Facet.MOTPART:
            motparter += 1
            if motparter > MAX_MOTPART_IN_PROMPT:
                continue
        own.append(tag)
    return [tag for _, tag in sorted(seeds, key=lambda pair: pair[0])] + own


# --- New tags ---------------------------------------------------------------------


_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """A slug for a tag: lowercase, æ→ae, ø→o, å→a, anything else to ``-``.

    Accents are taken off rather than turned into dashes («fôr» is ``for``,
    «café» ``cafe``), because a model writing a seed's label with its accent
    means that seed.
    """
    lowered = text.strip().casefold()
    for letter, spelled in (("æ", "ae"), ("ø", "o"), ("å", "a")):
        lowered = lowered.replace(letter, spelled)
    plain = "".join(
        char for char in unicodedata.normalize("NFKD", lowered) if not unicodedata.combining(char)
    )
    return _NON_SLUG.sub("-", plain).strip("-")[:64].rstrip("-")


def _stem(slug: str) -> str:
    """The slug without one trailing ``-er``/``-e``/``-r``, for «gave» against «gaver».

    Short slugs are left alone: ``for`` and ``fo`` are not the same thing.
    """
    for ending in ("er", "e", "r"):
        if slug.endswith(ending) and len(slug) - len(ending) >= 4:
            return slug[: -len(ending)]
    return slug


def _final(tag: Tag, by_id: dict[int, Tag]) -> Tag:
    """Follow a merge to the tag that replaced this one."""
    seen = {tag.id}
    while tag.merged_into_id is not None and tag.merged_into_id in by_id:
        tag = by_id[tag.merged_into_id]
        if tag.id in seen:  # a cycle a bad merge could make; stop rather than spin
            break
        seen.add(tag.id)
    return tag


def resolved(tags: Iterable[Tag]) -> dict[int, Tag]:
    """Each tag's id to the tag it stands for now, merges followed (WP5b).

    For the figures, which group lines by the tag stored on them: a line
    tagged before a merge belongs to the slice of the tag it was merged into,
    or the same thing would be two slices with two names.
    """
    by_id = {tag.id: tag for tag in tags}
    return {tag_id: _final(tag, by_id) for tag_id, tag in by_id.items()}


def match(tags: Sequence[Tag], facet: Facet, answer: str) -> Tag | None:
    """The tag a model meant by ``answer`` in ``facet``, or None.

    Matched on the slug, on the case-folded label, or on the slug with one
    trailing ``-er``/``-e``/``-r`` taken off, and a merged tag is followed to
    the one it was merged into. A retired tag is returned as it is: the caller
    decides what an answer naming one means.
    """
    wanted = slugify(answer)
    if not wanted:
        return None
    folded = " ".join(answer.split()).casefold()
    stem = _stem(wanted)
    in_facet = [tag for tag in tags if tag.facet == facet.value]
    by_id = {tag.id: tag for tag in tags}
    for test in (
        lambda tag: tag.slug == wanted,
        lambda tag: tag.label.casefold() == folded,
        lambda tag: _stem(tag.slug) == stem,
    ):
        for tag in in_facet:
            if test(tag):
                return _final(tag, by_id)
    return None


def active_hva(tags: Iterable[Tag]) -> int:
    """How many ``hva`` tags are in use: neither retired nor merged away."""
    return sum(1 for tag in tags if tag.facet == Facet.HVA and tag.active)


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)


def clean_label(text: str, *, limit: int = 80) -> str:
    """A model's label or name, made safe to keep: one line, no links, capped.

    Text a model wrote from the books is data (rule 8); it is only ever put on
    a page as text, and this keeps it from carrying anything else along.
    """
    flat = _URL.sub(" ", _CONTROL.sub(" ", text))
    return " ".join(flat.split())[:limit].strip()
