# WAF Management

Open Chat Studio uses AWS WAF (Web Application Firewall) to protect against common web exploits. This guide explains how to manage WAF rules and exceptions for legitimate application endpoints.

## Overview

The WAF management system consists of three components:

1. **`@waf_allow` decorator** - Marks views that need WAF rule exceptions
2. **`export_waf_allow_list` command** - Generates WAF rule configurations
3. **`filter_valid_paths.py` script** - Analyzes WAF/load balancer logs

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
    r"^a/[a-z0-9_-]+/assistants/new/$",
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

Use `scripts/filter_valid_paths.py` to analyze AWS WAF or load balancer logs and identify which blocked requests are legitimate views. You still need to review the matches since many will be valid rule matches ie. requests that we do want to block.
