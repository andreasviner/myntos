"""Anleggsregister and saldoavskrivning: fixed assets, the tax saldo per group, depreciation.

docs/anlegg.md says what is built, why the books follow the tax figures, and
where each rule comes from. The modules:

- :mod:`.rules`: the law as data, per year, with its source (groups, rates,
  the beløpsgrenser), and nothing that touches a database.
- :mod:`.models`: the register, the opening saldi brought from the old
  program, and the record of what each depreciation voucher posted per saldo.
- :mod:`.register`: adding, changing and disposing of assets, and the
  opening saldi.
- :mod:`.saldo`: the saldo per group per year, rolled forward from the start
  of the register. :func:`.saldo.specification` is what the
  næringsspesifikasjon reads.
- :mod:`.avskrivning`: the year's depreciation voucher, review then post,
  corrected by reversal; and the findings the year-end close shows.
"""
