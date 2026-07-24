/**
 * Reconcile Dimagi OpenAI + Anthropic account spend against OCS teams, into this Sheet.
 *
 * Google Apps Script port of scripts/provider_spend_reconcile.py. Run `reconcile()`
 * (or use the "Spend Report" menu) to (re)build the "Spend Reconciliation" sheet.
 *
 * Setup (Project Settings -> Script Properties):
 *   OPENAI_ADMIN_KEY      OpenAI admin key (platform.openai.com/settings/organization/admin-keys)
 *   ANTHROPIC_ADMIN_KEY   Anthropic admin key (sk-ant-admin...)
 *   OCS_BASE_URL          e.g. https://www.openchatstudio.com
 *   OCS_TOKEN             value of PROVIDER_REPORTING_API_TOKEN configured on OCS
 *   MONTH (optional)      YYYY-MM to report one calendar month (matches invoices)
 *   START, END (optional) YYYY-MM-DD custom range (END exclusive); ignored if MONTH set
 *   Default (none set):   the last complete calendar month
 *
 * Easiest month control: put YYYY-MM in a cell and name that cell "ReportMonth"
 * (Data -> Named ranges), on any tab other than the generated report. Edit the
 * cell and hit "Spend Report -> Refresh" -- no Script Property editing needed.
 * The cell takes precedence over the MONTH property.
 *
 * Attribution model (same as the Python version):
 *   - OpenAI cost is grouped by api_key_id -> EXACT per-key cost.
 *   - Anthropic cost is workspace-level only, so each workspace's cost is split
 *     across its keys by token share -> an ESTIMATE.
 *   Each key maps to an OCS team via the last-4 of its redacted value; keys OCS
 *   doesn't know (and console usage) are itemized by owner so they can be assigned.
 *
 * VERIFY against a live response: Anthropic users/workspaces list endpoints + their
 * id/email/name fields, and that cost_report `amount` is minor units (cents).
 */

var OPENAI_BASE = 'https://api.openai.com/v1';
var ANTHROPIC_BASE = 'https://api.anthropic.com/v1';
var ANTHROPIC_VERSION = '2023-06-01';
var SHEET_NAME = 'Spend Reconciliation';
var CONSOLE_LABEL = '(console / no API key)';

function onOpen() {
  SpreadsheetApp.getUi().createMenu('Spend Report').addItem('Refresh', 'reconcile').addToUi();
}

function reconcile() {
  var props = PropertiesService.getScriptProperties();
  var cfg = {
    openaiKey: reqProp_(props, 'OPENAI_ADMIN_KEY'),
    anthropicKey: reqProp_(props, 'ANTHROPIC_ADMIN_KEY'),
    ocsBase: reqProp_(props, 'OCS_BASE_URL').replace(/\/+$/, ''),
    ocsToken: reqProp_(props, 'OCS_TOKEN'),
  };
  var range = dateRange_(props);

  var providerKeys = ocsProviderKeys_(cfg);
  var openai = attribute_(
    'openai',
    openaiCharges_(cfg, range.start, range.end),
    openaiKeyInfo_(cfg),
    teamByLast4_(providerKeys.providers, 'openai')
  );
  var anthropic = attribute_(
    'anthropic',
    anthropicCharges_(cfg, range.start, range.end),
    anthropicKeyInfo_(cfg),
    teamByLast4_(providerKeys.providers, 'anthropic')
  );
  var ocsUsage = ocsUsage_(cfg, range.start, range.end);

  writeReport_(range, openai, anthropic, ocsUsage, teamMetaByName_(providerKeys.providers));
}

