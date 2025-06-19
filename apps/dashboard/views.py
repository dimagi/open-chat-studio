import json
import csv
from datetime import datetime
from typing import Dict, Any

from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponse
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin

from .forms import DashboardFilterForm, ExportForm, SavedFilterForm
from .services import DashboardService
from .models import DashboardFilter


@method_decorator(login_and_team_required, name='dispatch')
class DashboardView(LoginAndTeamRequiredMixin, TemplateView):
    """Main dashboard view"""
    template_name = 'dashboard/index.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Initialize filter form
        filter_form = DashboardFilterForm(
            data=self.request.GET if self.request.GET else None,
            team=self.request.team
        )
        
        # Get saved filters for this user/team
        saved_filters = DashboardFilter.objects.filter(
            team=self.request.team,
            user=self.request.user
        ).order_by('-is_default', 'filter_name')
        
        context.update({
            'filter_form': filter_form,
            'saved_filters': saved_filters,
            'export_form': ExportForm(),
            'saved_filter_form': SavedFilterForm(),
        })
        
        return context


@method_decorator(login_and_team_required, name='dispatch')
class DashboardApiView(LoginAndTeamRequiredMixin, TemplateView):
    """Base API view for dashboard data"""
    
    def get_dashboard_service(self):
        return DashboardService(self.request.team)
    
    def get_filter_params(self):
        """Extract filter parameters from request"""
        filter_form = DashboardFilterForm(
            data=self.request.GET,
            team=self.request.team
        )
        
        if filter_form.is_valid():
            return filter_form.get_filter_params()
        return {}
    
    def json_response(self, data):
        """Return JSON response with proper serialization"""
        return JsonResponse(data, encoder=DjangoJSONEncoder, safe=False)


class OverviewStatsApiView(DashboardApiView):
    """API endpoint for overview statistics"""
    
    def get(self, request, *args, **kwargs):
        service = self.get_dashboard_service()
        filter_params = self.get_filter_params()
        
        stats = service.get_overview_stats(**filter_params)
        return self.json_response(stats)


class ActiveParticipantsApiView(DashboardApiView):
    """API endpoint for active participants chart data"""
    
    def get(self, request, *args, **kwargs):
        service = self.get_dashboard_service()
        filter_params = self.get_filter_params()
        granularity = request.GET.get('granularity', 'daily')
        
        data = service.get_active_participants_data(
            granularity=granularity,
            **filter_params
        )
        return self.json_response(data)


class SessionAnalyticsApiView(DashboardApiView):
    """API endpoint for session analytics data"""
    
    def get(self, request, *args, **kwargs):
        service = self.get_dashboard_service()
        filter_params = self.get_filter_params()
        granularity = request.GET.get('granularity', 'daily')
        
        data = service.get_session_analytics_data(
            granularity=granularity,
            **filter_params
        )
        return self.json_response(data)


class MessageVolumeApiView(DashboardApiView):
    """API endpoint for message volume trends"""
    
    def get(self, request, *args, **kwargs):
        service = self.get_dashboard_service()
        filter_params = self.get_filter_params()
        granularity = request.GET.get('granularity', 'daily')
        
        data = service.get_message_volume_data(
            granularity=granularity,
            **filter_params
        )
        return self.json_response(data)


class BotPerformanceApiView(DashboardApiView):
    """API endpoint for bot performance summary"""
    
    def get(self, request, *args, **kwargs):
        service = self.get_dashboard_service()
        filter_params = self.get_filter_params()
        
        data = service.get_bot_performance_summary(**filter_params)
        return self.json_response(data)


class UserEngagementApiView(DashboardApiView):
    """API endpoint for user engagement analysis"""
    
    def get(self, request, *args, **kwargs):
        service = self.get_dashboard_service()
        filter_params = self.get_filter_params()
        limit = int(request.GET.get('limit', 10))
        
        data = service.get_user_engagement_data(
            limit=limit,
            **filter_params
        )
        return self.json_response(data)


class ChannelBreakdownApiView(DashboardApiView):
    """API endpoint for channel breakdown data"""
    
    def get(self, request, *args, **kwargs):
        service = self.get_dashboard_service()
        filter_params = self.get_filter_params()
        
        data = service.get_channel_breakdown_data(**filter_params)
        return self.json_response(data)


class TagAnalyticsApiView(DashboardApiView):
    """API endpoint for tag analytics"""
    
    def get(self, request, *args, **kwargs):
        service = self.get_dashboard_service()
        filter_params = self.get_filter_params()
        
        data = service.get_tag_analytics_data(**filter_params)
        return self.json_response(data)


@method_decorator(login_and_team_required, name='dispatch')
class SaveFilterView(LoginAndTeamRequiredMixin, TemplateView):
    """Save filter presets"""
    
    def post(self, request, *args, **kwargs):
        form = SavedFilterForm(request.POST)
        
        if form.is_valid():
            # If marking as default, unmark other defaults
            if form.cleaned_data['is_default']:
                DashboardFilter.objects.filter(
                    team=request.team,
                    user=request.user,
                    is_default=True
                ).update(is_default=False)
            
            # Save filter
            filter_obj, created = DashboardFilter.objects.update_or_create(
                team=request.team,
                user=request.user,
                filter_name=form.cleaned_data['name'],
                defaults={
                    'filter_data': json.loads(form.cleaned_data['filter_data']),
                    'is_default': form.cleaned_data['is_default']
                }
            )
            
            return JsonResponse({
                'success': True,
                'message': 'Filter saved successfully',
                'filter_id': filter_obj.id
            })
        
        return JsonResponse({
            'success': False,
            'errors': form.errors
        })


