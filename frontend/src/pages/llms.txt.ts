/**
 * The front door for agents: docs/agents/llms.txt, published as it is.
 *
 * Read from the repository at build time rather than copied into public/, so
 * the published text and the one the documentation test checks are the same
 * file and cannot drift (docs/agents-and-api.md rule 12).
 */

import type { APIRoute } from 'astro';

import text from '../../../docs/agents/llms.txt?raw';

export const prerender = true;

export const GET: APIRoute = () =>
  new Response(text, { headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
