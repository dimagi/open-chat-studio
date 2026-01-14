# Banners System

The banners system allows system administrators to create and manage notification banners that appear to users throughout the Open Chat Studio platform.

## Overview

Banners are temporary notifications that can be displayed to users on specific pages or globally across the entire platform. They support different visual styles and can be scheduled to appear and disappear automatically.

## Features

- **Multiple Banner Types**: Information, warning, error, and success banners
- **Location Targeting**: Display banners on specific pages or globally as well as on specific sites (domains) or all sites
- **Scheduling**: Set start and end dates for automatic banner display
- **Feature Flag Integration**: Show banners only to teams with specific feature flags
- **User Dismissal**: Users can dismiss banners with optional re-appearance timeout
- **Markdown Support**: Banner messages support markdown formatting
- **Template Variables**: Dynamic content using Django template syntax

## Creating Banners

### Access the Admin Interface

1. Navigate to the Django admin interface (typically `/admin/`)
2. Log in with superuser credentials
3. Find the "Banners" section and click "Banners"
4. Click "Add banner" to create a new banner

### Banner Configuration

#### Basic Information

- **Title** (optional): A brief title for the banner
- **Message**: The main content displayed to users (supports markdown)
- **Banner Type**: Choose the visual style:
  - `info` - Blue information banner
  - `warning` - Yellow warning banner
  - `error` - Red error banner
  - `success` - Green success banner

#### Location Settings

- **Location**: Where the banner should appear:
  - `global` - All pages (default)
  - `pipelines` - Pipelines home page
  - `pipelines_new` - New pipeline creation page
  - `chatbots_home` - Chatbots listing page
  - `chatbots_new` - New chatbot creation page
  - `assistants_home` - Assistants listing page
  - `team_settings` - Team settings page

#### Scheduling

- **Start Date**: When the banner should first appear (defaults to current time)
- **End Date**: When the banner should stop appearing (required)
- **Is Active**: Manual toggle to enable/disable the banner

#### Advanced Options

- **Feature Flag**: Only show the banner to teams that have this feature flag enabled
- **Dismiss Timeout**: Number of days before a dismissed banner reappears (0 = never reappear)

## Banner Display Logic

### Visibility Rules

A banner is visible when ALL of the following conditions are met:

1. The banner is marked as active (`is_active = True`)
2. The current time is between the start and end dates
3. The user hasn't dismissed the banner (or the dismiss timeout has expired)
4. The banner location matches the current page (or is set to "global")
5. If a feature flag is set, the user's team must have that flag enabled

### Display Locations

The banner location is determined by the `BannerLocationMiddleware` which maps URL patterns to banner locations:

- Global banners appear on all pages
- Location-specific banners only appear on their designated pages
- Multiple banners can be active simultaneously

## Message Formatting

### Markdown Support

Banner messages support standard markdown formatting:

```markdown
**Bold text** and *italic text*
[Links](https://example.com)
- Bullet points
- More bullets
```

### Template Variables

You can use Django template variables in banner messages:

```django
Welcome back, {{ request.user.first_name }}!
Check out this new feature: <a href="{% url "cool-feature" request.team.slug %}">GO!</a>.
```

**Note**: Template errors are only shown to superusers for security reasons.

## User Interaction

### Dismissing Banners

Users can dismiss banners by clicking the dismiss button (×). When dismissed:

- A cookie is set to remember the dismissal
- The banner won't reappear until the dismiss timeout expires
- The cookie expires when the banner ends or after the timeout period

### Dismiss Timeout Behavior

- **0 days**: Banner never reappears once dismissed
- **N days**: Banner reappears N days after dismissal
- Cookie expires at the earlier of: banner end date or dismiss timeout
