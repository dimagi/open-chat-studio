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
