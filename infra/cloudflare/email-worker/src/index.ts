/**
 * Cloudflare Email Worker for Myntos inbound email invoice intake (@myntos.no).
 *
 * Bound to the Cloudflare Email Routing catch-all rule on myntos.no. Specific
 * routing rules (kontakt@myntos.no, conduct@myntos.no, etc.) take precedence
 * over catch-all automatically in Cloudflare Email Routing.
 *
 * Signs the raw RFC 5322 message with HMAC-SHA256 using INBOUND_EMAIL_WEBHOOK_SECRET
 * and forwards it to Myntos backend's POST /api/v1/webhooks/inbound-email.
 */

export interface Env {
  WEBHOOK_URL: string;
  INBOUND_EMAIL_WEBHOOK_SECRET: string;
}

interface ForwardableEmailMessage {
  readonly from: string;
  readonly to: string;
  readonly headers: Headers;
  readonly raw: ReadableStream<Uint8Array>;
  readonly rawSize: number;
  setReject(reason: string): void;
}

const MAX_RAW_BYTES = 25 * 1024 * 1024;

function toHex(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let out = "";
  for (let i = 0; i < bytes.length; i++) {
    out += bytes[i].toString(16).padStart(2, "0");
  }
  return out;
}

async function signPayload(secret: string, timestamp: string, rawBytes: Uint8Array): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const prefix = encoder.encode(`${timestamp}.`);
  const combined = new Uint8Array(prefix.length + rawBytes.length);
  combined.set(prefix, 0);
  combined.set(rawBytes, prefix.length);
  const sig = await crypto.subtle.sign("HMAC", key, combined);
  return `sha256=${toHex(sig)}`;
}

export default {
  async email(message: ForwardableEmailMessage, env: Env): Promise<void> {
    if (!env.INBOUND_EMAIL_WEBHOOK_SECRET || !env.WEBHOOK_URL) {
      console.error("myntos-inbound-email: missing WEBHOOK_URL or INBOUND_EMAIL_WEBHOOK_SECRET");
      return;
    }

    if (message.rawSize > MAX_RAW_BYTES) {
      message.setReject("Message exceeds the 25 MB limit for Myntos invoice intake.");
      return;
    }

    const rawBuffer = await new Response(message.raw).arrayBuffer();
    const rawBytes = new Uint8Array(rawBuffer);
    if (rawBytes.byteLength > MAX_RAW_BYTES) {
      message.setReject("Message exceeds the 25 MB limit for Myntos invoice intake.");
      return;
    }

    const timestamp = String(Math.floor(Date.now() / 1000));
    const signature = await signPayload(env.INBOUND_EMAIL_WEBHOOK_SECRET, timestamp, rawBytes);

    const response = await fetch(env.WEBHOOK_URL, {
      method: "POST",
      headers: {
        "Content-Type": "message/rfc822",
        "X-Myntos-Timestamp": timestamp,
        "X-Myntos-Signature": signature,
        "X-Myntos-Envelope-From": message.from || "",
        "X-Myntos-Envelope-To": message.to || "",
      },
      body: rawBytes,
    });

    if (response.status === 429 || response.status >= 500) {
      // Throw so Cloudflare retries transient failures rather than silently dropping mail.
      throw new Error(`Myntos webhook returned ${response.status}`);
    }

    if (response.ok) {
      let body: { status?: string; reason?: string } | null = null;
      try {
        body = (await response.json()) as { status?: string; reason?: string };
      } catch {
        body = null;
      }
      if (body?.status === "rejected") {
        // Explicit rejection when a company disabled email intake or restricted
        // senders to approved addresses only.
        message.setReject("Recipient inbox is not accepting messages from this sender.");
      }
      return;
    }

    // 4xx other than 429 (e.g. malformed email or auth misconfiguration): log
    // without bouncing spam or leaking internal details.
    console.error(`myntos-inbound-email: webhook rejected request with status ${response.status}`);
  },
};
