# Dashboard App for Open Chat Studio

A comprehensive analytics dashboard for visualizing chatbot usage metrics across multiple experiments and channels.

## Features

### 📊 Visualizations
- **Active Participants Chart**: Line/bar chart showing participant activity over time
- **Session Analytics**: Stacked histograms for total sessions and unique participants
- **Message Volume Trends**: Dual-line chart comparing participant vs bot messages
- **Bot Performance Summary**: Table with horizontal bar charts for performance metrics
- **User Engagement Analysis**: Most/least active participants and session length distribution
- **Channel Breakdown**: Pie/donut charts showing usage distribution across channels
- **Tag Analytics**: Frequency analysis of message and session tags

### 🔍 Filtering System
- **Date Range Filter**: Last 7 days, 30 days, 3 months, year, or custom range
- **Chatbot Filter**: Multi-select dropdown for specific experiments
- **Channel Filter**: Filter by communication channels (WhatsApp, Telegram, etc.)
- **Time Granularity**: Hourly, daily, weekly, or monthly aggregation
- **Saved Filter Presets**: Save and load common filter combinations

### 📤 Export Functionality
- **CSV Export**: Download data in CSV format for external analysis
- **JSON Export**: Raw data export in JSON format
- **Filter Inclusion**: Option to include current filter settings in exports
- **PNG/PDF Export**: (Placeholder for future implementation)

### ⚡ Performance Features
- **Intelligent Caching**: 30-minute TTL cache for expensive queries
- **Database Optimization**: Proper indexing and query optimization
- **Daily Snapshots**: Pre-computed daily metrics for historical trend analysis
- **Team Isolation**: Multi-tenant data separation

## Installation

### 1. App Configuration
The dashboard app is already added to `INSTALLED_APPS` in `settings.py`:
```python
PROJECT_APPS = [
    # ... other apps
    "apps.dashboard",
]
```

### 2. Database Migration
Run migrations to create dashboard models:
```bash
python manage.py migrate dashboard
```

### 3. Frontend Assets
Build the dashboard assets:
```bash
npm run build  # or npm run dev for development
```

### 4. URL Configuration
Dashboard URLs are already configured under the team-scoped URLs:
```
/a/{team_slug}/dashboard/
```

## Usage

### Accessing the Dashboard
1. Navigate to `/a/{your-team-slug}/dashboard/`
2. Use the filter controls to customize the view
3. Charts update automatically when filters change
4. Save frequently-used filter combinations as presets

### Background Tasks
Set up Celery tasks for optimal performance:

```python
# Generate daily snapshots (run daily)
from apps.dashboard.tasks import generate_daily_metrics_snapshots
generate_daily_metrics_snapshots.delay()

# Clean up expired cache entries (run hourly)
from apps.dashboard.tasks import cleanup_expired_cache_entries
cleanup_expired_cache_entries.delay()

# Clean up old snapshots (run weekly)
from apps.dashboard.tasks import cleanup_old_snapshots
cleanup_old_snapshots.delay(days_to_keep=365)
```

### Generating Historical Data
To backfill historical snapshots:
```python
from apps.dashboard.tasks import generate_historical_snapshots
from datetime import date, timedelta

start_date = date.today() - timedelta(days=30)
end_date = date.today()
generate_historical_snapshots.delay(start_date.isoformat(), end_date.isoformat())
```

## API Endpoints

### Chart Data APIs
- `GET /a/{team}/dashboard/api/overview/` - Overview statistics
- `GET /a/{team}/dashboard/api/active-participants/` - Active participants chart data
- `GET /a/{team}/dashboard/api/session-analytics/` - Session analytics data
- `GET /a/{team}/dashboard/api/message-volume/` - Message volume trends
- `GET /a/{team}/dashboard/api/bot-performance/` - Bot performance summary
- `GET /a/{team}/dashboard/api/user-engagement/` - User engagement data
- `GET /a/{team}/dashboard/api/channel-breakdown/` - Channel breakdown data
- `GET /a/{team}/dashboard/api/tag-analytics/` - Tag analytics data

### Filter Management APIs
- `POST /a/{team}/dashboard/filters/save/` - Save filter preset
- `GET /a/{team}/dashboard/filters/load/{id}/` - Load saved filter
- `POST /a/{team}/dashboard/export/` - Export dashboard data

