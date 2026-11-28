# Myntos Inbound Email Worker (`myntos-inbound-email`)

Cloudflare Email Worker that routes incoming mail sent to `<selskap>@myntos.no`
into Myntos's **Mottatt** inbox (`POST /api/v1/webhooks/inbound-email`).

Specific Cloudflare Email Routing rules (such as `kontakt@myntos.no` or
`conduct@myntos.no`) automatically take precedence over the catch-all rule, so
existing addresses keep working unchanged.

## First-time deploy

1. Generate a shared 32-byte secret:

   ```bash
   openssl rand -hex 32
   ```

2. Add the same secret to the backend's `.env` on the production server:

   ```dotenv
   INBOUND_EMAIL_DOMAIN=myntos.no
   INBOUND_EMAIL_WEBHOOK_SECRET=<the-64-hex-char-secret>
   ```

   Then deploy the backend with `./update.sh`.

3. Deploy this worker and set the matching secret in Cloudflare:

   ```bash
   cd infra/cloudflare/email-worker
   npx wrangler deploy
   npx wrangler secret put INBOUND_EMAIL_WEBHOOK_SECRET
   ```

4. Bind the catch-all rule in the Cloudflare dashboard:
   - **Email → Email Routing → Routing rules → Catch-all address**
   - Set **Action** to **Send to a Worker** → select `myntos-inbound-email`
   - Enable the catch-all rule.

## Verification

Send an email with a PDF attachment to your company's `<slug>@myntos.no` address
shown on `/innkommende/`. Within a few seconds it appears under **Mottatt →
Kun e-postfaktura** with sender identity, SPF/DKIM/DMARC status, and attachment
preview.
