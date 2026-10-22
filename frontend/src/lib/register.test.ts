import { describe, expect, it } from 'vitest';

import { deletedYear, standing } from './register';

const live = {
  bankrupt: false,
  under_liquidation: false,
  under_forced_liquidation: false,
  deleted_on: null,
};

describe('standing', () => {
  it('says nothing about a company in business', () => {
    expect(standing(live)).toEqual([]);
  });

  it('says a deleted company is gone, first', () => {
    // The register's own answer for a real one on 2026-10-22: the date and
    // nothing else. Before this it read as a live company with no address.
    const marks = standing({ ...live, deleted_on: '2026-07-21', under_liquidation: true });
    expect(marks).toHaveLength(2);
    expect(marks[0]).toMatch(/^slettet fra Enhetsregisteret .*2026/);
    expect(marks[1]).toBe('under avvikling');
  });

  it('names each of the register flags', () => {
    expect(
      standing({ ...live, bankrupt: true, under_liquidation: true, under_forced_liquidation: true }),
    ).toEqual(['registrert konkurs', 'under avvikling', 'under tvangsavvikling']);
  });

  it('reads an older answer without the field as live', () => {
    expect(standing({ ...live, deleted_on: undefined })).toEqual([]);
  });
});

describe('deletedYear', () => {
  it('is the year of the deletion', () => {
    expect(deletedYear({ deleted_on: '2026-07-21' })).toBe(2026);
  });

  it('is null for a live unit', () => {
    expect(deletedYear({ deleted_on: null })).toBeNull();
    expect(deletedYear({})).toBeNull();
  });
});
