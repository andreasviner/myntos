---
title: Connect your own program or script
description: How a person or developer connects their own program, script or integration to a company's books in Myntos with an API key made through «Inviter en KI». Use when someone asks whether Myntos has an API, or how to connect a script, an integration or another system to Myntos.
pages: /organisasjon/
skill: myntos-connect
---

# Connect your own program or script

Myntos has a public HTTP API under `https://api.myntos.no/api/v1`, and the
web app uses the same API. A program, script or integration connects with
the same kind of key an AI assistant gets. Myntos treats them the same way:
the same rights, the same approval page, the same limits.

## They might ask

- «Har Myntos et API?»
- «Kan jeg koble mitt eget system til Myntos?»
- «Hvordan lager jeg en API-nøkkel?»
- "Does Myntos have an API key for integrations?"

## Steps

1. Make a key as described in
   https://myntos.no/llms/help/ai/invite-an-ai.md: «Innstillinger for
   selskapet» → «Tilgang» → «Inviter en KI». Name it after the program
   («Fakturaskript», «Lagersystem»).
2. Choose what it may do. For a program that should post without anybody
   pressing a button, choose «Selv» for «Bokføre»; for one whose work a person
   should check, choose «Spør meg først», and the person approves each request
   on «Godkjenning».
3. Copy the connection key from the text the window shows (it starts with
   `myntos_ck_`). It works once and must be used within a day.
4. The program exchanges it with `POST /api/v1/agent/token` and then uses
   short-lived access tokens with rotating refresh tokens. Everything a
   developer needs is in `https://myntos.no/llms.txt` and the skills it lists,
   starting with https://myntos.no/llms/skills/myntos-connect.md.

## Good to know

- A key works in one company. A program that serves several companies needs
  one key per company.
- 120 requests a minute per key.
- No key can file to Skatteetaten, move money, change a bank account number or
  manage access, whatever it is given.
- A request that waits for approval answers HTTP 202 with code
  `approval_pending`; see https://myntos.no/llms/skills/myntos-godkjenning.md.

## Related

- [Invite an AI](https://myntos.no/llms/help/ai/invite-an-ai.md)
- [Choose what a key may do](https://myntos.no/llms/help/ai/choose-what-an-ai-may-do.md)