// --------------------------------------------------------------------------- //
// HTTP
// --------------------------------------------------------------------------- //
function fetchJson_(url, headers) {
  var lastErr = '';
  for (var attempt = 0; attempt < 3; attempt++) {
    var resp = UrlFetchApp.fetch(url, { method: 'get', headers: headers, muteHttpExceptions: true });
    var code = resp.getResponseCode();
    if (code >= 200 && code < 300) {
      return JSON.parse(resp.getContentText());
    }
    if (code === 429 || code >= 500) {
      lastErr = code + ' ' + resp.getContentText().slice(0, 200);
      Utilities.sleep(1000 * (attempt + 1)); // retry transient errors
      continue;
    }
    throw new Error('HTTP ' + code + ' for ' + url + ' :: ' + resp.getContentText().slice(0, 300));
  }
  throw new Error('HTTP error after retries for ' + url + ' :: ' + lastErr);
}

function buildQuery_(params) {
  var parts = [];
  Object.keys(params).forEach(function (key) {
    var value = params[key];
    if (Array.isArray(value)) {
      value.forEach(function (item) {
        parts.push(encodeURIComponent(key) + '=' + encodeURIComponent(item));
      });
    } else {
      parts.push(encodeURIComponent(key) + '=' + encodeURIComponent(value));
    }
  });
  return parts.length ? '?' + parts.join('&') : '';
}

// --------------------------------------------------------------------------- //
// OpenAI
// --------------------------------------------------------------------------- //
function openaiHeaders_(cfg) {
  return { Authorization: 'Bearer ' + cfg.openaiKey };
}

function openaiList_(cfg, path, params) {
  var out = [];
  var after = null;
  do {
    var p = Object.assign({ limit: 100 }, params || {});
    if (after) p.after = after;
    var data = fetchJson_(OPENAI_BASE + path + buildQuery_(p), openaiHeaders_(cfg));
    (data.data || []).forEach(function (row) { out.push(row); });
    after = data.has_more ? data.last_id : null;
  } while (after);
  return out;
}

function openaiCostRows_(cfg, params) {
  // Terminate on an absent next_page; callers pass a wide limit to avoid paging.
  var out = [];
  var page = null;
  do {
    var p = Object.assign({}, params);
    if (page) p.page = page;
    var data = fetchJson_(OPENAI_BASE + '/organization/costs' + buildQuery_(p), openaiHeaders_(cfg));
    (data.data || []).forEach(function (bucket) {
      (bucket.results || []).forEach(function (row) { out.push(row); });
    });
    page = data.next_page || null;
  } while (page);
  return out;
}

function openaiOwner_(owner) {
  if (!owner) return '';
  if (owner.type === 'service_account') {
    var account = owner.service_account || {};
    return 'service account: ' + (account.name || account.id || '?');
  }
  var user = owner.user || {};
  return user.email || user.name || '';
}

function openaiKeyInfo_(cfg) {
  var info = {};
  openaiList_(cfg, '/organization/projects').forEach(function (project) {
    var scope = project.name || project.id;
    openaiList_(cfg, '/organization/projects/' + project.id + '/api_keys').forEach(function (key) {
      info[key.id] = {
        name: key.name || '',
        redacted: key.redacted_value || '',
        owner: openaiOwner_(key.owner),
        scope: scope,
      };
    });
  });
  return info;
}

function openaiCharges_(cfg, start, end) {
  var params = {
    start_time: toUnix_(start),
    end_time: toUnix_(end),
    bucket_width: '1d', // costs only supports 1d
    group_by: 'api_key_id', // exact per-key cost
    limit: 180, // max; <=180 daily buckets fit in one page
  };
  return openaiCostRows_(cfg, params).map(function (row) {
    // amount.value can arrive as a string; coerce so sums add instead of concatenating.
    return { apiKeyId: row.api_key_id || '', cost: toNumber_((row.amount || {}).value) };
  });
}

// --------------------------------------------------------------------------- //
// Anthropic
// --------------------------------------------------------------------------- //
function anthropicHeaders_(cfg) {
  return { 'x-api-key': cfg.anthropicKey, 'anthropic-version': ANTHROPIC_VERSION };
}

function anthropicList_(cfg, path) {
  var out = [];
  var afterId = null;
  do {
    var p = { limit: 100 };
    if (afterId) p.after_id = afterId;
    var data = fetchJson_(ANTHROPIC_BASE + path + buildQuery_(p), anthropicHeaders_(cfg));
    (data.data || []).forEach(function (row) { out.push(row); });
    afterId = data.has_more ? data.last_id : null;
  } while (afterId);
  return out;
}

