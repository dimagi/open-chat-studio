# OCS Notifications App

## Overview

The `ocs_notifications` app provides a **team-scoped notification system** for Open Chat Studio. It alerts team members about important events through in-app messages and email, allowing users to control delivery preferences and severity thresholds.

This system is designed to:

- **Alert team members** about important events (custom action health checks, evaluations, data syncs, etc.)
- **Notify users asynchronously** without blocking your feature code
- **Support multi-channel delivery** with granular user preferences (in-app, email, level thresholds)
- **Reduce notification fatigue** through deduplication and severity filtering
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

Notifications are **deduplicated** by combining a slug and event data. When you call `create_notification()` with the same slug and event_data, the system updates the existing notification rather than creating a duplicate.

**How deduplication works:**

- **Same slug + Same event_data** → Updates existing notification, re-notifies users who previously read it
- **Same slug + Different event_data** → Creates separate notification (different event thread)

**Example:**

```python
# First time: Creates notification
create_notification(
    slug="payment-api-timeout",
    event_data={"api_id": 1},  # Unique to this API
    # ... other fields
)

# Later: Same API times out again
create_notification(
    slug="payment-api-timeout",
    event_data={"api_id": 1},  # SAME → updates existing
    # ... other fields
)
# → Existing notification is updated
# → Users who read it are marked unread (gets re-notified)

# Different API times out
create_notification(
    slug="payment-api-timeout",
    event_data={"api_id": 2},  # DIFFERENT → separate notification
    # ... other fields
)
# → New notification thread for this API
```

### The Data Model

Notifications are split into two models:

- **`Notification`** (per team, per slug+event_data): Shared across all team members; contains the title, message, and event data
- **`UserNotification`** (per user): Tracks which users have seen the notification, read/unread status for each user

When you call `create_notification()`, the system creates/updates both automatically, applying permission filters when determining which users receive it.


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
  - Same slug + same event_data = updates existing notification

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
  

## When & Where to Call create_notification()

The best place to call `create_notification()` depends on your use case. All contexts are safe:

### In Django Signals (Recommended for model events)

Best for events tied to model lifecycle (create, update, delete):

```python
from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.custom_actions.models import CustomAction
from apps.ocs_notifications.models import LevelChoices
from apps.ocs_notifications.utils import create_notification

@receiver(post_save, sender=CustomAction)
def notify_custom_action_failed(sender, instance, created, **kwargs):
    """Notify on CustomAction status change."""
    if instance.status == CustomAction.STATUS_DOWN:
        create_notification(
            title=f"Custom Action '{instance.name}' is unreachable",
            message=f"The API has not responded in 5 minutes.",
            level=LevelChoices.ERROR,
            team=instance.team,
            slug="custom-action-health-check",
            event_data={"action_id": instance.id},
        )
```

### In Views (Best for user-triggered events)

Use for immediate feedback on user actions:

```python
# In your view
from apps.ocs_notifications.utils import create_notification
from apps.ocs_notifications.models import LevelChoices

def trigger_data_sync(request, experiment_id):
    experiment = Experiment.objects.get(id=experiment_id, team=request.team)
    
    # Perform sync
    sync_result = experiment.sync_data()
    
    # Notify team immediately
    create_notification(
        title="Data sync started",
        message=f"Syncing data for '{experiment.name}'...",
        level=LevelChoices.INFO,
        team=experiment.team,
        slug="data-sync-started",
        event_data={"experiment_id": experiment.id},
    )
    
    return redirect(...)
```

### In Celery Tasks (Best for background operations)

Safe to call from async tasks; use for long-running operations:

```python
from celery import shared_task
from apps.ocs_notifications.models import LevelChoices
from apps.ocs_notifications.utils import create_notification

@shared_task
def evaluate_experiment(experiment_id):
    """Run evaluation in background; notify on completion."""
    experiment = Experiment.objects.get(id=experiment_id)
    
    try:
        # Perform intensive evaluation
        results = experiment.run_evaluation()
        
        create_notification(
            title="Evaluation complete",
            message=f"Results: {results.summary}",
            level=LevelChoices.INFO,
            team=experiment.team,
            slug="evaluation-complete",
            event_data={"experiment_id": experiment.id},
        )
    except Exception as e:
        create_notification(
            title="Evaluation failed",
            message=f"Error: {e}",
            level=LevelChoices.ERROR,
            team=experiment.team,
            slug="evaluation-failed",
            event_data={"experiment_id": experiment.id},
        )
```

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

6. **Choose where to call it**: Signal, view, or Celery task?
   - Signals: For model lifecycle events
   - Views: For user-triggered actions
   - Celery: For background job results

7. **Write tests**: Create unit tests verifying the notification is sent
   - Use `UserNotification.objects.filter(...)` to assert notification was created
   - Test permission filters if used
   - Test with different notification level preferences

8. **Document it**: Add code comments explaining what triggers the notification
   - What slug/event_data is used and why
   - Who receives it and why
   - Example: Link to AGENTS.md or add inline docstring