### Query Parameters
All chart APIs support these parameters:
- `date_range`: '7', '30', '90', '365', or 'custom'
- `start_date`: YYYY-MM-DD format (for custom range)
- `end_date`: YYYY-MM-DD format (for custom range)
- `granularity`: 'hourly', 'daily', 'weekly', 'monthly'
- `experiments`: List of experiment IDs
- `channels`: List of channel IDs

## Data Models

### DashboardCache
Stores cached analytics data with TTL expiration:
```python
DashboardCache.set_cached_data(team, cache_key, data, ttl_minutes=30)
data = DashboardCache.get_cached_data(team, cache_key)
```

### DashboardFilter
Stores user filter presets:
```python
filter_preset = DashboardFilter.objects.create(
    team=team,
    user=user,
    filter_name="My Preset",
    filter_data={"date_range": "30", "granularity": "daily"},
    is_default=True
)
```

### DashboardMetricsSnapshot
Daily pre-computed metrics for performance:
```python
snapshot = DashboardMetricsSnapshot.generate_snapshot(team, date.today())
```

## Performance Considerations

### Caching Strategy
- API responses cached for 30 minutes
- Cache keys include filter parameters for accuracy
- Automatic cache cleanup via Celery tasks

### Database Optimization
- Proper indexing on team, date, and filter fields
- Daily snapshots reduce real-time computation
- Efficient QuerySet usage with select_related/prefetch_related

### Frontend Performance
- Chart.js with optimized datasets
- Debounced filter updates (500ms)
- Progressive loading with skeleton states
- Responsive design for mobile/tablet

## Testing

Run the dashboard tests:
```bash
# Run all dashboard tests
pytest apps/dashboard/tests/

# Run specific test files
pytest apps/dashboard/tests/test_models.py
pytest apps/dashboard/tests/test_views.py
pytest apps/dashboard/tests/test_services.py

# Run with coverage
pytest apps/dashboard/tests/ --cov=apps.dashboard
```

## Security

### Team Isolation
- All data is properly scoped to teams
- Users can only access their team's dashboard data
- Saved filters are user and team-specific

### Authentication
- All views require authentication via `@login_and_team_required`
- API endpoints verify team membership
- CSRF protection on all POST requests

### Data Privacy
- No sensitive data in cache keys
- Proper data anonymization in exports
- Audit logging for data access

## Troubleshooting

### Common Issues

#### Charts Not Loading
1. Check browser console for JavaScript errors
2. Verify Chart.js CDN is accessible
3. Ensure dashboard bundle is built: `npm run build`

#### Empty Data
1. Verify team has experiments and sessions
2. Check date range filters (may be too restrictive)
3. Run `generate_daily_metrics_snapshots` task

#### Performance Issues
1. Check cache hit rates in logs
2. Verify database indexes are created
3. Consider reducing date range for large datasets

#### Filter Issues
1. Clear browser cache and cookies
2. Reset filters using the "Reset" button
3. Check for JavaScript errors in browser console

### Debugging

Enable debug logging for dashboard:
```python
LOGGING = {
    'loggers': {
        'apps.dashboard': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': True,
        },
    },
}
```

### Database Queries
Monitor expensive queries:
```bash
# Django debug toolbar shows query counts
DEBUG_TOOLBAR = True

# Log slow queries
LOGGING_CONFIG['loggers']['django.db.backends'] = {
    'level': 'DEBUG',
    'handlers': ['console'],
}
```

## Future Enhancements

### Planned Features
- Real-time chart updates via WebSockets
- Server-side PNG/PDF export using headless browser
- Advanced filtering with saved search queries
- Dashboard embedding for external applications
- Custom chart builder interface
- A/B testing analytics integration

### Performance Improvements
- Redis caching for high-traffic teams
- Database query optimization with materialized views
- CDN integration for static assets
- Progressive Web App (PWA) features

## Contributing

### Code Style
- Follow Django and JavaScript best practices
- Use TypeScript for new frontend components
- Add tests for all new functionality
- Update documentation for new features

### Testing Requirements
- Unit tests for all models and services
- Integration tests for API endpoints
- Frontend tests for chart functionality
- Performance tests for large datasets

## Support

For issues and feature requests:
1. Check existing GitHub issues
2. Review troubleshooting section above
3. Create detailed bug reports with:
   - Browser and version
   - Team size and data volume
   - Steps to reproduce
   - Expected vs actual behavior