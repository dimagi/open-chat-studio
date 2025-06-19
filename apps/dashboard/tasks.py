from celery import shared_task
from django.utils import timezone
from datetime import date, timedelta
import logging

from apps.teams.models import Team
from .models import DashboardMetricsSnapshot

logger = logging.getLogger(__name__)


@shared_task
def generate_daily_metrics_snapshots(target_date=None):
    """
    Generate daily metrics snapshots for all teams.
    This task should be run daily to capture metrics.
    """
    if target_date is None:
        target_date = timezone.now().date()
    elif isinstance(target_date, str):
        target_date = timezone.datetime.strptime(target_date, '%Y-%m-%d').date()
    
    teams = Team.objects.all()
    snapshots_created = 0
    snapshots_updated = 0
    errors = 0
    
    for team in teams:
        try:
            snapshot = DashboardMetricsSnapshot.generate_snapshot(team, target_date)
            if snapshot:
                if hasattr(snapshot, '_state') and snapshot._state.adding:
                    snapshots_created += 1
                else:
                    snapshots_updated += 1
            
            logger.info(f"Generated metrics snapshot for team {team.slug} on {target_date}")
            
        except Exception as e:
            logger.error(f"Error generating metrics snapshot for team {team.slug}: {str(e)}")
            errors += 1
    
    logger.info(
        f"Daily metrics snapshot generation completed for {target_date}. "
        f"Created: {snapshots_created}, Updated: {snapshots_updated}, Errors: {errors}"
    )
    
    return {
        'date': target_date.isoformat(),
        'teams_processed': teams.count(),
        'snapshots_created': snapshots_created,
        'snapshots_updated': snapshots_updated,
        'errors': errors
    }


@shared_task
def generate_historical_snapshots(start_date, end_date, team_id=None):
    """
    Generate historical metrics snapshots for a date range.
    Useful for backfilling data or regenerating snapshots.
    """
    if isinstance(start_date, str):
        start_date = timezone.datetime.strptime(start_date, '%Y-%m-%d').date()
    if isinstance(end_date, str):
        end_date = timezone.datetime.strptime(end_date, '%Y-%m-%d').date()
    
    # Get teams to process
    if team_id:
        teams = Team.objects.filter(id=team_id)
    else:
        teams = Team.objects.all()
    
    total_processed = 0
    total_errors = 0
    
    current_date = start_date
    while current_date <= end_date:
        for team in teams:
            try:
                DashboardMetricsSnapshot.generate_snapshot(team, current_date)
                total_processed += 1
                logger.info(f"Generated historical snapshot for team {team.slug} on {current_date}")
                
            except Exception as e:
                logger.error(f"Error generating historical snapshot for team {team.slug} on {current_date}: {str(e)}")
                total_errors += 1
        
        current_date += timedelta(days=1)
    
    logger.info(
        f"Historical snapshot generation completed. "
        f"Date range: {start_date} to {end_date}. "
        f"Processed: {total_processed}, Errors: {total_errors}"
    )
    
    return {
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'teams_count': teams.count(),
        'total_processed': total_processed,
        'total_errors': total_errors
    }


@shared_task
def cleanup_expired_cache_entries():
    """
    Clean up expired cache entries to prevent database bloat.
    Should be run periodically (e.g., hourly).
    """
    from .models import DashboardCache
    
    expired_count = DashboardCache.objects.filter(
        expires_at__lt=timezone.now()
    ).delete()[0]
    
    logger.info(f"Cleaned up {expired_count} expired cache entries")
    
    return {
        'expired_entries_deleted': expired_count,
        'timestamp': timezone.now().isoformat()
    }


@shared_task
def cleanup_old_snapshots(days_to_keep=365):
    """
    Clean up old metrics snapshots beyond the retention period.
    Default is to keep 1 year of data.
    """
    cutoff_date = timezone.now().date() - timedelta(days=days_to_keep)
    
    deleted_count = DashboardMetricsSnapshot.objects.filter(
        date__lt=cutoff_date
    ).delete()[0]
    
    logger.info(f"Cleaned up {deleted_count} old metrics snapshots (older than {cutoff_date})")
    
    return {
        'snapshots_deleted': deleted_count,
        'cutoff_date': cutoff_date.isoformat(),
        'days_kept': days_to_keep
    }


@shared_task
def regenerate_team_snapshots(team_id, days_back=30):
    """
    Regenerate snapshots for a specific team for the last N days.
    Useful when team data has been corrected or updated.
    """
    try:
        team = Team.objects.get(id=team_id)
    except Team.DoesNotExist:
        logger.error(f"Team with id {team_id} not found")
        return {'error': 'Team not found'}
    
    end_date = timezone.now().date()
    start_date = end_date - timedelta(days=days_back)
    
    snapshots_processed = 0
    errors = 0
    
    current_date = start_date
    while current_date <= end_date:
        try:
            DashboardMetricsSnapshot.generate_snapshot(team, current_date)
            snapshots_processed += 1
            
        except Exception as e:
            logger.error(f"Error regenerating snapshot for team {team.slug} on {current_date}: {str(e)}")
            errors += 1
        
        current_date += timedelta(days=1)
    
    logger.info(
        f"Regenerated {snapshots_processed} snapshots for team {team.slug}. "
        f"Date range: {start_date} to {end_date}. Errors: {errors}"
    )
    
    return {
        'team_id': team_id,
        'team_slug': team.slug,
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'snapshots_processed': snapshots_processed,
        'errors': errors
    }