function anthropicReport_(cfg, path, params) {
  var out = [];
  var page = null;
  do {
    var p = Object.assign({}, params);
    if (page) p.page = page;
    var data = fetchJson_(ANTHROPIC_BASE + path + buildQuery_(p), anthropicHeaders_(cfg));
    (data.data || []).forEach(function (bucket) {
      (bucket.results || []).forEach(function (row) { out.push(row); });
    });
    page = data.next_page || null;
  } while (page);
  return out;
}

function anthropicKeyInfo_(cfg) {
  var users = {};
  anthropicList_(cfg, '/organizations/users').forEach(function (u) {
    users[u.id] = u.email || u.name || u.id;
  });
  var workspaces = {};
  anthropicList_(cfg, '/organizations/workspaces').forEach(function (w) {
    workspaces[w.id] = w.name || w.id;
  });
  var info = {};
  anthropicList_(cfg, '/organizations/api_keys').forEach(function (key) {
    var createdBy = (key.created_by || {}).id || '';
    info[key.id] = {
      name: key.name || '',
      redacted: key.partial_key_hint || '',
      owner: users[createdBy] || createdBy,
      scope: workspaces[key.workspace_id] || 'default',
    };
  });
  return info;
}

function anthropicCharges_(cfg, start, end) {
  var reportParams = { starting_at: toRfc_(start), ending_at: toRfc_(end), bucket_width: '1d', limit: 31 };

  var costByWs = {};
  var costParams = Object.assign({ 'group_by[]': 'workspace_id' }, reportParams);
  anthropicReport_(cfg, '/organizations/cost_report', costParams).forEach(function (row) {
    var ws = row.workspace_id || 'default';
    // VERIFY: cost_report `amount` is minor units (cents) -> /100.
    costByWs[ws] = (costByWs[ws] || 0) + toNumber_(row.amount) / 100;
  });

  var usage = {}; // "ws|key" -> {ws, apiKeyId, tokens}
  var usageParams = Object.assign({ 'group_by[]': ['api_key_id', 'workspace_id'] }, reportParams);
  anthropicReport_(cfg, '/organizations/usage_report/messages', usageParams).forEach(function (row) {
    var ws = row.workspace_id || 'default';
    var apiKeyId = row.api_key_id || '';
    var cache = row.cache_creation || {};
    var tokens =
      (row.uncached_input_tokens || 0) +
      (row.cache_read_input_tokens || 0) +
      (cache.ephemeral_1h_input_tokens || 0) +
      (cache.ephemeral_5m_input_tokens || 0) +
      (row.output_tokens || 0);
    var k = ws + '|' + apiKeyId;
    if (!usage[k]) usage[k] = { ws: ws, apiKeyId: apiKeyId, tokens: 0 };
    usage[k].tokens += tokens;
  });

  var tokensPerWs = {};
  Object.keys(usage).forEach(function (k) {
    tokensPerWs[usage[k].ws] = (tokensPerWs[usage[k].ws] || 0) + usage[k].tokens;
  });

  var charges = [];
  var chargedPerWs = {};
  Object.keys(usage).forEach(function (k) {
    var u = usage[k];
    var wsCost = costByWs[u.ws] || 0;
    var wsTokens = tokensPerWs[u.ws] || 0;
    var cost = wsTokens ? (wsCost * u.tokens) / wsTokens : 0;
    charges.push({ apiKeyId: u.apiKeyId, cost: cost });
    chargedPerWs[u.ws] = (chargedPerWs[u.ws] || 0) + cost;
  });

  // Workspace cost with no key-level usage becomes a console charge so totals reconcile.
  Object.keys(costByWs).forEach(function (ws) {
    var drift = costByWs[ws] - (chargedPerWs[ws] || 0);
    if (Math.abs(drift) > 1e-9) charges.push({ apiKeyId: '', cost: drift });
  });
  return charges;
}

