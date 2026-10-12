/**
 * Every help article in docs/agents/help, at /llms/help/<area>/<topic>.md.
 *
 * The half of the agent documentation written for an AI that is helping a
 * person use the web app: ChatGPT asked «hvordan sender jeg en faktura i
 * Myntos?» (docs/agents/README.md). It has no key and no access to the books;
 * these tell it what to click.
 *
 * Found by globbing, like the skills, so an article added in the repository is
 * published by the next build. `llms.txt` links every one, and the backend's
 * documentation test checks that it does, and that every page of the app is
 * explained by at least one.
 */

import type { APIRoute, GetStaticPaths } from 'astro';

const articles = import.meta.glob<string>('../../../../../docs/agents/help/*/*.md', {
  query: '?raw',
  import: 'default',
  eager: true,
});

export const prerender = true;

export const getStaticPaths = (() =>
  Object.entries(articles).map(([path, text]) => {
    const [area, file] = path.split('/').slice(-2);
    return {
      params: { topic: `${area}/${(file ?? '').replace(/\.md$/, '')}` },
      props: { text },
    };
  })) satisfies GetStaticPaths;

export const GET: APIRoute = ({ props }) =>
  new Response(String(props.text), {
    headers: { 'Content-Type': 'text/markdown; charset=utf-8' },
  });
