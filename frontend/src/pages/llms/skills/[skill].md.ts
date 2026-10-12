/**
 * Every skill in docs/agents/skills, at /llms/skills/<name>.md.
 *
 * The half of the agent documentation that an agent working in the books
 * through the API reads (docs/agents/README.md); the other half, for an AI
 * helping a person use the web app, is published beside it by
 * `../help/[...topic].md.ts`.
 *
 * One page per folder, found by globbing rather than listed, so a skill added
 * in the repository is published by the next build without anybody touching
 * the frontend. `llms.txt` links them by this path, and the backend's
 * documentation test checks that it lists every one.
 */

import type { APIRoute, GetStaticPaths } from 'astro';

const skills = import.meta.glob<string>('../../../../../docs/agents/skills/*/SKILL.md', {
  query: '?raw',
  import: 'default',
  eager: true,
});

export const prerender = true;

export const getStaticPaths = (() =>
  Object.entries(skills).map(([path, text]) => ({
    params: { skill: path.split('/').at(-2) ?? '' },
    props: { text },
  }))) satisfies GetStaticPaths;

export const GET: APIRoute = ({ props }) =>
  new Response(String(props.text), {
    headers: { 'Content-Type': 'text/markdown; charset=utf-8' },
  });