// --------------------------------------------------------------------------- //
// OCS
// --------------------------------------------------------------------------- //
function ocsHeaders_(cfg) {
  return { Authorization: 'Bearer ' + cfg.ocsToken };
}

function ocsProviderKeys_(cfg) {
  return fetchJson_(cfg.ocsBase + '/admin/api/provider-keys/', ocsHeaders_(cfg));
}

function teamByLast4_(providers, providerPrefix) {
  var map = {};
  (providers || []).forEach(function (row) {
    if (!(row.provider_type || '').startsWith(providerPrefix)) return;
    var fp = last4_(row.masked_key || '');
    if (fp) map[fp] = row.team_name;
  });
  return map;
}

// team_name -> {slug, metadata} from the key registry, which knows every team
// that owns a provider key -- including those with no usage in the window (and
// so absent from the usage report). Lets the report label a zero-token team.
function teamMetaByName_(providers) {
  var map = {};
  (providers || []).forEach(function (row) {
    if (!row.team_name || map[row.team_name]) return;
    map[row.team_name] = { slug: row.team_slug || '', metadata: row.metadata || {} };
  });
  return map;
}

function ocsUsage_(cfg, start, end) {
  var query = buildQuery_({ range_type: 'custom', start: ymd_(start), end: ymd_(end) });
  return fetchJson_(cfg.ocsBase + '/admin/api/provider-usage/' + query, ocsHeaders_(cfg));
}

// --------------------------------------------------------------------------- //
// attribution
// --------------------------------------------------------------------------- //
function attribute_(name, charges, keyInfo, teamByLast4) {
  var result = { name: name, total: 0, perTeam: {}, unattributed: [] };
  var buckets = {};
  charges.forEach(function (charge) {
    result.total += charge.cost;
    var info = keyInfo[charge.apiKeyId];
    var team = null;
    if (charge.apiKeyId && info) {
      var fp = last4_(info.redacted);
      team = fp ? teamByLast4[fp] : null;
    }
    if (team) {
      result.perTeam[team] = (result.perTeam[team] || 0) + charge.cost;
    } else {
      addUnattributed_(buckets, charge, info);
    }
  });
  result.unattributed = Object.keys(buckets)
    .map(function (k) { return buckets[k]; })
    .sort(function (a, b) { return b.cost - a.cost; });
  return result;
}

function addUnattributed_(buckets, charge, info) {
  var id, spend;
  if (!charge.apiKeyId) {
    id = '__console__';
    spend = { keyName: CONSOLE_LABEL, owner: '', scope: '', redacted: '', cost: 0 };
  } else {
    id = charge.apiKeyId;
    var keyName = info && info.name ? info.name : '(unnamed key ' + charge.apiKeyId + ')';
    spend = {
      keyName: keyName,
      owner: info ? info.owner : '',
      scope: info ? info.scope : '',
      redacted: info ? info.redacted : '',
      cost: 0,
    };
  }
  if (!buckets[id]) buckets[id] = spend;
  buckets[id].cost += charge.cost;
}

