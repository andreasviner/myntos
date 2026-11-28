---
title: Set up an Altinn system user (systembruker)
description: How a company owner or administrator creates and approves an Altinn 3 systembruker in Myntos so Myntos can file Aksjonærregisteroppgaven (RF-1086) and call machine-to-machine Skatteetaten APIs on the company's behalf. Use when someone asks how to connect Altinn systembruker or why Myntos asks for Altinn approval before sending RF-1086.
pages: /organisasjon/ /aksjonaer/
skill: myntos-aksjonaer
---

# Set up an Altinn system user (systembruker)

An **Altinn 3 systembruker** is a one-time authorization a company grants in
Altinn so Myntos can talk directly to Skatteetaten's machine-to-machine APIs
(such as Aksjonærregisteroppgaven RF-1086 and prefill). Once approved, you do
not need to repeat the setup each year.

## They might ask

- «Hvordan setter jeg opp Altinn-systembruker i Myntos?»
- «Hvorfor må jeg godkjenne Myntos i Altinn før jeg sender aksjonærregisteroppgaven?»
- "How do I connect an Altinn 3 system user in Myntos?"

## Before they start

- They need the role «Eier» or «Administrator» in Myntos.
- The person who approves the request in Altinn must have access-management
  authority for the company in Altinn (typically daglig leder, styrets leder,
  or someone with tilgangsstyring in Altinn).

## Steps

1. Open «Innstillinger for selskapet» (the gear icon at the top right, or
   `https://myntos.no/organisasjon/#altinn`) and scroll to «Altinn-tilkobling
   (systembruker)».
2. Click «Opprett systembruker i Altinn». Myntos creates a request for the
   company's organisasjonsnummer and displays «Godkjenn i Altinn ↗».
3. Click «Godkjenn i Altinn ↗». This opens Altinn's own approval page in a new
   tab, where the company's representative signs in with ID-porten / BankID and
   approves Myntos for «Aksjonærregisteroppgaven (RF-1086)».
4. Return to Myntos on `/organisasjon/#altinn` and click «Sjekk status i
   Altinn».
5. When Altinn confirms approval, the badge changes to «Godkjent» and the
   button «Til aksjonærregisteroppgaven →» appears.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Bare eiere og administratorer kan opprette eller oppdatere systembruker i Altinn.» | The signed-in user is a «Medlem» or «Leser» | Ask an «Eier» or «Administrator» on the company to create the request |
| «Status er oppdatert, men forespørselen er ikke godkjent i Altinn ennå.» | The approval step on Altinn's page was not finished yet | Open «Godkjenn i Altinn ↗» again, finish signing in and approving in Altinn, then click «Sjekk status i Altinn» |
| «Altinn-systembruker er ikke satt opp i denne installasjonen ennå.» | Self-hosted or test deployment without Maskinporten / Altinn vendor configuration | Configure Maskinporten and Altinn system register settings, or contact the operator |

## Good to know

- An AI agent with an API key can read whether the system user is approved, but
  can never create or refresh the system user request. Granting Myntos access
  in Altinn is always the company administrator's own act.
- The system user only receives the narrow resource rights requested by Myntos
  (starting with `ske-innrapportering-aksjonaerregisteroppgave`).

## Related

- [File the shareholder register statement](https://myntos.no/llms/help/shareholders/file-the-shareholder-register-statement.md)
- [Keep the shareholder register](https://myntos.no/llms/help/shareholders/keep-the-shareholder-register.md)
- [Confirm the company with ID-porten](https://myntos.no/llms/help/company-and-access/confirm-the-company.md)
