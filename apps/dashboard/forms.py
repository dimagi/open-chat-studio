from django import forms
from django.utils import timezone
from datetime import timedelta
from django.utils.translation import gettext_lazy as _

from apps.experiments.models import Experiment
from apps.channels.models import ExperimentChannel


class DashboardFilterForm(forms.Form):
    """Form for filtering dashboard data"""
    
    # Date range options
    DATE_RANGE_CHOICES = [
        ('7', _('Last 7 days')),
        ('30', _('Last 30 days')),
        ('90', _('Last 3 months')),
        ('365', _('Last year')),
        ('custom', _('Custom range')),
    ]
    
    # Time granularity options
    GRANULARITY_CHOICES = [
        ('hourly', _('Hourly')),
        ('daily', _('Daily')),
        ('weekly', _('Weekly')),
        ('monthly', _('Monthly')),
    ]
    
    date_range = forms.ChoiceField(
        choices=DATE_RANGE_CHOICES,
        initial='30',
        required=False,
        widget=forms.Select(attrs={
            'class': 'form-select form-select-sm',
            'data-filter-type': 'date_range'
        })
    )
    
    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control form-control-sm',
            'data-filter-type': 'start_date'
        })
    )
    
    end_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={
            'type': 'date',
            'class': 'form-control form-control-sm',
            'data-filter-type': 'end_date'
        })
    )
    
    granularity = forms.ChoiceField(
        choices=GRANULARITY_CHOICES,
        initial='daily',
        required=False,
        widget=forms.Select(attrs={
            'class': 'form-select form-select-sm',
            'data-filter-type': 'granularity'
        })
    )
    
    experiments = forms.ModelMultipleChoiceField(
        queryset=Experiment.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={
            'class': 'form-select form-select-sm',
            'multiple': True,
            'data-filter-type': 'experiments',
            'size': '4'
        })
    )
    
    channels = forms.ModelMultipleChoiceField(
        queryset=ExperimentChannel.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={
            'class': 'form-select form-select-sm',
            'multiple': True,
            'data-filter-type': 'channels',
            'size': '4'
        })
    )
    
    def __init__(self, *args, team=None, **kwargs):
        super().__init__(*args, **kwargs)
        
        if team:
            # Filter experiments and channels by team
            self.fields['experiments'].queryset = Experiment.objects.filter(
                team=team, is_archived=False
            ).order_by('name')
            
            self.fields['channels'].queryset = ExperimentChannel.objects.filter(
                team=team, deleted=False
            ).order_by('name')
        
        # Set default dates if not provided
        if not self.data.get('start_date') and not self.data.get('end_date'):
            today = timezone.now().date()
            if self.data.get('date_range') == '7':
                self.fields['start_date'].initial = today - timedelta(days=7)
                self.fields['end_date'].initial = today
            elif self.data.get('date_range') == '90':
                self.fields['start_date'].initial = today - timedelta(days=90)
                self.fields['end_date'].initial = today
            elif self.data.get('date_range') == '365':
                self.fields['start_date'].initial = today - timedelta(days=365)
                self.fields['end_date'].initial = today
            else:  # Default 30 days
                self.fields['start_date'].initial = today - timedelta(days=30)
                self.fields['end_date'].initial = today
    
    def clean(self):
        cleaned_data = super().clean()
        date_range = cleaned_data.get('date_range')
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        
        # Handle date range logic
        if date_range and date_range != 'custom':
            today = timezone.now().date()
            days = int(date_range)
            cleaned_data['start_date'] = today - timedelta(days=days)
            cleaned_data['end_date'] = today
        elif date_range == 'custom':
            if not start_date or not end_date:
                raise forms.ValidationError(_('Start date and end date are required for custom range.'))
            if start_date > end_date:
                raise forms.ValidationError(_('Start date must be before end date.'))
        
        return cleaned_data
    
    def get_filter_params(self):
        """Get cleaned filter parameters for the service layer"""
        if not self.is_valid():
            return {}
        
        data = self.cleaned_data
        params = {}
        
        if data.get('start_date'):
            # Convert to datetime for filtering
            params['start_date'] = timezone.make_aware(
                timezone.datetime.combine(data['start_date'], timezone.datetime.min.time())
            )
        
        if data.get('end_date'):
            # Convert to datetime for filtering (end of day)
            params['end_date'] = timezone.make_aware(
                timezone.datetime.combine(data['end_date'], timezone.datetime.max.time())
            )
        
        if data.get('experiments'):
            params['experiment_ids'] = [exp.id for exp in data['experiments']]
        
        if data.get('channels'):
            params['channel_ids'] = [ch.id for ch in data['channels']]
        
        return params


class ExportForm(forms.Form):
    """Form for exporting dashboard data"""
    
    EXPORT_FORMATS = [
        ('png', _('PNG Image')),
        ('pdf', _('PDF Document')),
        ('csv', _('CSV Data')),
        ('json', _('JSON Data')),
    ]
    
    CHART_TYPES = [
        ('active_participants', _('Active Participants')),
        ('session_analytics', _('Session Analytics')),
        ('message_volume', _('Message Volume')),
        ('bot_performance', _('Bot Performance')),
        ('user_engagement', _('User Engagement')),
        ('channel_breakdown', _('Channel Breakdown')),
        ('tag_analytics', _('Tag Analytics')),
        ('overview', _('Overview Statistics')),
    ]
    
    chart_type = forms.ChoiceField(
        choices=CHART_TYPES,
        required=True,
        widget=forms.Select(attrs={
            'class': 'form-select'
        })
    )
    
    export_format = forms.ChoiceField(
        choices=EXPORT_FORMATS,
        required=True,
        widget=forms.Select(attrs={
            'class': 'form-select'
        })
    )
    
    include_filters = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input'
        })
    )
    
    # Hidden fields to pass current filter state
    filter_data = forms.CharField(
        widget=forms.HiddenInput(),
        required=False
    )


class SavedFilterForm(forms.Form):
    """Form for saving/loading filter presets"""
    
    name = forms.CharField(
        max_length=100,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': _('Filter preset name')
        })
    )
    
    is_default = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input'
        })
    )
    
    filter_data = forms.CharField(
        widget=forms.HiddenInput(),
        required=True
    )