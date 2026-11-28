"""XML serialization for Brønnøysundregistrene RR-0002 (Årsregnskap vanlig).

Generates both XML parts required by Altinn 3 app
``brg/aarsregnskap-vanlig-202406``:

- **Hovedskjema** (``RR-0002``, ``dataFormatId="1266"``,
  ``dataFormatVersion="51820"``, namespace
  ``http://schema.brreg.no/regnsys/aarsregnskap_vanlig``)
- **Underskjema** (``RR-0002-U``, ``dataFormatId="758"``,
  ``dataFormatVersion="51980"``, namespace
  ``http://schema.brreg.no/regnsys/aarsregnskap_vanlig/underskjema``)

Every element name, attribute, ``orid`` code, and child order is verified
against both Brønnøysundregistrene's live XSDs and the stricter January 2027
XSDs vendored in ``backend/tests/fixtures/aarsregnskap/``. Official XSD
spellings (such as ``opptjentEgenkaiptal`` and
``noteBalansefoertVediDesemberVarigeDriftsmidler``) are reproduced verbatim.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from decimal import ROUND_HALF_UP, Decimal

from myntos.aarsregnskap.compute import ComputedAarsregnskap
from myntos.domain.ledger.models import Company

HOVED_NS = "http://schema.brreg.no/regnsys/aarsregnskap_vanlig"
UNDER_NS = "http://schema.brreg.no/regnsys/aarsregnskap_vanlig/underskjema"

HOVED_DATA_FORMAT_ID = "1266"
HOVED_DATA_FORMAT_VERSION = "51820"
UNDER_DATA_FORMAT_ID = "758"
UNDER_DATA_FORMAT_VERSION = "51980"

SYSTEM_NAME = "Myntos"
# Brreg's official code for machine-readable notes supplied in RR-0002-U
# (verified against Brreg's NYDOK Postman collections for RR-0002).
NOTE_MASKINELL_BEHANDLING = "20"


def _ja_nei(flag: bool) -> str:
    return "ja" if flag else "nei"


def _bool_str(flag: bool) -> str:
    return "true" if flag else "false"


def _fmt_decimal_2(value: Decimal) -> str:
    """Format a decimal value matching both live string and Jan 2027 xs:decimal(9,2)."""
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _el(
    parent: ET.Element,
    ns: str,
    tag: str,
    text: str | None = None,
    *,
    orid: int | None = None,
) -> ET.Element:
    attrs = {"orid": str(orid)} if orid is not None else {}
    node = ET.SubElement(parent, f"{{{ns}}}{tag}", attrs)
    if text is not None:
        node.text = text
    return node


def _post(
    parent: ET.Element,
    tag: str,
    *,
    curr: int | None,
    prior: int | None,
    orid_curr: int,
    orid_prior: int,
    orid_note: int | None = None,
    note: str | None = None,
    orid_desc: int | None = None,
    desc: str | None = None,
    always: bool = False,
) -> ET.Element | None:
    """Append one standard RR-0002-U amount element (optional desc, note, aarets, fjoraarets)."""
    curr_nz = curr is not None and curr != 0
    prior_nz = prior is not None and prior != 0
    if not always and not curr_nz and not prior_nz:
        return None
    node = _el(parent, UNDER_NS, tag)
    if desc and orid_desc is not None:
        _el(node, UNDER_NS, "beskrivelse", desc[:70], orid=orid_desc)
    if note and orid_note is not None:
        _el(node, UNDER_NS, "note", note[:35], orid=orid_note)
    if curr is not None:
        _el(node, UNDER_NS, "aarets", str(int(curr)), orid=orid_curr)
    if prior is not None:
        _el(node, UNDER_NS, "fjoraarets", str(int(prior)), orid=orid_prior)
    return node


def _serialize(root: ET.Element, ns: str) -> str:
    ET.register_namespace("", ns)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def render_hovedskjema(company: Company, computed: ComputedAarsregnskap) -> str:
    """Render RR-0002 Hovedskjema XML."""
    draft = computed.draft
    root = ET.Element(
        f"{{{HOVED_NS}}}melding",
        {
            "dataFormatId": HOVED_DATA_FORMAT_ID,
            "dataFormatVersion": HOVED_DATA_FORMAT_VERSION,
            "tjenestehandling": "aarsregnskap_vanlig",
            "tjeneste": "regnskap",
        },
    )

    innsender = _el(root, HOVED_NS, "Innsender")
    enhet = _el(innsender, HOVED_NS, "enhet")
    _el(enhet, HOVED_NS, "organisasjonsnummer", company.orgnr.strip()[:9], orid=18)
    org_form = (computed.organisation_form or "AS")[:35]
    _el(enhet, HOVED_NS, "organisasjonsform", org_form, orid=756)
    _el(enhet, HOVED_NS, "navn", company.name.strip()[:175], orid=1)

    email = (draft.contact_email or "").strip()
    if email:
        kontakt = _el(innsender, HOVED_NS, "kontaktperson")
        _el(kontakt, HOVED_NS, "e-post", email[:70], orid=19022)

    opplysninger = _el(innsender, HOVED_NS, "opplysningerInnsending")
    _el(opplysninger, HOVED_NS, "landTilLand", "false", orid=35172)
    if draft.winding_up_accounts:
        _el(opplysninger, HOVED_NS, "avviklingsregnskap", "true", orid=40314)
    _el(
        opplysninger,
        HOVED_NS,
        "noteMaskinellBehandling",
        NOTE_MASKINELL_BEHANDLING,
        orid=37499,
    )
    _el(opplysninger, HOVED_NS, "systemNavn", SYSTEM_NAME[:50], orid=39007)

    innhold = _el(root, HOVED_NS, "Skjemainnhold")

    periode = _el(innhold, HOVED_NS, "regnskapsperiode")
    _el(periode, HOVED_NS, "regnskapsaar", str(computed.year), orid=17102)
    _el(periode, HOVED_NS, "regnskapsstart", computed.start_date.isoformat(), orid=17103)
    _el(periode, HOVED_NS, "regnskapsslutt", computed.end_date.isoformat(), orid=17104)

    konsern = _el(innhold, HOVED_NS, "konsern")
    _el(konsern, HOVED_NS, "morselskap", _ja_nei(draft.is_parent), orid=4168)
    if draft.is_parent:
        _el(konsern, HOVED_NS, "konsernregnskap", _ja_nei(draft.consolidated), orid=25943)

    prinsipper = _el(innhold, HOVED_NS, "regnskapsprinsipper")
    _el(prinsipper, HOVED_NS, "smaaForetak", _ja_nei(draft.small_company), orid=8079)
    # regnskapsreglerSelskap: "nei" = Regnskapsloven (NGAAP), "ja" = IFRS.
    _el(prinsipper, HOVED_NS, "regnskapsreglerSelskap", "nei", orid=25021)
    _el(prinsipper, HOVED_NS, "forenkletIFRS", "nei", orid=36639)

    fastsettelse = _el(innhold, HOVED_NS, "fastsettelse")
    approval_date = draft.approval_date or computed.end_date
    _el(fastsettelse, HOVED_NS, "fastsettelsedato", approval_date.isoformat(), orid=17105)
    rep = (draft.confirming_representative or company.name).strip()[:70]
    _el(fastsettelse, HOVED_NS, "bekreftendeSelskapsrepresentant", rep, orid=19023)

    revisjon = _el(innhold, HOVED_NS, "revisjonRegnskapsfoerer")
    _el(
        revisjon,
        HOVED_NS,
        "aarsregnskapIkkeRevideres",
        _ja_nei(draft.auditor_elected_out),
        orid=34669,
    )
    _el(
        revisjon,
        HOVED_NS,
        "aarsregnskapUtarbeidetAutorisertRegnskapsfoerer",
        _ja_nei(draft.prepared_by_external_accountant),
        orid=34670,
    )
    _el(
        revisjon,
        HOVED_NS,
        "tjenestebistandEksternAutorisertRegnskapsfoerer",
        _ja_nei(draft.external_accountant_assistance),
        orid=34671,
    )

    return _serialize(root, HOVED_NS)


def render_underskjema(computed: ComputedAarsregnskap) -> str:
    """Render RR-0002-U Underskjema (Selskapsregnskap + Noter) XML."""
    c = computed.current_values
    p = computed.prior_values
    notes = computed.notes

    root = ET.Element(
        f"{{{UNDER_NS}}}melding",
        {
            "dataFormatId": UNDER_DATA_FORMAT_ID,
            "dataFormatVersion": UNDER_DATA_FORMAT_VERSION,
            "tjenestehandling": "aarsregnskap_vanlig_underskjema",
            "tjeneste": "regnskap",
        },
    )

    rapport = _el(root, UNDER_NS, "Rapport-RR0002U")
    ar = _el(rapport, UNDER_NS, "aarsregnskap")
    _el(ar, UNDER_NS, "regnskapstype", "S", orid=25942)
    _el(ar, UNDER_NS, "valuta", "NOK", orid=34984)
    _el(ar, UNDER_NS, "valoer", "H", orid=28974)

    innhold = _el(root, UNDER_NS, "Skjemainnhold-RR0002U")

    def _p(
        parent: ET.Element,
        tag: str,
        key: str,
        orid_curr: int,
        orid_prior: int,
        *,
        orid_note: int | None = None,
        note: str | None = None,
        always: bool = False,
    ) -> ET.Element | None:
        return _post(
            parent,
            tag,
            curr=c.get(key, 0),
            prior=p.get(key),
            orid_curr=orid_curr,
            orid_prior=orid_prior,
            orid_note=orid_note,
            note=note,
            always=always,
        )

    # --- 1. Resultatregnskap: Driftsresultat ---
    drift = _el(innhold, UNDER_NS, "resultatregnskapDriftsresultat")
    _p(drift, "driftsresultat", "driftsresultat", 146, 7026, always=True)

    inntekt = _el(drift, UNDER_NS, "inntekt")
    _p(inntekt, "salgsinntekt", "salgsinntekt", 1340, 7965)
    _p(inntekt, "driftsinntekt", "annen_driftsinntekt", 7709, 7966)
    _p(inntekt, "driftsinntektSum", "sum_driftsinntekter", 72, 6972, always=True)

    kostnad = _el(drift, UNDER_NS, "kostnad")
    _p(kostnad, "beholdningsendringVarer", "beholdningsendring_varer", 16924, 16925)
    _p(kostnad, "varekostnad", "varekostnad", 101, 6977)
    _p(
        kostnad,
        "loennskostnad",
        "lonnskostnad",
        81,
        6979,
        orid_note=18239,
        note="2" if c.get("lonnskostnad", 0) != 0 else None,
    )
    _p(
        kostnad,
        "avskrivning",
        "avskrivning",
        2139,
        10181,
        orid_note=18247,
        note="4" if c.get("avskrivning", 0) != 0 else None,
    )
    _p(kostnad, "nedskrivning", "nedskrivning", 2354, 10180)
    _p(kostnad, "annenDriftskostnad", "annen_driftskostnad", 82, 7023)
    _p(kostnad, "sumDriftskostnad", "sum_driftskostnader", 17126, 17127, always=True)

    # --- 2. Resultatregnskap: Finansposter ---
    has_fin = any(
        (c.get(k, 0) != 0 or (p.get(k) or 0) != 0)
        for k in (
            "sum_finansinntekter",
            "sum_finanskostnader",
            "netto_finans",
        )
    )
    if has_fin:
        fin = _el(innhold, UNDER_NS, "resultatregnskapFinansinntekt")
        _p(fin, "nettoFinans", "netto_finans", 158, 7999, always=True)
        if c.get("sum_finansinntekter", 0) != 0 or (p.get("sum_finansinntekter") or 0) != 0:
            fin_in = _el(fin, UNDER_NS, "finansinntekt")
            _p(
                fin_in,
                "investeringDatterforetakTilknyttetSelskap",
                "finansinntekt_konsern",
                27934,
                27935,
            )
            _p(fin_in, "andreInvesteringer", "finansinntekt_andre_inv", 27936, 27937)
            _p(fin_in, "renteinntektSammeKonsern", "renteinntekt_konsern", 149, 7029)
            _p(fin_in, "annenRenteinntekt", "annen_renteinntekt", 150, 7030)
            _p(fin_in, "annenFinansinntekt", "annen_finansinntekt", 152, 7032)
            _p(fin_in, "sumFinansinntekter", "sum_finansinntekter", 153, 7993, always=True)
        if c.get("sum_finanskostnader", 0) != 0 or (p.get("sum_finanskostnader") or 0) != 0:
            fin_ut = _el(fin, UNDER_NS, "finanskostnad")
            _p(
                fin_ut,
                "verdireduksjonFinansielleInstrumenter",
                "verdireduksjon_fin_instr",
                25004,
                25005,
            )
            _p(fin_ut, "nedskrivningFinansielleEiendeler", "nedskrivning_fin_eiendeler", 7035, 7036)
            _p(fin_ut, "rentekostnad", "rentekostnad_konsern", 7037, 7038)
            _p(fin_ut, "annenRentekostnad", "annen_rentekostnad", 2216, 7039)
            _p(fin_ut, "annenFinanskostnad", "annen_finanskostnad", 156, 7041)
            _p(fin_ut, "sumFinanskostnader", "sum_finanskostnader", 17130, 17131, always=True)

    # --- 3. Resultatregnskap: Resultat og disponeringer (required) ---
    res_wrap = _el(innhold, UNDER_NS, "resultatregnskapResultat")
    res = _el(res_wrap, UNDER_NS, "resultat")
    _p(res, "resultatFoerSkattekostnad", "resultat_for_skatt", 167, 7042, always=True)
    _p(
        res,
        "skattekostnad",
        "skattekostnad",
        11835,
        11836,
        orid_note=18259,
        note="5" if c.get("skattekostnad", 0) != 0 else None,
    )
    _p(res, "aarsresultat", "aarsresultat", 172, 7054, always=True)

    if c.get("sum_overfoeringer", 0) != 0 or (p.get("sum_overfoeringer") or 0) != 0:
        overf = _el(res_wrap, UNDER_NS, "overfoeringer")
        _p(overf, "overfoeringTilFraFond", "disp_fond", 7055, 7056)
        _p(overf, "utbytte", "disp_utbytte", 6112, 9952)
        _p(overf, "konsernbidrag", "disp_konsernbidrag_mottatt", 6113, 10288)
        _p(overf, "avgittKonsernbidrag", "disp_konsernbidrag_avgitt", 36636, 36637)
        _p(overf, "udekketTap", "disp_udekket_tap", 185, 7070)
        _p(overf, "overfoeringTilFraAnnenEgenkapital", "disp_annen_egenkapital", 7065, 7066)
        _p(overf, "sumOverfoeringerOgDisponeringer", "sum_overfoeringer", 7071, 7072, always=True)

    # --- 4. Balanse: Eiendeler (required) ---
    eiendeler = _el(innhold, UNDER_NS, "balanseAnleggsmidlerOmloepsmidler")
    _p(eiendeler, "sumEiendeler", "sum_eiendeler", 219, 7127, always=True)

    has_anlegg = c.get("sum_anleggsmidler", 0) != 0 or (p.get("sum_anleggsmidler") or 0) != 0
    if has_anlegg:
        anlegg = _el(eiendeler, UNDER_NS, "balanseAnleggsmidler")
        _p(anlegg, "sumAnleggsmidler", "sum_anleggsmidler", 217, 7108, always=True)

        has_imm = c.get("sum_immaterielle", 0) != 0 or (p.get("sum_immaterielle") or 0) != 0
        has_var = (
            c.get("sum_varige_driftsmidler", 0) != 0 or (p.get("sum_varige_driftsmidler") or 0) != 0
        )
        if has_imm or has_var:
            im_var = _el(anlegg, UNDER_NS, "immaterielleEiendelerVarigeDriftsmidler")
            if has_imm:
                imm = _el(im_var, UNDER_NS, "immaterielleEiendeler")
                _p(imm, "forskningUtvikling", "utvikling", 7073, 7074, orid_note=18559, note="4")
                _p(
                    imm,
                    "konsesjonerPatenterLisenserVaremerkerRettigheter",
                    "konsesjoner_rettigheter",
                    205,
                    7075,
                    orid_note=18560,
                    note="4",
                )
                _p(imm, "utsattSkattefordel", "utsatt_skattefordel", 202, 7076)
                _p(imm, "goodwill", "goodwill", 206, 7077, orid_note=18183, note="4")
                _p(imm, "sumImmaterielleEiendeler", "sum_immaterielle", 2400, 8006, always=True)
            if has_var:
                var = _el(im_var, UNDER_NS, "varigeDriftsmidler")
                _p(
                    var,
                    "tomterBygningerAnnenFastEiendom",
                    "tomter_bygninger",
                    1976,
                    8007,
                    orid_note=18178,
                    note="4",
                )
                _p(var, "maskinAnlegg", "maskiner_anlegg", 1975, 8008, orid_note=18179, note="4")
                _p(var, "skipRiggFly", "skip_rigg_fly", 1348, 7084)
                _p(
                    var,
                    "driftsloesoereInventarVerktoeyKontormaskin",
                    "driftslosore_inventar",
                    7725,
                    8009,
                    orid_note=18562,
                    note="4",
                )
                _p(var, "sumVarigeDriftsmidler", "sum_varige_driftsmidler", 47, 8010, always=True)

        has_fin_anl = (
            c.get("sum_finansielle_anleggsmidler", 0) != 0
            or (p.get("sum_finansielle_anleggsmidler") or 0) != 0
        )
        if has_fin_anl:
            fa = _el(anlegg, UNDER_NS, "balanseFinansielleAnleggsmidler")
            _p(fa, "investeringDatterselskap", "inv_datterselskap", 9686, 10289)
            _p(fa, "investeringAnnetForetakSammeKonsern", "inv_samme_konsern", 7727, 8012)
            _p(fa, "laanForetakSammeKonsern", "laan_samme_konsern", 6500, 7093)
            _p(fa, "investeringTilknyttetSelskap", "inv_tilknyttet", 7726, 8013)
            _p(
                fa,
                "laanTilknyttetSelskapFelleskontrollertVirksomhet",
                "laan_tilknyttet",
                7098,
                7099,
            )
            _p(fa, "investeringAksjerAndeler", "inv_aksjer_andeler", 7100, 7101)
            _p(fa, "obligasjon", "obligasjoner", 27583, 27584)
            _p(fa, "annenFordring", "andre_langsiktige_fordringer", 203, 27585)
            _p(
                fa,
                "sumFinansielleAnleggsmidler",
                "sum_finansielle_anleggsmidler",
                5267,
                8014,
                always=True,
            )

    has_omlop = c.get("sum_omlopsmidler", 0) != 0 or (p.get("sum_omlopsmidler") or 0) != 0
    if has_omlop:
        omlop = _el(eiendeler, UNDER_NS, "balanseOmloepsmidler")
        _p(omlop, "sumOmloepsmidler", "sum_omlopsmidler", 194, 7126, always=True)

        has_varer = c.get("sum_varer", 0) != 0 or (p.get("sum_varer") or 0) != 0
        has_fordr = c.get("sum_fordringer", 0) != 0 or (p.get("sum_fordringer") or 0) != 0
        if has_varer or has_fordr:
            vf = _el(omlop, UNDER_NS, "balanseOmloepsmidlerVarerFordringer")
            if has_varer:
                varer = _el(vf, UNDER_NS, "varer")
                _p(varer, "lagerbeholdning", "varer", 326, 797)
                _p(varer, "sumVarer", "sum_varer", 25012, 25013, always=True)
            if has_fordr:
                fordr = _el(vf, UNDER_NS, "fordringer")
                _p(fordr, "kundefordringer", "kundefordringer", 116, 6921)
                _p(fordr, "andreFordringer", "andre_kortsiktige_fordringer", 282, 7112)
                _p(fordr, "konsernfordringer", "konsernfordringer", 7128, 6946)
                _p(fordr, "sumFordringer", "sum_fordringer", 80, 8015, always=True)

        has_inv = c.get("sum_investeringer", 0) != 0 or (p.get("sum_investeringer") or 0) != 0
        has_bank = c.get("sum_bankinnskudd", 0) != 0 or (p.get("sum_bankinnskudd") or 0) != 0
        if has_inv or has_bank:
            ib = _el(omlop, UNDER_NS, "balanseOmloepsmidlerInvesteringerBankinnskuddKontanter")
            if has_inv:
                inv = _el(ib, UNDER_NS, "investering")
                _p(inv, "markedsbaserteAksjer", "markedsbaserte_aksjer", 7117, 7118)
                _p(inv, "andreFinansielleInstrumenter", "andre_fin_instrumenter", 6429, 7123)
                _p(inv, "sumInvesteringer", "sum_investeringer", 6601, 8018, always=True)
            if has_bank:
                bank = _el(ib, UNDER_NS, "bankinnskuddKontanter")
                _p(
                    bank,
                    "bankinnskuddKontanter",
                    "bankinnskudd_kontanter",
                    786,
                    8019,
                    orid_note=18390,
                    note="7" if notes.bundne_bankinnskudd > 0 else None,
                )
                _p(bank, "sumBankinnskuddKontanter", "sum_bankinnskudd", 29042, 29043, always=True)

    # --- 5. Balanse: Egenkapital og gjeld (required) ---
    ek_gjeld = _el(innhold, UNDER_NS, "balanseEgenkapitalGjeld")
    _p(ek_gjeld, "sumEgenkapitalGjeld", "sum_egenkapital_og_gjeld", 251, 7185, always=True)

    ek = _el(ek_gjeld, UNDER_NS, "balanseEgenkapitalInnskuttOpptjentEgenkapital")
    innskutt = _el(ek, UNDER_NS, "innskuttEgenkapital")
    _p(
        innskutt,
        "selskapskapital",
        "selskapskapital",
        20488,
        20489,
        orid_note=18581,
        note="3" if c.get("selskapskapital", 0) != 0 else None,
    )
    _p(innskutt, "egneAksjer", "egne_aksjer", 7131, 7132)
    _p(innskutt, "overkursfond", "overkurs", 2585, 7135)
    _p(innskutt, "annenInnskuttEgenkapital", "annen_innskutt_egenkapital", 17110, 17111)
    _p(innskutt, "sumInnskuttEgenkapital", "sum_innskutt_egenkapital", 3730, 9984, always=True)

    # Note official Brreg XSD typo: ``opptjentEgenkaiptal`` (not opptjentEgenkapital).
    opptjent = _el(ek, UNDER_NS, "opptjentEgenkaiptal")
    _p(opptjent, "fond", "fond", 27579, 27580)
    _p(opptjent, "avsattUtbytte", "avsatt_utbytte", 23857, 23858)
    _p(opptjent, "annenEgenkapital", "annen_egenkapital", 3274, 7140)
    _p(opptjent, "udekketTap", "udekket_tap", 249, 7141)
    _p(opptjent, "sumOpptjentEgenkapital", "sum_opptjent_egenkapital", 9702, 9985, always=True)
    _p(opptjent, "sumEgenkapital", "sum_egenkapital", 250, 7142, always=True)

    has_gjeld = c.get("sum_gjeld", 0) != 0 or (p.get("sum_gjeld") or 0) != 0
    if has_gjeld:
        gjeld = _el(ek_gjeld, UNDER_NS, "balanseGjeldOversikt")
        _p(gjeld, "sumGjeld", "sum_gjeld", 1119, 7184, always=True)

        has_lang = (
            c.get("sum_langsiktig_gjeld", 0) != 0 or (p.get("sum_langsiktig_gjeld") or 0) != 0
        )
        if has_lang:
            lang = _el(
                gjeld,
                UNDER_NS,
                "balanseGjeldAvsetningerForpliktelserAnnenLangsiktigGjeld",
            )
            _p(lang, "sumLangsiktigGjeld", "sum_langsiktig_gjeld", 86, 7156, always=True)

            has_avs = c.get("sum_avsetninger", 0) != 0 or (p.get("sum_avsetninger") or 0) != 0
            if has_avs:
                avs = _el(lang, UNDER_NS, "avsetningForpliktelser")
                _p(avs, "pensjonsforpliktelse", "pensjonsforpliktelser", 238, 6685)
                _p(avs, "utsattSkatt", "utsatt_skatt", 237, 7143)
                _p(avs, "andreAvsetningerForpliktelser", "andre_avsetninger", 7157, 7146)
                _p(avs, "sumAvsetningerForpliktelser", "sum_avsetninger", 7231, 7230, always=True)

            has_al = (
                c.get("sum_annen_langsiktig_gjeld", 0) != 0
                or (p.get("sum_annen_langsiktig_gjeld") or 0) != 0
            )
            if has_al:
                al = _el(lang, UNDER_NS, "annenLangsiktigGjeld")
                _p(al, "konvertibleLaan", "konvertible_laan_lang", 7147, 7148)
                _p(al, "obligasjonslaan", "obligasjonslaan", 6091, 7149)
                _p(al, "gjeldKredittinstitusjoner", "gjeld_kredittinst_lang", 7150, 7151)
                _p(al, "langsiktigKonserngjeld", "langsiktig_konserngjeld", 2256, 7152)
                _p(al, "ansvarligLaanekapital", "ansvarlig_laanekapital", 243, 9989)
                _p(al, "oevrigLangsiktigGjeld", "ovrig_langsiktig_gjeld", 242, 7155)
                _p(
                    al,
                    "sumAnnenLangsiktigGjeld",
                    "sum_annen_langsiktig_gjeld",
                    25019,
                    25020,
                    always=True,
                )

        has_kort = (
            c.get("sum_kortsiktig_gjeld", 0) != 0 or (p.get("sum_kortsiktig_gjeld") or 0) != 0
        )
        if has_kort:
            kort = _el(gjeld, UNDER_NS, "balanseKortsiktigGjeld")
            _p(kort, "gjeldKredittinstitusjoner", "gjeld_kredittinst_kort", 10926, 13203)
            _p(kort, "leverandoergjeld", "leverandorgjeld", 220, 7162)
            _p(
                kort,
                "betalbarSkatt",
                "betalbar_skatt",
                2483,
                10293,
                orid_note=18589,
                note="5" if c.get("betalbar_skatt", 0) != 0 else None,
            )
            _p(kort, "skyldigeOffentligeAvgifter", "skyldige_offentlige_avgifter", 225, 7170)
            _p(kort, "utbytte", "utbytte_gjeld", 235, 7171)
            _p(kort, "kortsiktigKonserngjeld", "kortsiktig_konserngjeld", 2255, 7175)
            _p(kort, "annenKortsiktigGjeld", "annen_kortsiktig_gjeld", 236, 7182)
            _p(kort, "sumKortsiktigGjeld", "sum_kortsiktig_gjeld", 85, 7183, always=True)

    # --- 6. Poster utenom balansen (optional) ---
    if (
        c.get("garantistillelser", 0) != 0
        or c.get("pantstillelser", 0) != 0
        or (p.get("garantistillelser") or 0) != 0
        or (p.get("pantstillelser") or 0) != 0
    ):
        utenom = _el(innhold, UNDER_NS, "posterUtenomBalansen")
        _p(utenom, "garantistillelser", "garantistillelser", 16920, 16921)
        _p(utenom, "pantstillelser", "pantstillelser", 16922, 16923)

    # --- 7. Noter (structured small-company notes per regnskapsloven § 7-35 ff.) ---
    noter = _el(innhold, UNDER_NS, "noter")

    # Note 1: Regnskapsprinsipper og fortsatt drift
    n_prins = _el(noter, UNDER_NS, "noteRegnskapsPrinsipp")
    _el(n_prins, UNDER_NS, "noteNummerRegnskapsPrinsipp", "1", orid=39009)
    _el(
        n_prins,
        UNDER_NS,
        "regnskapsPrinsippFritekst",
        notes.regnskapsprinsipper[:30000],
        orid=37442,
    )
    if notes.usikkerhet_fortsatt_drift:
        _el(n_prins, UNDER_NS, "noteNummerUsikkerhetFortsattDrift", "1", orid=39010)
    _el(
        n_prins,
        UNDER_NS,
        "usikkerhetDrift",
        _bool_str(notes.usikkerhet_fortsatt_drift),
        orid=37443,
    )
    if notes.usikkerhet_fortsatt_drift and notes.usikkerhet_fortsatt_drift_tekst:
        _el(
            n_prins,
            UNDER_NS,
            "usikkerhetDriftFritekst",
            notes.usikkerhet_fortsatt_drift_tekst[:30000],
            orid=37444,
        )

    # Note 3: Aksjekapital og aksjonærer
    if notes.aksjekapital_tekst or notes.egne_aksjer_antall > 0:
        n_aksje = _el(noter, UNDER_NS, "noteAksjeOpplysninger")
        _el(n_aksje, UNDER_NS, "noteNummerAksjer", "3", orid=39011)
        if notes.egne_aksjer_antall > 0:
            beh = _el(n_aksje, UNDER_NS, "beholdningEgneAksjer")
            _el(beh, UNDER_NS, "egneAksjerAntall", str(notes.egne_aksjer_antall), orid=39939)
        if notes.aksjekapital_tekst:
            _el(
                n_aksje,
                UNDER_NS,
                "antallAksjerFritekst",
                notes.aksjekapital_tekst[:30000],
                orid=37455,
            )

    # Note 2: Antall årsverk (mandatory by Brreg VAL_37467 for small companies)
    n_aarsverk = _el(noter, UNDER_NS, "noteAarsverkTjenestePensjon")
    _el(n_aarsverk, UNDER_NS, "noteNummerAarsverk", "2", orid=39016)
    _el(n_aarsverk, UNDER_NS, "antallAarsverk", _fmt_decimal_2(notes.antall_aarsverk), orid=37467)

    # Lån og sikkerhetsstillelse til ledende personer (if any)
    has_laan = any(
        [
            notes.laan_til_styret,
            notes.laan_til_daglig_leder,
            notes.sikkerhet_styret,
            notes.sikkerhet_daglig_leder,
            notes.laan_fritekst,
        ]
    )
    if has_laan:
        n_laan = _el(noter, UNDER_NS, "noteLaanLedendePerson")
        _el(n_laan, UNDER_NS, "noteNummerLaanSikkerhetLedendePerson", "7", orid=39018)
        if notes.laan_til_styret:
            _el(
                n_laan,
                UNDER_NS,
                "samletLaanMedlemmerStyret",
                str(notes.laan_til_styret),
                orid=39889,
            )
        if notes.laan_til_daglig_leder:
            _el(
                n_laan,
                UNDER_NS,
                "samletLaanAndreMedlemmer",
                str(notes.laan_til_daglig_leder),
                orid=39890,
            )
        if notes.sikkerhet_styret:
            _el(
                n_laan,
                UNDER_NS,
                "samletSikkerhetsstillelseMedlemmerStyret",
                str(notes.sikkerhet_styret),
                orid=39891,
            )
        if notes.sikkerhet_daglig_leder:
            _el(
                n_laan,
                UNDER_NS,
                "samletSikkerhetsstillelseAndreMedlemmer",
                str(notes.sikkerhet_daglig_leder),
                orid=39892,
            )
        if notes.laan_rentesats_styret is not None:
            _el(
                n_laan,
                UNDER_NS,
                "rentesatsMedlemmerStyret",
                _fmt_decimal_2(notes.laan_rentesats_styret),
                orid=39893,
            )
        if notes.laan_rentesats_daglig_leder is not None:
            _el(
                n_laan,
                UNDER_NS,
                "rentesatsAndreMedlemmer",
                _fmt_decimal_2(notes.laan_rentesats_daglig_leder),
                orid=39894,
            )
        if notes.laan_vilkaar_styret:
            _el(
                n_laan,
                UNDER_NS,
                "hovedvilkaarMedlemmerStyret",
                notes.laan_vilkaar_styret[:3000],
                orid=39895,
            )
        if notes.laan_vilkaar_daglig_leder:
            _el(
                n_laan,
                UNDER_NS,
                "hovedvilkaarAndreMedlemmer",
                notes.laan_vilkaar_daglig_leder[:3000],
                orid=39896,
            )
        if notes.laan_fritekst:
            _el(
                n_laan,
                UNDER_NS,
                "laanSikkerhetLedendeFritekst",
                notes.laan_fritekst[:30000],
                orid=37477,
            )

    # Note 2: Spesifisering av lønnskostnader og OTP
    if (
        notes.sum_lonnskostnad_aarets != 0
        or (notes.sum_lonnskostnad_fjoraarets or 0) != 0
        or notes.lonnskostnad_fritekst
    ):
        n_lonn = _el(noter, UNDER_NS, "noteSpesifiseringResultatregnskap")
        _el(n_lonn, UNDER_NS, "noteNummerSpesifiseringResultat", "2", orid=39022)
        if notes.lonn_aarets != 0:
            _el(n_lonn, UNDER_NS, "noteLoennAarets", str(notes.lonn_aarets), orid=37525)
        if notes.lonn_fjoraarets:
            _el(n_lonn, UNDER_NS, "noteLoennFjoraarets", str(notes.lonn_fjoraarets), orid=37526)
        if notes.arbeidsgiveravgift_aarets != 0:
            _el(
                n_lonn,
                UNDER_NS,
                "noteArbGivAvgAarets",
                str(notes.arbeidsgiveravgift_aarets),
                orid=37527,
            )
        if notes.arbeidsgiveravgift_fjoraarets:
            _el(
                n_lonn,
                UNDER_NS,
                "noteArbGivAvgFjoraarets",
                str(notes.arbeidsgiveravgift_fjoraarets),
                orid=37528,
            )
        if notes.pensjon_aarets != 0:
            _el(
                n_lonn,
                UNDER_NS,
                "notePensjonskostnaderAarets",
                str(notes.pensjon_aarets),
                orid=37529,
            )
        if notes.pensjon_fjoraarets:
            _el(
                n_lonn,
                UNDER_NS,
                "notePensjonskostnaderFjoraarets",
                str(notes.pensjon_fjoraarets),
                orid=37530,
            )
        if notes.andre_ytelser_aarets != 0:
            _el(
                n_lonn,
                UNDER_NS,
                "noteAndreYtelserAarets",
                str(notes.andre_ytelser_aarets),
                orid=37531,
            )
        if notes.andre_ytelser_fjoraarets:
            _el(
                n_lonn,
                UNDER_NS,
                "noteAndreYtelserFjoraarets",
                str(notes.andre_ytelser_fjoraarets),
                orid=37532,
            )
        if notes.sum_lonnskostnad_aarets != 0:
            _el(
                n_lonn,
                UNDER_NS,
                "noteSumLoennAarets",
                str(notes.sum_lonnskostnad_aarets),
                orid=37533,
            )
        if notes.sum_lonnskostnad_fjoraarets:
            _el(
                n_lonn,
                UNDER_NS,
                "noteSumLoennFjoraarets",
                str(notes.sum_lonnskostnad_fjoraarets),
                orid=37534,
            )
        if notes.lonnskostnad_fritekst:
            _el(
                n_lonn,
                UNDER_NS,
                "spesifiseringResultatregnskapFritekst",
                notes.lonnskostnad_fritekst[:30000],
                orid=37494,
            )

    # Note 7: Gjeld, pantstillelser, garantier og bundne bankinnskudd
    has_gjeld_note = any(
        [
            notes.gjeld_over_fem_aar,
            notes.pantstillelser_gjeld,
            notes.pantsatte_eiendeler_verdi,
            notes.garantiforpliktelser,
            notes.gjeld_fritekst,
        ]
    )
    if has_gjeld_note:
        n_gjeld = _el(noter, UNDER_NS, "noteGjeld")
        _el(n_gjeld, UNDER_NS, "noteNummerGjeld", "7", orid=39026)
        if notes.gjeld_over_fem_aar:
            _el(
                n_gjeld,
                UNDER_NS,
                "noteGjeldForfallerEtterFemAar",
                str(notes.gjeld_over_fem_aar),
                orid=40005,
            )
        if notes.pantstillelser_gjeld:
            _el(
                n_gjeld,
                UNDER_NS,
                "noteGjeldMedSikkerhetIRegnskapsplEiendeler",
                str(notes.pantstillelser_gjeld),
                orid=40006,
            )
        if notes.pantsatte_eiendeler_verdi:
            _el(
                n_gjeld,
                UNDER_NS,
                "noteGjeldBalansefoertVerdiPantsatteEiendeler",
                str(notes.pantsatte_eiendeler_verdi),
                orid=40008,
            )
        if notes.garantiforpliktelser:
            _el(
                n_gjeld,
                UNDER_NS,
                "sumGarantiforpliktelserIkkeRegnskapsfoert",
                str(notes.garantiforpliktelser),
                orid=39936,
            )
        if notes.gjeld_fritekst:
            _el(
                n_gjeld,
                UNDER_NS,
                "noteMerOmGjeldFritekst",
                notes.gjeld_fritekst[:30000],
                orid=38601,
            )

    # Note 4: Varige driftsmidler og immaterielle eiendeler
    assets_note = notes.anleggsmidler
    has_tangible_note = any(
        [
            assets_note.tangible_opening_cost,
            assets_note.tangible_additions,
            assets_note.tangible_disposals,
            assets_note.tangible_closing_cost,
            assets_note.tangible_year_depreciation,
            assets_note.tangible_carrying_value,
        ]
    )
    has_intangible_note = any(
        [
            assets_note.intangible_opening_cost,
            assets_note.intangible_additions,
            assets_note.intangible_disposals,
            assets_note.intangible_closing_cost,
            assets_note.intangible_year_depreciation,
            assets_note.intangible_carrying_value,
        ]
    )
    if has_tangible_note or has_intangible_note or notes.anleggsmidler_fritekst:
        n_anl = _el(noter, UNDER_NS, "noteAnleggsmidlerDriftsmidler")
        _el(n_anl, UNDER_NS, "noteNummerDriftsmidlerAnleggsmidler", "4", orid=39029)
        if has_tangible_note:
            _el(
                n_anl,
                UNDER_NS,
                "noteAnskaffelseskostJanuarVarigeDriftsmidler",
                str(assets_note.tangible_opening_cost),
                orid=39982,
            )
            _el(
                n_anl,
                UNDER_NS,
                "noteTilgangAaretsVarigeDriftsmidler",
                str(assets_note.tangible_additions),
                orid=39983,
            )
            _el(
                n_anl,
                UNDER_NS,
                "noteAvgangAaretsVarigeDriftsmidler",
                str(assets_note.tangible_disposals),
                orid=39984,
            )
            _el(
                n_anl,
                UNDER_NS,
                "noteAnskaffelseskostDesemberVarigeDriftsmidler",
                str(assets_note.tangible_closing_cost),
                orid=39985,
            )
            _el(
                n_anl,
                UNDER_NS,
                "noteAaretsAvNedskrivningerReverseringVarigeDriftsmidler",
                str(assets_note.tangible_year_depreciation),
                orid=39987,
            )
            # Official Brreg XSD spelling: ``Vedi`` (not Verdi).
            _el(
                n_anl,
                UNDER_NS,
                "noteBalansefoertVediDesemberVarigeDriftsmidler",
                str(assets_note.tangible_carrying_value),
                orid=39999,
            )
            _el(
                n_anl,
                UNDER_NS,
                "noteSamledeAvNedskrivningerReverseringVarigeDriftsmidler",
                str(assets_note.tangible_accumulated_depreciation),
                orid=39986,
            )
        if has_intangible_note:
            _el(
                n_anl,
                UNDER_NS,
                "noteAnskaffelseskostJanuarImmaterielleEiendeler",
                str(assets_note.intangible_opening_cost),
                orid=39998,
            )
            _el(
                n_anl,
                UNDER_NS,
                "noteTilgangAaretsImmaterielleEiendeler",
                str(assets_note.intangible_additions),
                orid=39988,
            )
            _el(
                n_anl,
                UNDER_NS,
                "noteAvgangAaretsImmaterielleEiendeler",
                str(assets_note.intangible_disposals),
                orid=39989,
            )
            _el(
                n_anl,
                UNDER_NS,
                "noteAnskaffelseskostDesemberImmaterielleEiendeler",
                str(assets_note.intangible_closing_cost),
                orid=39990,
            )
            _el(
                n_anl,
                UNDER_NS,
                "noteAaretsAvNedskrivningerReverseringImmaterielleEiendeler",
                str(assets_note.intangible_year_depreciation),
                orid=39992,
            )
            _el(
                n_anl,
                UNDER_NS,
                "noteBalansefoertVediDesemberImmaterielleEiendeler",
                str(assets_note.intangible_carrying_value),
                orid=40000,
            )
            _el(
                n_anl,
                UNDER_NS,
                "noteSamledeAvNedskrivningerReverseringImmaterielleEiendeler",
                str(assets_note.intangible_accumulated_depreciation),
                orid=39991,
            )
        if notes.anleggsmidler_fritekst:
            _el(
                n_anl,
                UNDER_NS,
                "anleggsmidlerFritekst",
                notes.anleggsmidler_fritekst[:30000],
                orid=37496,
            )

    # Note 5 (Skatt), Note 6 (Egenkapital), custom notes, and additional disclosures.
    extra_sections: list[str] = []
    if notes.skatt_tekst:
        extra_sections.append(f"Note 5 - Skatt:\n{notes.skatt_tekst}")
    ek_summary = (
        f"Note 6 - Egenkapital:\n"
        f"Egenkapital per 01.01.{computed.year}: kr {notes.egenkapital_ib:,}\n"
        f"Årsresultat: kr {notes.egenkapital_aarsresultat:,}\n"
        f"Avsatt utbytte: kr {notes.egenkapital_utbytte:,}\n"
        f"Andre egenkapitalendringer: kr {notes.egenkapital_andre_endringer:,}\n"
        f"Egenkapital per 31.12.{computed.year}: kr {notes.egenkapital_ub:,}"
    ).replace(",", " ")
    extra_sections.append(ek_summary)
    for idx, item in enumerate(notes.egendefinerte_noter, start=8):
        title = item.get("tittel") or f"Tilleggsnote {idx}"
        body = item.get("tekst") or ""
        extra_sections.append(f"Note {idx} - {title}:\n{body}")
    if notes.ytterligere_opplysninger:
        extra_sections.append(notes.ytterligere_opplysninger)

    combined_extra = "\n\n".join(extra_sections).strip()
    if combined_extra:
        _el(noter, UNDER_NS, "noteNummerUtenforMinimumskravSmaaForetak", "5-8", orid=39031)
        _el(noter, UNDER_NS, "noteYtterligereOpplysninger", combined_extra[:30000], orid=37551)

    return _serialize(root, UNDER_NS)
