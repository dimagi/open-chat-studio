# OCS Notifications App

## Overview

The `ocs_notifications` app provides a **team-scoped notification system** for Open Chat Studio. It alerts team members about important events through in-app messages and email, allowing users to control delivery preferences and severity thresholds.

This system is designed to:

- **Alert team members** about important events (custom action health checks, evaluations, data syncs, etc.)
- **Handle per-user bookkeeping automatically** (read/unread, mute, do-not-disturb)
- **Support multi-channel delivery** with granular user preferences (in-app, email, level thresholds)
- **Reduce notification fatigue** through deduplication, muting, and severity filtering
- **Persist notifications** so users can view, filter, and manage them later

## Core Concepts

### Notification Levels

Notifications use a three-tier severity system:

| Level | Purpose | Example |
|-------|---------|---------|
| **INFO** | General information, non-critical updates | "Data sync completed successfully" |
| **WARNING** | Concerning but non-breaking changes | "Custom action response time is slow" |
| **ERROR** | Critical issues requiring attention | "Custom action is unreachable" |

Users configure separate thresholds for in-app and email delivery. For example, a user might see INFO and WARNING in-app but only receive ERROR emails.

### The Identifier System

Notifications are **threaded** by combining a slug and event data. The app hashes `{"slug": slug, "data": event_data}` into a SHA1 identifier, stored on **EventType** - which identifies an event in the system. When you call `create_notification()` with the same slug and event_data, the system reuses the same EventType and appends a new **NotificationEvent** to that thread.

**How deduplication works:**

- **Same slug + Same event_data** → Reuses the same event thread; creates a new NotificationEvent and re-notifies users who previously read it
- **Same slug + Different event_data** → Creates a separate event thread

**Example:**

```python
# First time: Creates event thread + first event
create_notification(
    slug="payment-api-timeout",
    event_data={"api_id": 1},  # Unique to this API
    # ... other fields
)

# Later: Same API times out again
create_notification(
    slug="payment-api-timeout",
    event_data={"api_id": 1},  # SAME → same thread
    # ... other fields
)
# → Same EventType (thread)
# → New NotificationEvent created
# → Users who read it are marked unread (gets re-notified)

# Different API times out
create_notification(
    slug="payment-api-timeout",
    event_data={"api_id": 2},  # DIFFERENT → separate notification
    # ... other fields
)
# → New EventType (thread) for this API
```

### The Data Model

Notifications are split into three primary models:

- **`EventType`** (per team, per slug+event_data): The event thread key. Stores identifier, event data, and severity level.
- **`NotificationEvent`** (per occurrence): Each call to `create_notification()` creates a new event with title/message/links.
- **`EventUser`** (per user per event thread): Tracks read/unread status and muting for each user.

When you call `create_notification()`, the system creates/updates these automatically, applying permission filters when determining which users receive it.

User preferences live in **`UserNotificationPreferences`** and control:
- In-app enabled + minimum in-app level (used for unread counts/badge)
- Email enabled + minimum email level (used when sending emails)
- Do Not Disturb (blocks all notifications for a duration)


## How to Create Notifications

### Basic Usage

Use the `create_notification()` utility function from `apps.ocs_notifications.utils`:

```python
from apps.ocs_notifications.models import LevelChoices
from apps.ocs_notifications.utils import create_notification

create_notification(
    title="Custom Action is down",
    message="The custom action 'My API' is currently unreachable.",
    level=LevelChoices.ERROR,
    team=team_object,
    slug="custom-action-health-check",
    event_data={"action_id": 123},
)
```
### Where to Add Notification Methods

For better code organization and maintainability, **preferably add your notification helper functions in `apps/ocs_notifications/notifications.py`**.

### Required Parameters

- **`title`** (str): Short notification heading (shown in list)
- **`message`** (str): Detailed notification content
- **`level`** (LevelChoices): Severity level (INFO, WARNING, ERROR)
- **`team`** (Team): Team to notify (determines who receives this notification)
- **`slug`** (str): Identifier for notification type; groups related notifications
  - Use format: `"feature-event-type"` (e.g., `"custom-action-health-check"`)
  - Same slug + same event_data = same event thread

