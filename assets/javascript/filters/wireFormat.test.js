import {describe, expect, it} from 'vitest';

import {
  buildFilterParams,
  filterParamsToRequestValues,
  parseFilterParams,
  replaceFilterParams,
} from './wireFormat.js';

// A date range is the canonical two-filters-on-one-column case: `after X` AND `before Y`.
const DATE_RANGE = [
  {column: 'first_message', operator: 'after', value: '2026-01-01'},
  {column: 'first_message', operator: 'before', value: '2026-02-01'},
];
const DATE_RANGE_QUERY =
  'f_first_message=2026-01-01&op_first_message=after&f_first_message=2026-02-01&op_first_message=before';

describe('parseFilterParams', () => {
  it('reads both filters when a column is repeated', () => {
    expect(parseFilterParams(DATE_RANGE_QUERY)).toEqual(DATE_RANGE);
  });

  it('reads a single filter', () => {
    expect(parseFilterParams('f_state=active&op_state=equals')).toEqual([
      {column: 'state', operator: 'equals', value: 'active'},
    ]);
  });

  it('keeps columns in the order they first appear', () => {
    const filters = parseFilterParams('f_state=active&op_state=equals&f_tags=a&op_tags=any+of');
    expect(filters.map(f => f.column)).toEqual(['state', 'tags']);
  });

  it('ignores params that are not filters', () => {
    expect(parseFilterParams('page=2&sort=-created&f_state=active&op_state=equals')).toEqual([
      {column: 'state', operator: 'equals', value: 'active'},
    ]);
  });

  it('degrades to the shorter list when f_/op_ counts do not match', () => {
    const filters = parseFilterParams('f_first_message=2026-01-01&op_first_message=after&f_first_message=2026-02-01');
    expect(filters).toEqual([{column: 'first_message', operator: 'after', value: '2026-01-01'}]);
  });

  it('skips a filter with no operator', () => {
    expect(parseFilterParams('f_state=active')).toEqual([]);
  });

  it('returns nothing for an empty query string', () => {
    expect(parseFilterParams('')).toEqual([]);
  });

  it('leaves a tilde-delimited list value in wire form', () => {
    expect(parseFilterParams('f_tags=a~b&op_tags=any+of')).toEqual([
      {column: 'tags', operator: 'any of', value: 'a~b'},
    ]);
  });
});

describe('buildFilterParams', () => {
  it('writes both filters when a column is repeated', () => {
    const params = buildFilterParams(DATE_RANGE);
    expect(params.getAll('f_first_message')).toEqual(['2026-01-01', '2026-02-01']);
    expect(params.getAll('op_first_message')).toEqual(['after', 'before']);
  });

  it('drops incomplete filters', () => {
    const params = buildFilterParams([
      {column: 'state', operator: 'equals', value: ''},
      {column: 'tags', operator: '', value: 'a'},
      {column: '', operator: 'equals', value: 'a'},
    ]);
    expect([...params.keys()]).toEqual([]);
  });

  it('round-trips through parseFilterParams', () => {
    expect(parseFilterParams(buildFilterParams(DATE_RANGE).toString())).toEqual(DATE_RANGE);
  });
});

describe('replaceFilterParams', () => {
  it('replaces existing filter params rather than accumulating them', () => {
    const params = replaceFilterParams('f_state=archived&op_state=equals', DATE_RANGE);
    expect(params.getAll('f_state')).toEqual([]);
    expect(params.getAll('f_first_message')).toEqual(['2026-01-01', '2026-02-01']);
  });

  it('clears every value of a previously repeated column', () => {
    const params = replaceFilterParams(DATE_RANGE_QUERY, [{column: 'state', operator: 'equals', value: 'active'}]);
    expect(params.getAll('f_first_message')).toEqual([]);
    expect(params.getAll('op_first_message')).toEqual([]);
  });

  it('preserves params that are not filters', () => {
    const params = replaceFilterParams('page=2&sort=-created&f_state=archived&op_state=equals', DATE_RANGE);
    expect(params.get('page')).toBe('2');
    expect(params.get('sort')).toBe('-created');
  });

  it('removes all filter params when there are no filters', () => {
    const params = replaceFilterParams(`page=2&${DATE_RANGE_QUERY}`, []);
    expect(params.toString()).toBe('page=2');
  });
});

describe('filterParamsToRequestValues', () => {
  it('groups a repeated column into arrays so htmx appends each value', () => {
    expect(filterParamsToRequestValues(DATE_RANGE)).toEqual({
      f_first_message: ['2026-01-01', '2026-02-01'],
      op_first_message: ['after', 'before'],
    });
  });

  it('uses a single-element array for a single filter', () => {
    expect(filterParamsToRequestValues([{column: 'state', operator: 'equals', value: 'active'}])).toEqual({
      f_state: ['active'],
      op_state: ['equals'],
    });
  });

  it('is empty when there are no filters', () => {
    expect(filterParamsToRequestValues([])).toEqual({});
  });
});