// --------------------------------------------------------------------------- //
// reporting
// --------------------------------------------------------------------------- //
function writeReport_(range, openai, anthropic, ocsUsage, teamMetaByName) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(SHEET_NAME) || ss.insertSheet(SHEET_NAME);
  sheet.clear();

  var ocsByName = {};
  (ocsUsage.teams || []).forEach(function (t) { ocsByName[t.team_name] = t; });
  var metaFallback = teamMetaByName || {};

  // Team metadata field definitions ({key, label}); each becomes its own column.
  var metadataFields = ocsUsage.metadata_fields || [];

  // Team rows, sorted by total cost desc.
  var teamRows = Object.keys(Object.assign({}, openai.perTeam, anthropic.perTeam)).map(function (team) {
    var oa = openai.perTeam[team] || 0;
    var an = anthropic.perTeam[team] || 0;
    var ocs = ocsByName[team] || {};
    // Teams with no usage in the window are absent from the usage report; fall
    // back to the key registry's metadata so slug/metadata columns aren't blank.
    var fallback = metaFallback[team] || {};
    var metadata = ocs.metadata || fallback.metadata || {};
    return {
      team: team,
      slug: ocs.team_slug || fallback.slug || '',
      metadata: metadataFields.map(function (f) { return metadata[f.key] || ''; }),
      oa: oa,
      an: an,
      total: oa + an,
      tokens: ocs.total_tokens || 0,
    };
  });
  teamRows.sort(function (a, b) { return b.total - a.total; });

  var attributed = teamRows.reduce(function (sum, r) { return sum + r.total; }, 0);
  var openaiUnattributed = sumCost_(openai.unattributed);
  var anthropicUnattributed = sumCost_(anthropic.unattributed);
  var grandTotal = openai.total + anthropic.total;

  // Display an inclusive range (end is exclusive internally), so a month reads
  // as e.g. 2026-06-01 to 2026-06-30 rather than ".. 2026-07-01".
  var lastDay = new Date(range.end.getTime() - 86400000);
  var rows = [];
  rows.push(['Spend reconciliation ' + ymd_(range.start) + ' to ' + ymd_(lastDay)]);
  rows.push([]);

  // Totals up top.
  rows.push(['Summary']);
  rows.push(['OpenAI total', round2_(openai.total)]);
  rows.push(['Anthropic total', round2_(anthropic.total)]);
  rows.push(['Grand total', round2_(grandTotal)]);
  rows.push(['Attributed to teams', round2_(attributed)]);
  rows.push(['Unattributed', round2_(openaiUnattributed + anthropicUnattributed)]);
  rows.push([]);

  // Attributed teams, sorted by total cost. Team metadata fields sit between the
  // team identity and the cost columns, one column per configured field.
  var metadataLabels = metadataFields.map(function (f) { return f.label; });
  rows.push(['By team (attributed)']);
  rows.push(['Team', 'Slug'].concat(metadataLabels, ['OpenAI ($)', 'Anthropic ($)', 'Total ($)', 'OCS tokens']));
  teamRows.forEach(function (r) {
    rows.push([r.team, r.slug].concat(r.metadata, [round2_(r.oa), round2_(r.an), round2_(r.total), r.tokens]));
  });
  rows.push([]);

  // Unattributed, one table per provider, each sorted by cost.
  pushUnattributedTable_(rows, 'Unattributed OpenAI (assign to key owner)', openai.unattributed, openaiUnattributed);
  rows.push([]);
  pushUnattributedTable_(
    rows, 'Unattributed Anthropic (assign to key owner)', anthropic.unattributed, anthropicUnattributed
  );
  rows.push([]);

  rows.push(['Notes']);
  rows.push(['- Provider totals are exact (pulled from each provider cost report) and should match the invoice.']);
  rows.push(['- OpenAI per-team dollars are exact: OpenAI reports cost per API key, mapped to the key owner team.']);
  rows.push([
    '- Anthropic per-team dollars are an ESTIMATE: its cost report is workspace-level only, so each ' +
    "workspace's exact cost is split across its keys in proportion to each key's token usage.",
  ]);
  rows.push([
    '- That split assumes a uniform $/token within a workspace, so it can skew when keys there use ' +
    'different models (e.g. Opus vs Haiku); the workspace total is still exact. It is exact when a ' +
    'workspace has one key or all its keys belong to one team.',
  ]);
  rows.push([
    '- Unattributed = spend on API keys OCS does not know, plus console/no-key usage; assign each ' +
    'row to the listed owner.',
  ]);

  // The by-team table is the widest: 6 base columns + one per metadata field.
  var width = 6 + metadataFields.length;
  var padded = rows.map(function (row) {
    var copy = row.slice();
    while (copy.length < width) copy.push('');
    return copy;
  });
  sheet.getRange(1, 1, padded.length, width).setValues(padded);
}

