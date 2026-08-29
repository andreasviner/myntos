/**
 * Cloudflare Worker in front of the FastAPI container.
 *
 * Every request to api.myntos.no lands here, is routed to a Durable Object,
 * and is proxied into the container built from backend/Dockerfile.
 *
 * Deliberately dependency-free: it uses the low-level `ctx.container` API from
 * the `cloudflare:workers` runtime module rather than the `@cloudflare/containers`
 * npm package. backend/ is a Python project with no package.json, and
 * upload.bat runs wrangler straight from frontend/node_modules without an
 * install step here — a bare npm import would fail to bundle. Everything that
 * package would give us (wait-for-port, idle shutdown, crash detection) is
 * implemented below.
 */

import { DurableObject } from "cloudflare:workers";

/** Port uvicorn listens on inside the image (backend/Dockerfile: EXPOSE 8000). */
const CONTAINER_PORT = 8000;

/**
 * Readiness probe target. myntos.api.v1.health touches no database, so it
 * answers as soon as FastAPI has bound the port instead of blocking on a cold
 * Postgres connection.
 */
const READINESS_PATH = "/api/v1/health";

/** Cloudflare gives a container ~20 s to come up; stay inside that. */
const READY_TIMEOUT_MS = 20_000;
const READY_POLL_MS = 250;

/**
 * Containers bill memory and disk the whole time they are awake, not just
 * while burning CPU, so an idle instance has to be stopped rather than left
 * parked. The next request pays a ~2-3 s cold start.
 */
const IDLE_SHUTDOWN_MS = 10 * 60 * 1000;
const ALARM_REWRITE_INTERVAL_MS = 30_000;

/**
 * One named instance, so all traffic shares a single container and a single
 * pool of Postgres connections. The backend keeps no in-memory state (sessions
 * live in the database), so this can become a pool later by picking the name
 * per request — but every extra name is another container being billed.
 */
const INSTANCE_NAME = "myntos-api";

/**
 * Worker vars and secrets to hand to the container process, matched to
 * myntos.core.config.Settings. Listed explicitly rather than spreading `env`:
 * that object also holds bindings (the Durable Object namespace itself), which
 * are not strings and have no business in the container's environment.
 */
const FORWARDED_ENV = [
  "ENVIRONMENT",
  "LOG_LEVEL",
  "DATABASE_URL",
  "CORS_ORIGINS",
  "FRONTEND_BASE_URL",
  "SESSION_COOKIE_NAME",
  "SESSION_COOKIE_DOMAIN",
  "SESSION_LIFETIME_DAYS",
  "SESSION_IDLE_TIMEOUT_HOURS",
  "PASSWORD_MIN_LENGTH",
  "LOGIN_MAX_FAILED_ATTEMPTS",
  "LOGIN_LOCKOUT_MINUTES",
  "INVITATION_LIFETIME_DAYS",
  "GOOGLE_CLIENT_ID",
  "GOOGLE_CLIENT_SECRET",
  "GOOGLE_REDIRECT_URI",
  "GOOGLE_WELL_KNOWN_URL",
  "MICROSOFT_CLIENT_ID",
  "MICROSOFT_CLIENT_SECRET",
  "MICROSOFT_REDIRECT_URI",
  "MICROSOFT_TENANT",
  "MICROSOFT_WELL_KNOWN_URL",
  "VIPPS_CLIENT_ID",
  "VIPPS_CLIENT_SECRET",
  "VIPPS_REDIRECT_URI",
  "VIPPS_WELL_KNOWN_URL",
  "MASKINPORTEN_CLIENT_ID",
  "MASKINPORTEN_WELL_KNOWN_URL",
  "MASKINPORTEN_SCOPES",
  "IDPORTEN_CLIENT_ID",
  "IDPORTEN_WELL_KNOWN_URL",
  "IDPORTEN_REDIRECT_URI",
  "ALTINN_PLATFORM_BASE_URL",
  "SKATTEETATEN_API_BASE_URL",
  "STORAGE_BACKEND",
  "R2_ACCOUNT_ID",
  "R2_ACCESS_KEY_ID",
  "R2_SECRET_ACCESS_KEY",
  "R2_BUCKET",
  "R2_JURISDICTION",
  "R2_ENDPOINT_URL",
  // Løynd. Missing from this list until now, which is why the deployed API
  // behaved as though no key existed: myntos.integrations.loynd reads
  // LOYND_KEY out of the process environment, the key was set in the repo's
  // .env, and .env is a local file that never reaches the container. Every
  // uploaded bilag therefore took the no-model path in production while
  // working perfectly on a developer's machine, and said nothing about it,
  // because an unavailable model is deliberately not an error for the user.
  "LOYND_KEY",
  "LOYND_BASE_URL",
  "LOYND_MODEL",
];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * CORS headers for responses this Worker generates *itself*.
 *
 * Healthy traffic never comes through here: FastAPI's CORSMiddleware
 * (myntos.main) owns the CORS contract and stays the single source of truth.
 * But the 503s below are produced before the container is ever reached, and a
 * 503 without these headers is not seen by the browser as a 503 at all — the
 * CORS check runs first, so the page gets an opaque "No
 * 'Access-Control-Allow-Origin' header" error and no way to tell a cold start
 * apart from a misconfiguration. Same allow-list, read from the same var.
 */
