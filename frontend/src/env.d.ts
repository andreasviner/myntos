/// <reference types="astro/client" />

interface ImportMetaEnv {
  /**
   * Absolute base URL of the Myntos API, e.g. `https://api.myntos.no`.
   *
   * Inlined at build time, so a deployment that points at the wrong backend
   * has to be rebuilt, not reconfigured. Defaults to `http://localhost:8000`
   * when unset — see `frontend/.env.example`.
   */
  readonly PUBLIC_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
