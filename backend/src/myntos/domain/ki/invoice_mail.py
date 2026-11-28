"""Email notice with attached PDF for a monthly KI subscription invoice.

Sent to the subscribing company's billing email whenever a monthly KI invoice
is issued. Carries Myntos's own sales invoice (from the configured billing
company, e.g. Lindeman Technologies AS), never customer bookkeeping data.
"""

from __future__ import annotations

import html
from datetime import date
from decimal import Decimal

from myntos.core.brand import INK, LOGO_CID, LOGO_WIDTH, logo_png
from myntos.core.mail import InlineImage, Mail, MailAttachment

_QUIET = "#5f6661"
_FAINT = "#8a8f8b"
_RULE = "#e8e8e3"


def _kroner(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,.2f}".replace(",", " ").replace(".", ",")


def _dato(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def compose_invoice_mail(
    *,
    to: str,
    buyer_name: str,
    seller_name: str,
    plan_name: str,
    invoice_number: str,
    period_start: date,
    period_end: date,
    amount_incl_vat: Decimal,
    due_date: date,
    payment_reference: str | None,
    bank_account: str | None,
    ehf_sent: bool,
    pdf_bytes: bytes,
    pdf_filename: str,
    portal_url: str,
) -> Mail:
    """Build the email carrying the issued monthly KI invoice PDF."""
    subject = f"Faktura nr. {invoice_number} for Myntos {plan_name} ({buyer_name})"
    period_label = f"{_dato(period_start)} til {_dato(period_end)}"
    amount_text = f"{_kroner(amount_incl_vat)} kr (inkl. mva)"
    ehf_line = (
        "Fakturaen er også sendt som EHF over Peppol til organisasjonsnummeret deres."
        if ehf_sent
        else "Fakturaen ligger vedlagt som PDF, og kan også lastes ned i Myntos."
    )

    lines = [
        "Hei,",
        "",
        f"Her er månedlig faktura fra {seller_name} for {plan_name} i Myntos til {buyer_name}.",
        "",
        f"Fakturanummer: {invoice_number}",
        f"Periode: {period_label}",
        f"Beløp: {amount_text}",
        f"Forfallsdato: {_dato(due_date)}",
    ]
    if bank_account:
        lines.append(f"Kontonummer: {bank_account}")
    if payment_reference:
        lines.append(f"KID: {payment_reference}")
    lines.extend(
        [
            "",
            ehf_line,
            "",
            f"Se abonnement og fakturaer i Myntos: {portal_url}",
            "",
            f"Vennlig hilsen {seller_name}",
        ]
    )
    text_body = "\n".join(lines)

    details_rows = [
        ("Fakturanummer", invoice_number),
        ("Plan", f"Myntos {plan_name}"),
        ("Periode", period_label),
        ("Beløp", amount_text),
        ("Forfallsdato", _dato(due_date)),
    ]
    if bank_account:
        details_rows.append(("Kontonummer", bank_account))
    if payment_reference:
        details_rows.append(("KID", payment_reference))

    rows_html = "".join(
        f'<tr><td style="padding:6px 12px 6px 0;color:{_QUIET};font-size:0.9rem;">'
        f"{html.escape(label)}</td>"
        f'<td style="padding:6px 0;font-weight:600;font-size:0.9rem;color:{INK};">'
        f"{html.escape(value)}</td></tr>"
        for label, value in details_rows
    )
    safe_url = html.escape(portal_url, quote=True)

    html_body = f"""<!doctype html>
<html lang="nb">
  <head>
    <meta charset="utf-8" />
    <meta name="color-scheme" content="light" />
    <meta name="supported-color-schemes" content="light" />
  </head>
  <body style="margin:0;padding:24px;background:#f6f6f4;
               font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
               color:{INK};line-height:1.55;">
    <div style="max-width:34rem;margin:0 auto;background:#fff;border:1px solid #e4e4df;
                border-radius:12px;padding:28px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"
             style="margin:0 0 24px;border-collapse:collapse;">
        <tr>
          <td style="padding:0 10px 0 0;vertical-align:middle;">
            <img src="cid:{LOGO_CID}" alt="Myntos" width="{LOGO_WIDTH}" height="{LOGO_WIDTH}"
                 style="display:block;width:{LOGO_WIDTH}px;height:{LOGO_WIDTH}px;border:0;" />
          </td>
          <td style="vertical-align:middle;font-size:1.35rem;font-weight:700;
                     letter-spacing:-0.01em;color:{INK};">Myntos</td>
        </tr>
      </table>
      <p style="margin:0 0 8px;font-size:0.95rem;color:{_QUIET};">Hei,</p>
      <p style="margin:0 0 18px;font-size:1.05rem;font-weight:600;line-height:1.4;color:{INK};">
        Faktura nr. {html.escape(invoice_number)} for Myntos {html.escape(plan_name)}
        til {html.escape(buyer_name)}
      </p>
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"
             style="margin:0 0 20px;border-collapse:collapse;width:100%;">
        {rows_html}
      </table>
      <p style="margin:0 0 18px;font-size:0.9rem;color:{_QUIET};">{html.escape(ehf_line)}</p>
      <p style="margin:0 0 22px;">
        <a href="{safe_url}"
           style="display:inline-block;padding:10px 18px;border-radius:8px;
                  background:{INK};color:#fff;text-decoration:none;
                  font-size:0.9rem;font-weight:600;">Se KI-plan og fakturaer</a>
      </p>
      <hr style="border:0;border-top:1px solid {_RULE};margin:20px 0 14px;" />
      <p style="margin:0;font-size:0.8rem;color:{_FAINT};">
        Sendt fra {html.escape(seller_name)} via Myntos.
      </p>
    </div>
  </body>
</html>
"""

    return Mail(
        to=to,
        subject=subject,
        text=text_body,
        html=html_body,
        images=(InlineImage(cid=LOGO_CID, data=logo_png()),),
        attachments=(
            MailAttachment(
                filename=pdf_filename,
                data=pdf_bytes,
                maintype="application",
                subtype="pdf",
            ),
        ),
    )
