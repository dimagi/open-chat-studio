import pytest
import json
from datetime import date, timedelta
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model

from apps.teams.models import Team
from apps.experiments.models import Experiment, ExperimentSession, Participant
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.channels.models import ExperimentChannel

from ..models import DashboardFilter

User = get_user_model()


@pytest.mark.django_db
class TestDashboardViews:
    """Test dashboard view functionality"""
    
    def test_dashboard_main_view_requires_login(self, client):
        """Test that dashboard view requires authentication"""
        url = reverse('dashboard:index')
        response = client.get(url)
        
        # Should redirect to login
        assert response.status_code == 302
    
    def test_dashboard_main_view_with_auth(self, authenticated_client, team):
        """Test dashboard main view with authenticated user"""
        url = reverse('dashboard:index')
        response = authenticated_client.get(url)
        
        assert response.status_code == 200
        assert 'filter_form' in response.context
        assert 'export_form' in response.context
        assert 'saved_filter_form' in response.context
    
    def test_dashboard_api_requires_team_context(self, authenticated_client):
        """Test that API endpoints require team context"""
        # Test without team context
        url = reverse('dashboard:api_overview')
        response = authenticated_client.get(url)
        
        # Should return empty data or error when no team context
        assert response.status_code in [200, 403]


@pytest.mark.django_db
class TestDashboardApiViews:
    """Test dashboard API endpoints"""
    
    def test_overview_stats_api(self, authenticated_client, team, experiment, participant, experiment_session, chat):
        """Test overview statistics API endpoint"""
        # Create some test data
        ChatMessage.objects.create(
            chat=chat,
            message_type=ChatMessageType.HUMAN,
            content='Test message'
        )
        
        url = reverse('dashboard:api_overview')
        response = authenticated_client.get(url)
        
        assert response.status_code == 200
        data = response.json()
        
        # Check that expected fields are present
        expected_fields = [
            'total_experiments', 'total_participants', 'total_sessions',
            'total_messages', 'active_experiments', 'active_participants'
        ]
        for field in expected_fields:
            assert field in data
            assert isinstance(data[field], (int, float))
    
    def test_active_participants_api(self, authenticated_client, team, experiment, participant, experiment_session, chat):
        """Test active participants API endpoint"""
        # Create test message
        ChatMessage.objects.create(
            chat=chat,
            message_type=ChatMessageType.HUMAN,
            content='Test message'
        )
        
        url = reverse('dashboard:api_active_participants')
        response = authenticated_client.get(url)
        
        assert response.status_code == 200
        data = response.json()
        
        assert isinstance(data, list)
        if data:  # If there's data
            item = data[0]
            assert 'date' in item
            assert 'active_participants' in item
    
    def test_api_with_filters(self, authenticated_client, team, experiment):
        """Test API endpoints with filter parameters"""
        url = reverse('dashboard:api_overview')
        
        # Test with date range filter
        params = {
            'date_range': '7',
            'granularity': 'daily'
        }
        response = authenticated_client.get(url, params)
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
    
    def test_session_analytics_api(self, authenticated_client, team, experiment, participant, experiment_session):
        """Test session analytics API endpoint"""
        url = reverse('dashboard:api_session_analytics')
        response = authenticated_client.get(url)
        
        assert response.status_code == 200
        data = response.json()
        
        assert isinstance(data, dict)
        assert 'sessions' in data
        assert 'participants' in data
    
    def test_message_volume_api(self, authenticated_client, team):
        """Test message volume API endpoint"""
        url = reverse('dashboard:api_message_volume')
        response = authenticated_client.get(url)
        
        assert response.status_code == 200
        data = response.json()
        
        assert isinstance(data, dict)
        expected_keys = ['human_messages', 'ai_messages', 'totals']
        for key in expected_keys:
            assert key in data
            assert isinstance(data[key], list)
    
    def test_bot_performance_api(self, authenticated_client, team, experiment):
        """Test bot performance API endpoint"""
        url = reverse('dashboard:api_bot_performance')
        response = authenticated_client.get(url)
        
        assert response.status_code == 200
        data = response.json()
        
        assert isinstance(data, list)
        if data:  # If there's data
            item = data[0]
            expected_fields = [
                'experiment_id', 'experiment_name', 'participants',
                'sessions', 'messages', 'completion_rate'
            ]
            for field in expected_fields:
                assert field in item
    
    def test_channel_breakdown_api(self, authenticated_client, team):
        """Test channel breakdown API endpoint"""
        url = reverse('dashboard:api_channel_breakdown')
        response = authenticated_client.get(url)
        
        assert response.status_code == 200
        data = response.json()
        
        assert isinstance(data, dict)
        assert 'channels' in data
        assert 'totals' in data
        assert isinstance(data['channels'], list)
        assert isinstance(data['totals'], dict)