function pushUnattributedTable_(rows, title, items, total) {
  rows.push([title]);
  rows.push(['Owner', 'Key name', 'Scope', 'Cost ($)']);
  items
    .slice()
    .sort(function (a, b) { return b.cost - a.cost; })
    .forEach(function (item) {
      rows.push([item.owner, item.keyName, item.scope, round2_(item.cost)]);
    });
  rows.push(['Total', '', '', round2_(total)]);
}

function sumCost_(items) {
  return items.reduce(function (sum, item) { return sum + item.cost; }, 0);
}

// --------------------------------------------------------------------------- //
// helpers
// --------------------------------------------------------------------------- //
function reqProp_(props, name) {
  var value = props.getProperty(name);
  if (!value) throw new Error('Missing Script Property: ' + name);
  return value;
}

function reportMonthCell_() {
  // The "ReportMonth" named range lets a user pick the month from a sheet cell
  // instead of a Script Property. Accepts a YYYY-MM string or a real date value.
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var range = ss.getRangeByName('ReportMonth');
  if (!range) return null;
  var value = range.getValue();
  if (value instanceof Date) {
    // Format in the sheet's OWN timezone: Sheets stores a date-typed cell at
    // local midnight, so formatting in UTC would shift a "Jun 2026" cell back to
    // May whenever the sheet timezone is ahead of UTC.
    return Utilities.formatDate(value, ss.getSpreadsheetTimeZone(), 'yyyy-MM');
  }
  var text = String(value == null ? '' : value).trim();
  return /^\d{4}-\d{1,2}/.test(text) ? text : null;
}

function dateRange_(props) {
  // MONTH=YYYY-MM: a whole calendar month, [first-of-month, first-of-next-month).
  // The exclusive end is what the provider cost APIs expect, so this captures the
  // full month (including its last day) and lines up with a monthly invoice.
  // The ReportMonth sheet cell wins over the MONTH property.
  var month = reportMonthCell_() || props.getProperty('MONTH');
  if (month) {
    var parts = month.split('-');
    var year = Number(parts[0]);
    var monthIndex = Number(parts[1]) - 1; // 0-based; Date.UTC handles year rollover
    return { start: new Date(Date.UTC(year, monthIndex, 1)), end: new Date(Date.UTC(year, monthIndex + 1, 1)) };
  }

  // Custom range: START/END as YYYY-MM-DD (END exclusive).
  var startStr = props.getProperty('START');
  var endStr = props.getProperty('END');
  if (startStr || endStr) {
    var now = new Date();
    var todayUtc = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
    var end = endStr ? ymdToDate_(endStr) : todayUtc;
    var start = startStr ? ymdToDate_(startStr) : new Date(end.getTime() - 30 * 24 * 3600 * 1000);
    return { start: start, end: end };
  }

  // Default: the last complete calendar month (e.g. run any time in July -> June).
  var today = new Date();
  return {
    start: new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() - 1, 1)),
    end: new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 1)),
  };
}

function ymdToDate_(s) {
  var parts = s.split('-');
  return new Date(Date.UTC(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2])));
}

function ymd_(dt) {
  return dt.toISOString().slice(0, 10);
}

function toUnix_(dt) {
  return Math.floor(dt.getTime() / 1000);
}

function toRfc_(dt) {
  return dt.toISOString().replace(/\.\d{3}Z$/, 'Z');
}

function last4_(masked) {
  var alnum = (masked || '').replace(/[^a-zA-Z0-9]/g, '');
  return alnum.length >= 4 ? alnum.slice(-4) : null;
}

function round2_(n) {
  return Math.round(n * 100) / 100;
}

function toNumber_(value) {
  // Robust coercion: handles numbers, numeric strings, and strings carrying a
  // currency symbol / thousands separators. Non-numeric -> 0.
  if (typeof value === 'number') return isFinite(value) ? value : 0;
  var cleaned = String(value == null ? '' : value).replace(/[^0-9eE+.\-]/g, '');
  var parsed = parseFloat(cleaned);
  return isFinite(parsed) ? parsed : 0;
}
