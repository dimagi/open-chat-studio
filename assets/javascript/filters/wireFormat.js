/**
 * Read and write the dynamic-filter query-param wire format.
 *
 * A filter is carried as a pair of params keyed by its column: `f_<column>` holds the value and
 * `op_<column>` the operator. Because the key contains the column rather than a position, more
 * than one filter on the same column (a date range is `after X` AND `before Y`) can only be
 * expressed as a *repeated* key. Every read and write here therefore uses list semantics --
 * `getAll` and `append`, never `get` and `set`, which would keep just one of the two.
 *
 * This is the single source of truth for the format on the JS side. The Python equivalents live
 * in apps/web/dynamic_filters/datastructures.py (FilterParams / FilterParams.to_query).
 */

/** Operators whose value is a tilde-delimited CSV list rather than a scalar. */
export const LIST_OPERATORS = new Set(['any of', 'all of', 'excludes']);

const VALUE_PREFIX = 'f_';
const OPERATOR_PREFIX = 'op_';

/**
 * Parse a query string into an ordered list of `{column, operator, value}` filters.
 *
 * Columns appear in the order they first occur in the query string, and each column's values are
 * zipped positionally with its operators. A malformed query string with mismatched f_/op_ counts
 * degrades to the shorter list rather than throwing, matching the Python parser.
 */
export function parseFilterParams(queryString) {
  const params = new URLSearchParams(queryString);
  const columns = [];

  for (const key of params.keys()) {
    if (key.startsWith(VALUE_PREFIX)) {
      const column = key.slice(VALUE_PREFIX.length);
      if (column && !columns.includes(column)) {
        columns.push(column);
      }
    }
  }

  const filters = [];
  for (const column of columns) {
    const values = params.getAll(`${VALUE_PREFIX}${column}`);
    const operators = params.getAll(`${OPERATOR_PREFIX}${column}`);
    const paired = Math.min(values.length, operators.length);
    for (let i = 0; i < paired; i++) {
      if (values[i] && operators[i]) {
        filters.push({column, operator: operators[i], value: values[i]});
      }
    }
  }
  return filters;
}

/**
 * Serialize `{column, operator, value}` filters into URLSearchParams, preserving repeats.
 *
 * Values are expected to already be in wire form -- scalars as-is, list-operator values as
 * tilde-delimited CSV (see serializeCSVTildeValues).
 */
export function buildFilterParams(filters) {
  const params = new URLSearchParams();
  for (const {column, operator, value} of filters) {
    if (!column || !operator || !value) {
      continue;
    }
    params.append(`${VALUE_PREFIX}${column}`, value);
    params.append(`${OPERATOR_PREFIX}${column}`, operator);
  }
  return params;
}

/**
 * Replace the filter params in `search` with `filters`, leaving every other param untouched.
 *
 * Non-filter params (pagination, sort order) must survive a filter change, so they are kept in
 * place while all `f_*` / `op_*` keys are dropped and rebuilt.
 */
export function replaceFilterParams(search, filters) {
  const params = new URLSearchParams(search);
  for (const key of [...params.keys()]) {
    if (key.startsWith(VALUE_PREFIX) || key.startsWith(OPERATOR_PREFIX)) {
      params.delete(key);
    }
  }
  for (const [key, value] of buildFilterParams(filters)) {
    params.append(key, value);
  }
  return params;
}

/**
 * Build the `values` payload for an htmx request from `filters`.
 *
 * Keys map to arrays because a column may carry several filters; htmx appends each element as its
 * own param, so a repeated column survives the request.
 */
export function filterParamsToRequestValues(filters) {
  const values = {};
  for (const [key, value] of buildFilterParams(filters)) {
    (values[key] ??= []).push(value);
  }
  return values;
}