@pytest.mark.django_db
class TestFilterManagement:
    """Test filter management functionality"""
    
    def test_save_filter(self, authenticated_client, team, user):
        """Test saving filter presets"""
        url = reverse('dashboard:save_filter')
        
        filter_data = {
            'date_range': '30',
            'granularity': 'daily',
            'experiments': [1, 2]
        }
        
        data = {
            'name': 'Test Filter',
            'is_default': True,
            'filter_data': json.dumps(filter_data)
        }
        
        response = authenticated_client.post(url, data)
        
        assert response.status_code == 200
        response_data = response.json()
        assert response_data['success'] is True
        
        # Check that filter was saved
        saved_filter = DashboardFilter.objects.get(
            team=team,
            user=user,
            filter_name='Test Filter'
        )
        assert saved_filter.filter_data == filter_data
        assert saved_filter.is_default is True
    
    def test_load_filter(self, authenticated_client, team, user):
        """Test loading saved filter presets"""
        # Create a saved filter
        filter_data = {
            'date_range': '7',
            'experiments': [1]
        }
        
        saved_filter = DashboardFilter.objects.create(
            team=team,
            user=user,
            filter_name='Test Load Filter',
            filter_data=filter_data,
            is_default=False
        )
        
        url = reverse('dashboard:load_filter', kwargs={'filter_id': saved_filter.id})
        response = authenticated_client.get(url)
        
        assert response.status_code == 200
        response_data = response.json()
        assert response_data['success'] is True
        assert response_data['filter_data'] == filter_data
    
    def test_load_nonexistent_filter(self, authenticated_client, team):
        """Test loading non-existent filter"""
        url = reverse('dashboard:load_filter', kwargs={'filter_id': 99999})
        response = authenticated_client.get(url)
        
        assert response.status_code == 200
        response_data = response.json()
        assert response_data['success'] is False
        assert 'error' in response_data


@pytest.mark.django_db
class TestExportFunctionality:
    """Test dashboard export functionality"""
    
    def test_export_json(self, authenticated_client, team):
        """Test JSON export functionality"""
        url = reverse('dashboard:export')
        
        data = {
            'chart_type': 'overview',
            'export_format': 'json',
            'include_filters': False
        }
        
        response = authenticated_client.post(url, data)
        
        assert response.status_code == 200
        assert response['Content-Type'] == 'application/json'
        assert 'attachment' in response['Content-Disposition']
    
    def test_export_csv(self, authenticated_client, team):
        """Test CSV export functionality"""
        url = reverse('dashboard:export')
        
        data = {
            'chart_type': 'overview',
            'export_format': 'csv',
            'include_filters': False
        }
        
        response = authenticated_client.post(url, data)
        
        assert response.status_code == 200
        assert response['Content-Type'] == 'text/csv'
        assert 'attachment' in response['Content-Disposition']
    
    def test_export_with_filters(self, authenticated_client, team):
        """Test export with filter data included"""
        url = reverse('dashboard:export')
        
        filter_data = {
            'date_range': '30',
            'granularity': 'daily'
        }
        
        data = {
            'chart_type': 'bot_performance',
            'export_format': 'csv',
            'include_filters': True,
            'filter_data': json.dumps(filter_data)
        }
        
        response = authenticated_client.post(url, data)
        
        assert response.status_code == 200
        assert response['Content-Type'] == 'text/csv'
    
    def test_export_invalid_format(self, authenticated_client, team):
        """Test export with invalid format"""
        url = reverse('dashboard:export')
        
        data = {
            'chart_type': 'overview',
            'export_format': 'invalid_format',
            'include_filters': False
        }
        
        response = authenticated_client.post(url, data)
        
        assert response.status_code == 200
        response_data = response.json()
        assert response_data['success'] is False
    
    def test_export_png_not_implemented(self, authenticated_client, team):
        """Test that PNG export returns not implemented message"""
        url = reverse('dashboard:export')
        
        data = {
            'chart_type': 'overview',
            'export_format': 'png',
            'include_filters': False
        }
        
        response = authenticated_client.post(url, data)
        
        assert response.status_code == 200
        response_data = response.json()
        assert response_data['success'] is False
        assert 'not yet implemented' in response_data['error']


@pytest.mark.django_db
class TestDashboardSecurity:
    """Test dashboard security and access controls"""
    
    def test_team_isolation(self, client, django_user_model):
        """Test that users can only access their team's data"""
        # Create two teams with users
        team1 = Team.objects.create(name='Team 1', slug='team1')
        team2 = Team.objects.create(name='Team 2', slug='team2')
        
        user1 = django_user_model.objects.create_user(
            email='user1@test.com',
            password='testpass123'
        )
        user2 = django_user_model.objects.create_user(
            email='user2@test.com',
            password='testpass123'
        )
        
        # Add users to their respective teams
        team1.members.add(user1)
        team2.members.add(user2)
        
        # Create filter for team1/user1
        filter_data = {'test': 'data'}
        saved_filter = DashboardFilter.objects.create(
            team=team1,
            user=user1,
            filter_name='Team 1 Filter',
            filter_data=filter_data
        )
        
        # Login as user2 (team2)
        client.force_login(user2)
        
        # Try to access team1's filter
        url = reverse('dashboard:load_filter', kwargs={'filter_id': saved_filter.id})
        response = client.get(url)
        
        # Should not be able to access other team's filter
        assert response.status_code == 200
        response_data = response.json()
        assert response_data['success'] is False
    
    def test_unauthenticated_api_access(self, client):
        """Test that API endpoints require authentication"""
        api_endpoints = [
            'dashboard:api_overview',
            'dashboard:api_active_participants',
            'dashboard:api_session_analytics',
            'dashboard:api_message_volume',
            'dashboard:api_bot_performance',
            'dashboard:api_user_engagement',
            'dashboard:api_channel_breakdown',
            'dashboard:api_tag_analytics'
        ]
        
        for endpoint_name in api_endpoints:
            url = reverse(endpoint_name)
            response = client.get(url)
            
            # Should redirect to login or return 403
            assert response.status_code in [302, 403]