### Optional Parameters

- **`event_data`** (dict): Additional JSON data stored with the notification
  - Combined with `slug` to create the deduplication identifier
  - Use for: IDs, status flags, context needed for re-delivery decisions
  - **Best practice**: Include minimal identifiers needed for deduplication logic
  - Default: empty dict

- **`permissions`** (list[str]): Django permission codenames to filter recipients
  - Format: `"app_label.action"` (e.g., `"custom_actions.change_customaction"`)
  - Only team members with **ALL** specified permissions will receive the notification
  - Permissions are checked per-team (combines Django perms + team membership)
  - Default: None (notify all team members in the team)

- **`links`** (dict): A dictionary of label → URL pairs to attach to the notification
  - These are rendered as clickable chips/buttons in the notification UI
  - Use for: linking to the relevant bot, session, or admin page
  - Example: `{"View Bot": "/experiments/123/", "View Session": "/sessions/456/"}`
  - Default: empty dict

### Delivery Behavior

- **In-app**: Notifications always create `EventUser` rows for eligible users. The unread badge/count respects the
  user’s in-app level preference (and can be disabled entirely).
- **Email**: Email is sent synchronously when a notification is created and the user meets their email preferences.
- **Do Not Disturb / Mute**: If a user has Do Not Disturb enabled or muted that event thread, they won’t be notified of new events.

## When & Where to Call create_notification()

The best place to call `create_notification()` depends on your use case. All contexts are safe:


## Adding Notifications to Your Feature

Follow this checklist when integrating notifications:

1. **Identify the event**: What user-facing event should trigger the notification?
   - Examples: Custom action down, evaluation complete, data sync failed

2. **Choose a slug**: A descriptive identifier for the notification type
   - Format: `"feature-event-type"` (e.g., `"data-sync-failed"`)
   - Use consistent slug for the same type of event

3. **Design event_data**: What minimal context identifies this specific event instance?
   - Include IDs to distinguish between different instances (e.g., `action_id`, `experiment_id`)
   - Keep it small and JSON-serializable
   - **Don't** include sensitive data (passwords, tokens, etc.)

4. **Pick a severity level**: Should this be INFO, WARNING, or ERROR?
   - INFO: Confirmations, completions, general information
   - WARNING: Performance issues, retry scenarios, unusual conditions
   - ERROR: System failures, unreachable services, data loss risks

5. **Determine scope**: Who should receive this notification?
   - All team members? Or only users with specific permissions?
   - Use `permissions` parameter if role-based filtering is needed
   - Test with users having different permissions

## Span-Driven Notifications (Tracing Integration)

For errors that occur inside traced spans (pipeline execution, message processing, etc.), notifications are fired **automatically at trace exit** rather than eagerly at each call site. This is powered by `SpanNotificationConfig` and the `OCSTracer`.

### How It Works

1. When opening a span via `TracingService.span()`, pass a `SpanNotificationConfig` to declare that errors in this span should trigger a notification:

```python
from apps.service_providers.tracing.base import SpanNotificationConfig

with self.trace_service.span(
    "Run Pipeline",
    inputs={"input": data},
    notification_config=SpanNotificationConfig(permissions=["experiments.change_experiment"]),
) as span:
    # If an exception propagates out of this block, a notification
    # will be fired automatically when the trace completes.
    result = do_work()
    span.set_outputs({"result": result})
```

2. When a span with a `notification_config` exits with an error, the `OCSTracer` captures the **innermost** erroring span's name and config (first error wins).
3. At **trace exit**, if an error was detected and the experiment is not a working version, `OCSTracer` fires a single `trace_error_notification` with:
   - A human-readable title derived from the span name (e.g., `"Run Pipeline"` becomes `"Run Pipeline Failed for 'My Bot'"`)
   - The error message from the exception
   - A link to the trace record for debugging
   - Permission-scoped delivery based on the `SpanNotificationConfig`

### When to Use Span-Driven Notifications

- Preferrably always when you use a span and want users to be notified if that specific span encounters an error.

Use direct `create_notification()` calls when:
- The error occurs outside of a trace context (e.g., background tasks, health checks)
- You need custom slug/event_data control beyond what span names provide
