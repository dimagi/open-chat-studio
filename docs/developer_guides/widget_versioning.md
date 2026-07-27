# Chat widget versioning & deprecation

The chat widget (`open-chat-studio-widget` on npm, source in `components/chat_widget`)
is embedded on customer sites with a version-pinned script tag. The Django app's
version policy lives in `apps/channels/widget_versions.py`.

## How version tracking works

- The widget sends `x-ocs-widget-version` on chat API requests (since v0.5.1).
- `chat_start_session` persists it to `ExperimentChannel.widget_version` /
  `widget_version_updated_at` (unaudited telemetry columns, throttled writes).
- The channel button and widget params dialog show upgrade/deprecation badges
  based on `get_widget_update_status()`.

## Per-channel authentication policy

Each embedded-widget channel stores a durable `ExperimentChannel.required_auth_level`
(`WidgetAuthLevel`) that fixes the minimum authentication the server demands, rather
than inferring it from per-request heuristics:

| Level | Value | What the server enforces |
|---|---|---|
| `NONE` | 0 | Legacy path: embed key optional, `is_public`/allowlist fallback permitted (pre-0.5.1 widgets). |
| `EMBED_KEY` | 1 | A valid `X-Embed-Key` + allowed-domain check; no session token. The `is_public` fallback is blocked. |
| `SESSION_TOKEN` | 2 | A valid `X-Embed-Key` **and** `X-Session-Token`. No legacy path reachable. |

- New channels default to `SESSION_TOKEN` (the strictest level).
- `chat_start_session` consults the resolved channel's level (see
  `_issue_or_opt_out_session_token`): `SESSION_TOKEN` always issues and enforces a
  token; `EMBED_KEY`/`NONE` opt the session out of token enforcement.
- `SessionAccessPermission` enforces the level on every subsequent request.
- Migration `0029_experimentchannel_required_auth_level` grandfathers existing
  channels from their last-reported `widget_version`: `unknown`/`< 0.5.1` → `NONE`,
  `0.5.1–0.8.x` → `EMBED_KEY`, `>= 0.9.0` and never-connected (`null`) → `SESSION_TOKEN`.
- The level is system-managed, not user-editable: it is set by the model default for new
  channels and by the grandfathering migration for existing ones. It is visible (read-only)
  in the Django admin for inspection/support.

### Ratcheting the level up when a widget upgrades

A grandfathered channel keeps recording the version its widget reports
(`widget_version`), but that never moves `required_auth_level` on its own. The
`ratchet_widget_auth_levels` Celery task (daily; `apps/channels/tasks.py`) closes that
gap. For each embedded-widget channel it maps the recorded version to the level it can
satisfy (`widget_versions.level_for_version`) and, if that is **higher** than the current
`required_auth_level`, runs a two-phase, notify-then-apply upgrade:

1. **First detection** — record `pending_auth_level` + `auth_level_notified_at` and notify
   the team (`widget_auth_level_upgrade_notification`) with the minimum widget version
   every embed must run before the change lands.
2. **After the grace period** (`ExperimentChannel.AUTH_LEVEL_RATCHET_GRACE`, 14 days) —
   apply the level via an audited `save()`, then clear the pending state.

The ratchet is **monotonic**: it only ever raises a level, so a stale or spoofed version
header can only tighten auth, never relax it. If the reported version drops back below the
pending level before the grace period elapses, the pending bump is dropped. The channel
details dialog shows the current minimum required version and any pending upgrade.

## Releasing a new widget version

1. Publish the new version to npm (see `components/chat_widget`).
2. In the same PR, bump `LATEST_VERSION` in `apps/channels/widget_versions.py`.
   The embed snippet (`{% widget_script_url %}`) and the "update available"
   badges follow automatically.
3. Optionally announce the release in-app. Add a data migration in
   `apps/channels/migrations/` that triggers the notification on deploy, with the
   version and release notes inline:

        from apps.data_migrations.utils.migrations import RunDataMigration

        operations = [
            RunDataMigration(
                "notify_widget_version_release",
                command_options={
                    "force": True,
                    "widget_version": "0.10.0",
                    "notes": "Adds dark mode and faster load times.",
                    "changelog_url": "https://docs.openchatstudio.com/chat_widget/",
                },
            ),
        ]

   `widget_version` defaults to `LATEST_VERSION` and `changelog_url` to the chat
   widget docs, so both can be omitted. Every team with an embedded-widget
   channel gets an INFO notification linking to their widget chatbots and the
   changelog. The command slug is fixed; Django tracks each migration's single
   run, so `force=True` is required and nothing needs bumping. Preview with:

        python manage.py notify_widget_version_release --dry-run --widget-version 0.10.0

## Deprecating old versions

1. Add a `WidgetDeprecation` entry to `DEPRECATIONS` in
   `apps/channels/widget_versions.py` with a sunset date at least 60 days out.
   Versions below `below_version` are deprecated; widgets that predate the
   version header (< 0.5.1) count as deprecated too.
2. In the docs repo, update the chat widget changelog (published at
   <https://docs.openchatstudio.com/chat_widget/>): tag the affected version(s)
   as **deprecated** and note the same sunset date used in the
   `WidgetDeprecation` entry. This is the public record customers check to see
   whether their pinned version is still supported.
3. Add a data migration in `apps/channels/migrations/` that triggers the
   notification on deploy (see `0027_notify_widget_deprecation_below_0_6_0.py`):

        from apps.data_migrations.utils.migrations import RunDataMigration

        operations = [
            RunDataMigration("notify_deprecated_widget_versions", command_options={"force": True}),
        ]

   The command slug is fixed; Django tracks each migration's single run, so
   nothing needs bumping. Teams with affected channels (running a deprecated
   version and active in the last 90 days) get an in-app notification on deploy;
   dormant channels are surfaced passively by the UI badge instead.
4. Deploy. Deprecated widgets now receive RFC 8594 `Deprecation`/`Sunset`
   headers on chat API responses, affected channels show a warning badge, and
   the migration sends the notifications.
5. At sunset nothing breaks automatically — the date marks when breaking
   server-side changes may land. Plan any actual removal separately, following
   [feature deprecation](feature_deprecation.md).

Keep the sunset date consistent across all three places — the `WidgetDeprecation`
entry, the changelog tag, and the `Sunset` header (which is derived from the
entry automatically).

To preview who would be notified before deploying, run the command manually:

        python manage.py notify_deprecated_widget_versions --dry-run
