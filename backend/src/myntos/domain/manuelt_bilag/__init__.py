"""Manual vouchers (manuelt bilag): accounts and debit/credit, typed by a person.

Every other way into the ledger starts from something outside it: a payment,
an invoice, the previous program's file, the year's result. What an accountant
does at the end of a year starts from a decision instead: the change in stock
and livestock, a cost that belongs to next year (periodisering), depreciation
until the asset register exists, a balance moved from one account to another
(a driftskreditt from 2390 to 1920), a correction, a private withdrawal. Until
this existed, those had to be made in another program, and the year finished
there.

A manual voucher is one balanced voucher in its own journal (``MANUELT``),
numbered per year like every journal (``2026-0001``), posted by
:func:`service.post_voucher` after the same review :func:`service.review` gives
the page. The review stores nothing; the post runs it again, so the button the
page enabled and the rule enforced are one rule (the opening balance's shape,
:mod:`myntos.domain.arsoppgjor.opening`).

**Documentation is a written reason, and it is required.** Bokføringsloven § 10
asks that every entry be documented in a way that shows it is justified. A
payment is documented by somebody else's paper; a year-end adjustment has none,
because the company decided it. For exactly those, bokføringsforskriften § 5-12
(«Dokumentasjon av andre regnskapsmessige disposisjoner») requires documentation
that "skal vise årsaken til at disposisjonen gjennomføres og bokføres", and for
a correction that the new and the original documentation refer to each other.
§ 6-4 adds, for an estimate (a provision, a write-down), the method and the
assumptions. So every manual voucher carries an explanation of at least ten
characters (:class:`~myntos.domain.bilag.models.VoucherExplanation`, append-only,
like the archive), and may carry files in the bilag archive as well: the
stock count list § 6-1 asks for, the depreciation schedule, the loan statement.
Ten characters is a floor against an empty box, not a judgement of the text;
whether the reason is good enough is the person's and the auditor's question.
A correction's documentation refers to the original through the voucher's own
links (``reverses_transaction_pk``, ``corrects_transaction_pk``), which every
reader of the voucher sees, rather than through text somebody has to remember
to write.

**VAT is computed, never typed.** A line may name a VAT code, and its amount is
then the amount with VAT included, as on every Norwegian bilag and as the
register reads it: Myntos splits it (:func:`~myntos.domain.ledger.posting.split_gross`),
puts the tax information on the basis line
(:func:`~myntos.domain.ledger.posting.set_tax_information`) and adds the VAT
line on 2700 or 2710 itself. Lines straight on 2700 and 2710 are refused. The
alternative, explicit VAT lines beside a code on the basis line, puts two
numbers that must agree in two places for a person to type, and the first
time they disagree the MVA-melding (built from the tax information) and the
reconciliation on the MVA page (built from 2700 and 2710) part company with no
one able to say which is right. Computed, they agree by construction. Gross
rather than net because then the lines a person types balance on their own,
and the split moves an amount within a line without changing the total; a net
basis would leave the typed lines unbalanced until Myntos added a line nobody
typed. ``apply_tax`` is not used for the same reason: it reads the amount as
the basis.

Two families of code are refused and left to the register, where the question
they need is asked: border VAT (14, 15), where the whole amount is the tax, and
reverse charge without deduction (82, 84, 87, 89, 92), whose VAT becomes a
second cost line that could not be told apart from a typed one when the voucher
is read back for a correction. Reverse charge with deduction is allowed and
posts both 2700 and 2710, as the register does. An unregistered company gets
the codes the register gives it (:mod:`myntos.domain.ledger.vatstatus`).

**The other rules, and where they come from.** The date must be in an open
financial year (a closed year is closed for everyone,
:func:`~myntos.domain.ledger.service.ensure_financial_year`). Every account must
be in the company's kontoplan and switched on, as the register requires. The
lines must balance exactly (:mod:`myntos.domain.ledger.posting`). The accounts
the year-end closing posts by itself (8300, 8800, 8960, 8990) are refused,
because the closing would then refuse to close (``allerede_disponert``). A line
on a customer's or supplier's balance account must name that party, or the
sub-ledger stops reconciling, as on the opening balance. A line on the bank
account is allowed (moving a driftskreditt onto 1920 is one) and noted, since a
payment belongs in the register, where it gets its category.

**Corrections are a reversal plus a linked replacement, from here only.** The
register's reversal refuses this journal (``cannot_reverse_here``), as it
refuses the opening balance, the closing and the imported vouchers: undone
from there, it would be undone without the reason § 5-12 asks for. Reversing a
manual voucher asks for its own reason; correcting one reverses it and posts the
replacement with ``corrects_transaction_pk`` in one request. A voucher in a
closed year is corrected by a new voucher in the open year, or the year is
reopened under årsavslutning.

**Dimensions go on the net line.** A line may name an avdeling, prosjekt or
driftsgren (:mod:`myntos.domain.ledger.dimensions`), resolved through the
company like every other id. The VAT split keeps a line's identity, so the
allocation lands on the basis line it was given for, for its net amount, and
never on the generated VAT line: the result per driftsgren reads income and
costs, and an allocation of the gross amount would be larger than the line it
sits on, which posting refuses. A value that cannot be used is a finding
(``ugyldig_dimensjon``), not the register's 404, so the review lists it with
the rest.

**Shaped for what is coming.** Other features that post a decision
rather than a payment (the livestock count, depreciation) may post through
:func:`service.post_voucher` or carry a :class:`VoucherExplanation` of their own.
"""