function corsHeaders(request, env) {
  const origin = request.headers.get("Origin");
  if (!origin) return {};

  let allowed;
  try {
    allowed = JSON.parse(env.CORS_ORIGINS ?? "[]");
  } catch {
    return {};
  }
  if (!Array.isArray(allowed)) return {};
  if (!allowed.includes(origin) && !allowed.includes("*")) return {};

  return {
    "access-control-allow-origin": origin,
    "access-control-allow-credentials": "true",
    // The allow-list is per-origin, so caches must key on Origin.
    vary: "Origin",
  };
}

/**
 * Preflight answered locally, but *only* on the degraded path — a preflight
 * needs a 2xx, so attaching headers to the 503 would not be enough to let the
 * browser proceed to the real request and learn the backend is down.
 *
 * The requested method and headers are reflected rather than checked against a
 * copy of FastAPI's list: duplicating that list here would let the two drift
 * silently. Reflecting grants nothing, because the request this unblocks is
 * itself about to be answered with the 503 below.
 */
function preflightWhileDown(request, env) {
  const cors = corsHeaders(request, env);
  if (!cors["access-control-allow-origin"]) return null;
  if (request.method !== "OPTIONS") return null;
  if (!request.headers.get("Access-Control-Request-Method")) return null;

  const requested = request.headers.get("Access-Control-Request-Headers");
  return new Response(null, {
    status: 204,
    headers: {
      ...cors,
      "access-control-allow-methods": request.headers.get("Access-Control-Request-Method"),
      ...(requested ? { "access-control-allow-headers": requested } : {}),
      // Do not let a browser cache the outage-time answer.
      "access-control-max-age": "0",
    },
  });
}

function containerEnv(env) {
  const out = {};
  for (const key of FORWARDED_ENV) {
    const value = env[key];
    if (typeof value === "string" && value !== "") out[key] = value;
  }
  return out;
}