@method_decorator(login_and_team_required, name='dispatch')
class LoadFilterView(LoginAndTeamRequiredMixin, TemplateView):
    """Load saved filter preset"""
    
    def get(self, request, filter_id, *args, **kwargs):
        try:
            filter_obj = DashboardFilter.objects.get(
                id=filter_id,
                team=request.team,
                user=request.user
            )
            
            return JsonResponse({
                'success': True,
                'filter_data': filter_obj.filter_data
            })
        
        except DashboardFilter.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': 'Filter not found'
            })


@method_decorator(login_and_team_required, name='dispatch')
class ExportDataView(LoginAndTeamRequiredMixin, TemplateView):
    """Export dashboard data"""
    
    def post(self, request, *args, **kwargs):
        export_form = ExportForm(request.POST)
        
        if not export_form.is_valid():
            return JsonResponse({
                'success': False,
                'errors': export_form.errors
            })
        
        chart_type = export_form.cleaned_data['chart_type']
        export_format = export_form.cleaned_data['export_format']
        include_filters = export_form.cleaned_data['include_filters']
        
        # Parse filter data if provided
        filter_params = {}
        if include_filters and export_form.cleaned_data.get('filter_data'):
            try:
                filter_data = json.loads(export_form.cleaned_data['filter_data'])
                filter_form = DashboardFilterForm(data=filter_data, team=request.team)
                if filter_form.is_valid():
                    filter_params = filter_form.get_filter_params()
            except (json.JSONDecodeError, ValueError):
                pass
        
        # Get data from service
        service = DashboardService(request.team)
        
        if chart_type == 'overview':
            data = service.get_overview_stats(**filter_params)
        elif chart_type == 'active_participants':
            data = service.get_active_participants_data(**filter_params)
        elif chart_type == 'session_analytics':
            data = service.get_session_analytics_data(**filter_params)
        elif chart_type == 'message_volume':
            data = service.get_message_volume_data(**filter_params)
        elif chart_type == 'bot_performance':
            data = service.get_bot_performance_summary(**filter_params)
        elif chart_type == 'user_engagement':
            data = service.get_user_engagement_data(**filter_params)
        elif chart_type == 'channel_breakdown':
            data = service.get_channel_breakdown_data(**filter_params)
        elif chart_type == 'tag_analytics':
            data = service.get_tag_analytics_data(**filter_params)
        else:
            return JsonResponse({
                'success': False,
                'error': 'Invalid chart type'
            })
        
        # Handle different export formats
        if export_format == 'json':
            response = HttpResponse(
                json.dumps(data, cls=DjangoJSONEncoder, indent=2),
                content_type='application/json'
            )
            response['Content-Disposition'] = f'attachment; filename="{chart_type}_{timezone.now().strftime("%Y%m%d_%H%M%S")}.json"'
            return response
        
        elif export_format == 'csv':
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="{chart_type}_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
            
            writer = csv.writer(response)
            
            # Write CSV data based on chart type
            if chart_type == 'overview':
                writer.writerow(['Metric', 'Value'])
                for key, value in data.items():
                    writer.writerow([key.replace('_', ' ').title(), value])
            
            elif chart_type in ['active_participants', 'session_analytics', 'message_volume']:
                if isinstance(data, dict) and 'sessions' in data:
                    # Session analytics format
                    writer.writerow(['Date', 'Total Sessions', 'Unique Participants'])
                    sessions_data = data.get('sessions', [])
                    participants_data = data.get('participants', [])
                    for i, session_item in enumerate(sessions_data):
                        participant_item = participants_data[i] if i < len(participants_data) else {}
                        writer.writerow([
                            session_item.get('date', ''),
                            session_item.get('total_sessions', 0),
                            participant_item.get('unique_participants', 0)
                        ])
                elif isinstance(data, list):
                    # Simple list format
                    if data:
                        headers = list(data[0].keys())
                        writer.writerow(headers)
                        for item in data:
                            writer.writerow([item.get(header, '') for header in headers])
            
            elif chart_type == 'bot_performance':
                writer.writerow([
                    'Experiment Name', 'Participants', 'Sessions', 'Messages',
                    'Avg Session Duration', 'Completion Rate', 'Avg Messages/Session'
                ])
                for item in data:
                    writer.writerow([
                        item.get('experiment_name', ''),
                        item.get('participants', 0),
                        item.get('sessions', 0),
                        item.get('messages', 0),
                        item.get('avg_session_duration', 0),
                        item.get('completion_rate', 0),
                        item.get('avg_messages_per_session', 0)
                    ])
            
            return response
        
        elif export_format in ['png', 'pdf']:
            # For image/PDF exports, we would need to implement server-side chart rendering
            # This could be done with libraries like matplotlib, plotly, or headless browser automation
            return JsonResponse({
                'success': False,
                'error': f'{export_format.upper()} export not yet implemented'
            })
        
        return JsonResponse({
            'success': False,
            'error': 'Invalid export format'
        })