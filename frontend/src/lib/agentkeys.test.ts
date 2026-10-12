/**
 * The words the Tilgang page uses for keys, and the text a person pastes.
 *
 * A scope shown as narrower than it is would be a person believing their agent
 * cannot post when it can, so the mapping is asserted rather than trusted; and
 * the paste text is the agent's only pointer to its instructions.
 */

import { describe, expect, it } from 'vitest';

import type { AgentKey } from './api';
import {
  isLive,
  llmsUrl,
  modeOf,
  pasteText,
  rightsLabel,
  scopesFor,
  settleModes,
  stateLabel,
} from './agentkeys';

function key(overrides: Partial<AgentKey>): AgentKey {
  return {
    id: 1,
    company_id: 1,
    name: 'Claude',
    scopes: ['read'],
    approval_scopes: [],
    state: 'active',
    person_name: 'Kari Nordmann',
    is_own: true,
    created_at: '2026-10-10T10:00:00Z',
    expires_at: '2027-10-10T10:00:00Z',
    connection_key_expires_at: '2026-10-11T10:00:00Z',
    exchanged_at: '2026-10-10T10:05:00Z',
    last_used_at: '2026-10-10T10:05:00Z',
    last_used_client: 'python-httpx',
    revoked_at: null,
    revoked_reason: null,
    ...overrides,
  };
}

describe('pasteText', () => {
  it('carries the key and tells the agent where to start', () => {
    const text = pasteText('DIALGA AS', 'myntos_ck_abc', 'https://myntos.no');
    expect(text).toContain('myntos_ck_abc');
    expect(text).toContain('DIALGA AS');
    expect(text).toContain('https://myntos.no/llms.txt');
  });

  it('points at the public site when the build names none', () => {
    expect(llmsUrl(undefined)).toBe('https://myntos.no/llms.txt');
  });
});

describe('rightsLabel', () => {
  it('says what a key does on its own and what it asks for', () => {
    expect(rightsLabel({ scopes: ['read'], approval_scopes: [] })).toBe('Bare lese');
    expect(rightsLabel({ scopes: ['read', 'write'], approval_scopes: ['post'] })).toBe(
      'Forbereder selv, bokfører etter godkjenning',
    );
    expect(rightsLabel({ scopes: ['read'], approval_scopes: ['write', 'post'] })).toBe(
      'Forbereder etter godkjenning, bokfører etter godkjenning',
    );
  });

  it('never shows a key that posts on its own as anything narrower, and says so first', () => {
    expect(rightsLabel({ scopes: ['read', 'write', 'post'], approval_scopes: [] })).toBe(
      'Bokfører selv, forbereder selv',
    );
    expect(rightsLabel({ scopes: ['read', 'post'], approval_scopes: [] })).toMatch(/^Bokfører selv/);
  });
});

describe('the choices for a key', () => {
  it('never lets posting reach further than preparing', () => {
    expect(settleModes('ask', 'self')).toEqual({ write: 'ask', post: 'ask' });
    expect(settleModes('no', 'ask')).toEqual({ write: 'no', post: 'no' });
    expect(settleModes('self', 'ask')).toEqual({ write: 'self', post: 'ask' });
  });

  it('turns them into what the backend takes', () => {
    expect(scopesFor('ask', 'ask')).toEqual({ scopes: ['read'], approvalScopes: ['write', 'post'] });
    expect(scopesFor('self', 'ask')).toEqual({ scopes: ['read', 'write'], approvalScopes: ['post'] });
    expect(scopesFor('self', 'self')).toEqual({
      scopes: ['read', 'write', 'post'],
      approvalScopes: [],
    });
    expect(scopesFor('no', 'self')).toEqual({ scopes: ['read'], approvalScopes: [] });
  });

  it('reads them back off a key', () => {
    const asking = key({ scopes: ['read', 'write'], approval_scopes: ['post'] });
    expect([modeOf(asking, 'write'), modeOf(asking, 'post')]).toEqual(['self', 'ask']);
  });
});

describe('stateLabel', () => {
  it('says why a key stopped when Myntos stopped it', () => {
    expect(stateLabel(key({ state: 'stopped', revoked_reason: 'reuse' }))).toBe(
      'Stoppet: brukt fra to steder',
    );
  });

  it('falls back to a plain word for a reason it does not know', () => {
    expect(stateLabel(key({ state: 'stopped', revoked_reason: 'something_new' }))).toBe('Stoppet');
  });

  it('only offers to stop a key that still works', () => {
    expect(isLive(key({ state: 'waiting' }))).toBe(true);
    expect(isLive(key({ state: 'active' }))).toBe(true);
    expect(isLive(key({ state: 'expired' }))).toBe(false);
    expect(isLive(key({ state: 'stopped' }))).toBe(false);
  });
});