export class BackendContainer extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    /** Cached across requests so the happy path costs nothing. */
    this.ready = false;
    this.lastAlarmWrite = 0;
  }

  async fetch(request) {
    try {
      await this.#ensureRunning();
    } catch (err) {
      console.error("container startup failed:", err);
      return (
        preflightWhileDown(request, this.env) ??
        json({ detail: "Backend is starting up, try again shortly." }, 503, {
          ...corsHeaders(request, this.env),
          // A cold start is transient; tell the caller so rather than making
          // the frontend guess at a backoff.
          "retry-after": "5",
        })
      );
    }

    await this.#touchIdleTimer();

    const url = new URL(request.url);
    const headers = new Headers(request.headers);

    // uvicorn --proxy-headers reads these. Without them request.client.host is
    // the Worker's internal address, which would make the login audit trail and
    // the per-account lockout in api/v1/auth.py record one constant "client".
    const clientIp = headers.get("cf-connecting-ip");
    if (clientIp) headers.set("x-forwarded-for", clientIp);
    headers.set("x-forwarded-proto", url.protocol.replace(":", ""));
    headers.set("x-forwarded-host", url.host);

    return this.ctx.container.getTcpPort(CONTAINER_PORT).fetch(
      `http://container${url.pathname}${url.search}`,
      {
        method: request.method,
        headers,
        body: request.body,
        // The federated sign-in start endpoints answer with a 302 to Google /
        // Microsoft / Vipps. "follow" would make the Worker chase it and return
        // the provider's page instead of the redirect the browser needs.
        redirect: "manual",
      },
    );
  }

  async #ensureRunning() {
    const container = this.ctx.container;
    if (!container) {
      throw new Error(
        "no container attached — the `containers` block in wrangler.jsonc must name this class",
      );
    }
    if (this.ready && container.running) return;

    // Serialise startup: on a cold start every queued request would otherwise
    // launch its own probe loop against a container that is still booting.
    //
    // The callback must not be allowed to throw. Cloudflare terminates and
    // resets the Durable Object when it does, so a container that is merely
    // slow to boot would take the object down mid-request instead of reaching
    // the caller's catch — and the 503 *with* CORS headers built there would
    // never be sent. The browser would see a bare internal error, report it as
    // "No 'Access-Control-Allow-Origin' header", and a cold start would look
    // like a misconfigured allow-list. So: capture, then rethrow outside.
    let failure = null;
    await this.ctx.blockConcurrencyWhile(async () => {
      try {
        if (this.ready && container.running) return;

        if (!container.running) {
          container.start({ env: containerEnv(this.env), enableInternet: true });
          // A crash, an OOM kill, or a rolling deploy has to clear the cached
          // flag — otherwise later requests are proxied into a dead process.
          const invalidate = () => {
            this.ready = false;
          };
          container.monitor().then(invalidate, invalidate);
        }

        await this.#waitForReady(container.getTcpPort(CONTAINER_PORT));
        this.ready = true;
      } catch (err) {
        failure = err;
      }
    });
    if (failure) throw failure;
  }

  async #waitForReady(port) {
    const deadline = Date.now() + READY_TIMEOUT_MS;
    let lastError = "no response";

    while (Date.now() < deadline) {
      try {
        const probe = await port.fetch(`http://container${READINESS_PATH}`);
        // Drain it — an unread body holds the connection open.
        await probe.body?.cancel();
        if (probe.ok) return;
        lastError = `health check returned ${probe.status}`;
      } catch (err) {
        lastError = err instanceof Error ? err.message : String(err);
      }
      await sleep(READY_POLL_MS);
    }

    throw new Error(`container not ready after ${READY_TIMEOUT_MS} ms (${lastError})`);
  }

  async #touchIdleTimer() {
    const now = Date.now();
    // Throttled: rewriting the alarm on every request is a storage write per
    // request, and shutdown timing does not need that precision.
    if (now - this.lastAlarmWrite < ALARM_REWRITE_INTERVAL_MS) return;
    this.lastAlarmWrite = now;
    await this.ctx.storage.setAlarm(now + IDLE_SHUTDOWN_MS);
  }

  async alarm() {
    if (this.ctx.container?.running) this.ctx.container.destroy();
    this.ready = false;
    this.lastAlarmWrite = 0;
  }
}

function json(body, status, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

export default {
  async fetch(request, env) {
    // "eu" keeps the Durable Object — and with it the container scheduled
    // alongside it — inside the EEA, matching containers[].constraints in
    // wrangler.jsonc and the storage-location rules in docs/ARCHITECTURE.md.
    const ns = env.BACKEND.jurisdiction("eu");
    const stub = ns.get(ns.idFromName(INSTANCE_NAME));

    try {
      return await stub.fetch(request);
    } catch (err) {
      console.error("proxy to container failed:", err);
      return (
        preflightWhileDown(request, env) ??
        json({ detail: "Backend unavailable." }, 503, corsHeaders(request, env))
      );
    }
  },
};
