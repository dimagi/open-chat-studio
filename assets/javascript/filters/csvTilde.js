/**
 * Shared serialize/parse for the tilde-delimited CSV wire format used by dynamic filter
 * list values (f_* params). This is the single source of truth for the format on the JS
 * side; the Python equivalents live in apps/web/dynamic_filters/datastructures.py
 * (serialize_csv_tilde_values / _parse_csv_tilde_values). Values containing "~" or '"'
 * are quoted (matching csv.QUOTE_MINIMAL) so they round-trip losslessly.
 */

export function serializeCSVTildeValues(values) {
  return values.map(v => {
    const str = String(v);
    if (str.includes('~') || str.includes('"')) {
      return `"${str.replace(/"/g, '""')}"`;
    }
    return str;
  }).join('~');
}

export function parseCSVTildeValue(csvString) {
  const values = [];
  let current = '';
  let inQuotes = false;

  for (let i = 0; i < csvString.length; i++) {
    const char = csvString[i];

    if (char === '"') {
      if (inQuotes && csvString[i + 1] === '"') {
        // Escaped quote ("")
        current += '"';
        i++;
      } else {
        // Start/end quoted section
        inQuotes = !inQuotes;
      }
    } else if (char === '~' && !inQuotes) {
      values.push(current);
      current = '';
    } else {
      current += char;
    }
  }

  values.push(current);

  return values;
}
