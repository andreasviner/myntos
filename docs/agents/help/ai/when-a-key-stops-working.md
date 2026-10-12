---
title: When an AI's key stops working
description: Why an AI assistant's or program's key to Myntos can stop working (stopped, unused for 30 days, a year old, password reset, the person left the company, keys switched off) and how to give the AI a new one. Use when someone's AI says its Myntos key no longer works or asks for a new key.
pages: /organisasjon/
skill: myntos-connect
---

# When an AI's key stops working

## They might ask

- «Agenten sier at nøkkelen ikke virker lenger.»
- «Jeg fikk en e-post om at nøkkelen er stoppet.»

## Why a key stops

Under «Status» in the card «KI-agenter og API-nøkler» the key says why:

| «Status» | Why |
| --- | --- |
| «Utløpt» | Not used within a day of being made, unused for 30 days, or a year old |
| «Stoppet her» | Somebody pressed «Stopp» |
| «Stoppet av agenten» | The AI stopped it itself, as it is told to when anything looks wrong |
| «Stoppet: brukt fra to steder» | A refresh token was used twice, a sign the key was copied. The person was e-mailed |
| «Stoppet: prøvde for mye» | It kept trying things it may not do. The person was e-mailed |
| «Stoppet: nøkler ble slått av» | Keys were switched off for the company |
| «Stoppet: personen har ikke tilgang lenger» | The person who made it left the company |
| «Stoppet: passordet ble tilbakestilt» | The person's password was reset |
| «Stoppet: kontoen bruker BankID» | The account switched to BankID |

## Steps to give the AI a new key

1. «Innstillinger for selskapet» → «Tilgang» → «Inviter en KI».
2. Make a key as in https://myntos.no/llms/help/ai/invite-an-ai.md.
3. Paste the text into the conversation with the AI. In a new conversation is
   fine; the AI exchanges it at once.

## Good to know

- A stopped key cannot be started again.
- What a stopped key was waiting to have approved can no longer be approved;
  the AI has to ask again with the new key.

## Related

- [See and stop an AI's key](https://myntos.no/llms/help/ai/see-and-stop-keys.md)
