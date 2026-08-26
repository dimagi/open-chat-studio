# WAF Management

Open Chat Studio uses AWS WAF (Web Application Firewall) to protect against common web exploits. This guide explains how to manage WAF rules and exceptions for legitimate application endpoints.

## Overview

The WAF management system consists of three components:

1. **`@waf_allow` decorator** - Marks views that need WAF rule exceptions
2. **`export_waf_allow_list` command** - Generates WAF rule configurations
3. **`analyze_waf_logs` command** - Queries CloudWatch and reports WAF matches with their actual or projected outcome

## WAF Rules

Open Chat Studio currently defines two WAF rule exceptions:

### SizeRestrictions_BODY
Bypasses body size limits for endpoints that accept large POST bodies (file uploads, document processing, etc.)

### NoUserAgent_HEADER
Allows requests without User-Agent headers for endpoints accessed by bots, webhooks, or API clients

## Marking Views with `@waf_allow`

Use the `@waf_allow` decorator to mark views that need WAF rule exceptions.

### Usage

```python
from apps.web.waf import waf_allow, WafRule


# Function-based view
@waf_allow(WafRule.SizeRestrictions_BODY)
def upload_file(request):
    # Handle large file uploads
    pass


# Class-based view
@waf_allow(WafRule.NoUserAgent_HEADER)
class WebhookView(View):
    # Handle webhook requests that may not send User-Agent
    pass
```

### Important Notes

- **The `@waf_allow` decorator MUST be the topmost decorator** on the function or class
- For class-based views, apply it to the class itself, not to methods
- Only use when necessary - most views should go through full WAF protection

### Examples

```python
# ✅ Correct - topmost decorator on class
@waf_allow(WafRule.SizeRestrictions_BODY)
class DocumentUploadView(LoginAndTeamRequiredMixin, CreateView):
    model = Document
    # ...

# ✅ Correct - topmost decorator on function
@waf_allow(WafRule.NoUserAgent_HEADER)
@csrf_exempt
def telegram_webhook(request, channel_external_id):
    # ...

# ❌ Incorrect - decorator below other decorators
@login_required
@waf_allow(WafRule.SizeRestrictions_BODY)
def my_view(request):
    # This won't work correctly
    pass
```

## Exporting WAF Rules

After adding `@waf_allow` decorators, generate the updated WAF configuration:

```bash
python manage.py export_waf_allow_list
```

### Output Format

The command generates Python code ready for the `ocs-deploy` repository:

```python
# URI patterns for endpoints that can send large POST bodies
# These bypass only SizeRestrictions_BODY, all other protections remain active
SizeRestrictions_BODY = [
    r"^a/[a-z0-9_-]+/documents/collections/\d+/add_files$",
    r"^slack/events$",
]

# URI patterns for endpoints that may not send User-Agent header
# These bypass only NoUserAgent_HEADER, all other protections remain active
NoUserAgent_HEADER = [
    r"^a/[a-z0-9_-]+/chatbots/[^/]+/start/$",
    r"^channels/telegram/[^/]+$",
]
```

### Deployment

1. Run the export command
2. Copy the output into the `ocs-deploy` repository's WAF module
3. Deploy the updated WAF configuration

## Analyzing WAF Logs

`analyze_waf_logs` queries CloudWatch Logs Insights directly — no manual export step.

```bash
AWS_PROFILE=ocs-prod python manage.py analyze_waf_logs --since 7d
```

It finds the `aws-waf-logs-*` log group, aggregates matched requests, and splits them into two groups:

- **Legitimate endpoints** — the URI resolves to a Django view, so the WAF is producing a false positive.
- **Everything else** — scanner and exploit traffic, summarised by rule so the noise stays quantified rather than silently dropped.

For each false positive it resolves the view and tells you which fix it needs:

| Reported as | Meaning |
|---|---|
| `Add @waf_allow` | The view has no exemption for that rule. Add the decorator. |
| `Decorated in code but not deployed` | The decorator exists but the URI doesn't match any deployed regex — re-export and deploy. |
| `Decorated and deployed, yet still matched` | Usually a log entry predating the last deploy. Check `lastSeen`. |
| `No @waf_allow rule covers this WAF rule` | The rule has no `WafRule` member (e.g. `UserAgent_BadBots_HEADER`). Needs a WAF rule change in `ocs-deploy`. |

The deployed-state column is a live check: it fetches the regex pattern sets from the wafv2 API and
matches the URI against them, so it catches drift between what's decorated and what's actually running.

You still need to review the results — a matched endpoint isn't automatically one that should be exempted.

### Useful options

```bash
--since 24h              # window: 90m, 24h, 7d (default 7d)
--profile / --region     # AWS credentials (defaults to the ambient profile)
--log-group NAME         # skip auto-discovery
--no-drift               # skip the wafv2 lookup (fewer permissions needed)
--waf-env chatbots-prod  # pick an environment when the account holds more than one
--csv findings.csv       # write the endpoint findings out
--dump-json raw.json     # save raw results, then re-run offline with --from-json
--check-path /some/path  # diagnose a single path: view, decorator, deployed state
```

### Reading the outcome column

The managed rule group runs with a **Count override** (`waf.py`, `AWSManagedCommonRuleSet`), so its
rules record `BLOCK` in the logs on requests that were actually let through. The report shows what
happened rather than what the rule claims:

| Outcome | Meaning |
|---|---|
| `would-block` | The rule matched and says BLOCK, but the Count override let the request through. Enforcing that rule would start rejecting this traffic. |
| `BLOCKED` | The request was genuinely rejected. |
| `counted` | A Count-mode rule (e.g. `RateLimitRule`) matched; the request was served. |

While the managed group runs with the Count override it blocks nothing, so its matches all report as
`would-block`. That makes the report a safe way to see what enforcing the rules *would* cost before
flipping the override.
