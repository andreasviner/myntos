import { describe, expect, it } from 'vitest';

import {
  forgetKey,
  frameLine,
  journalCommand,
  keepKey,
  keyProblem,
  ownFrame,
  shortType,
  storedKey,
  timesLabel,
} from './drift';

class MemoryStorage implements Storage {
  private items = new Map<string, string>();
  get length(): number {
    return this.items.size;
  }
  clear(): void {
    this.items.clear();
  }
  getItem(key: string): string | null {
    return this.items.get(key) ?? null;
  }
  key(index: number): string | null {
    return [...this.items.keys()][index] ?? null;
  }
  removeItem(key: string): void {
    this.items.delete(key);
  }
  setItem(key: string, value: string): void {
    this.items.set(key, value);
  }
}

const KEY = 'myntos_op_' + 'a'.repeat(43);
const DIGEST = 'ab'.repeat(32);

describe('the operator key', () => {
  it('is kept for the tab and forgotten on «Lås»', () => {
    const storage = new MemoryStorage();
    expect(storedKey(storage)).toBeNull();
    keepKey(KEY, storage);
    expect(storedKey(storage)).toBe(KEY);
    forgetKey(storage);
    expect(storedKey(storage)).toBeNull();
  });

  it('survives storage that refuses to be used', () => {
    const refusing = new MemoryStorage();
    refusing.getItem = () => {
      throw new Error('SecurityError');
    };
    refusing.setItem = () => {
      throw new Error('QuotaExceededError');
    };
    expect(() => keepKey(KEY, refusing)).not.toThrow();
    expect(storedKey(refusing)).toBeNull();
    expect(storedKey(null)).toBeNull();
  });

  it('is told apart from the digest in .env before anything is sent', () => {
    expect(keyProblem(KEY)).toBeNull();
    expect(keyProblem(`  ${KEY}\n`)).toBeNull();
    expect(keyProblem(DIGEST)).toBe('digest');
    expect(keyProblem(DIGEST.toUpperCase())).toBe('digest');
    expect(keyProblem('')).toBe('empty');
    expect(keyProblem('myntos_ak_something')).toBe('not-a-key');
  });
});

describe('a recorded failure', () => {
  const frames = [
    { file: 'starlette/routing.py', line: 74, function: 'app', code: 'await func()' },
    { file: 'myntos/saft/jobs.py', line: 610, function: 'read', code: 'raise ImportFailed()' },
    { file: 'sqlalchemy/engine/base.py', line: 1967, function: 'execute', code: null },
  ];

  it('is named by its type without the module', () => {
    expect(shortType('myntos.saft.jobs.ImportFailed')).toBe('ImportFailed');
    expect(shortType('KeyError')).toBe('KeyError');
    expect(shortType(null)).toBe('Logget uten unntak');
  });

  it('points at the innermost line of Myntos’s own code', () => {
    const own = ownFrame(frames);
    expect(own?.file).toBe('myntos/saft/jobs.py');
    expect(frameLine(own!)).toBe('myntos/saft/jobs.py:610 i read');
    expect(ownFrame(frames.filter((frame) => !frame.file.startsWith('myntos/')))?.function).toBe(
      'execute',
    );
    expect(ownFrame([])).toBeNull();
  });

  it('says how often without a stress accent', () => {
    expect(timesLabel(1)).toBe('1 gang');
    expect(timesLabel(3)).toBe('3 ganger');
  });

  it('gives the journal window around the latest occurrence, in UTC', () => {
    expect(journalCommand({ last_seen_at: '2026-10-28T19:47:30+00:00' })).toBe(
      'journalctl -u myntos-backend --since "2026-10-28 19:46 UTC" --until "2026-10-28 19:49 UTC"',
    );
  });
});
