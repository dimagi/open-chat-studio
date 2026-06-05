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

## Releasing a new widget version

1. Publish the new version to npm (see `components/chat_widget`).
2. In the same PR, bump `LATEST_VERSION` in `apps/channels/widget_versions.py`.
   The embed snippet (`{% widget_script_url %}`) and the "update available"
   badges follow automatically.

## Deprecating old versions

1. Add a `WidgetDeprecation` entry to `DEPRECATIONS` in
   `apps/channels/widget_versions.py` with a sunset date at least 60 days out.
   Versions below `below_version` are deprecated; widgets that predate the
   version header (< 0.5.1) count as deprecated too.
2. Deploy. Deprecated widgets now receive RFC 8594 `Deprecation`/`Sunset`
   headers on chat API responses, and affected channels show a warning badge.
3. Bump the `migration_name` suffix in
   `apps/data_migrations/management/commands/notify_deprecated_widget_versions.py`
   for the new batch, then run it:

        python manage.py notify_deprecated_widget_versions --dry-run
        python manage.py notify_deprecated_widget_versions

   Teams with affected channels (deprecated recorded version, or no recorded
   version but sessions in the last 90 days) get an in-app notification.
4. At sunset nothing breaks automatically — the date marks when breaking
   server-side changes may land. Plan any actual removal separately, following
   [feature deprecation](feature_deprecation.md).
