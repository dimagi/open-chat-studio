from dateutil.relativedelta import relativedelta
from django import forms
from django.contrib.auth import get_user_model
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db.models import TextChoices
from django.utils import timezone

from apps.admin.models import ChatWidgetConfig, OcsConfiguration, SiteConfig
from apps.teams.models import Team

User = get_user_model()


class DateRanges(TextChoices):
    THIS_MONTH = "m0", "Month to date"
    LAST_MONTH = "m1", "Last month"
    LAST_30_DAYS = "d30", "Last 30 days"
    CUSTOM = "custom", "Custom"

    def get_date_range(self):
        now = timezone.now().date()
        match self:
            case DateRanges.THIS_MONTH:
                return now.replace(day=1), now
            case DateRanges.LAST_MONTH:
                start = now - relativedelta(months=1, day=1)
                end = start + relativedelta(day=31)  # gets coerced to the last day of the month
                return start, end
            case DateRanges.LAST_30_DAYS:
                return now - relativedelta(days=30), now
            case DateRanges.CUSTOM:
                return None
        return None


class DateRangeForm(forms.Form):
    range_type = forms.ChoiceField(label="Date Range", choices=DateRanges.choices, initial=DateRanges.LAST_30_DAYS)
    start = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def get_date_range(self):
        range_type = self.cleaned_data["range_type"]
        if range_type == DateRanges.CUSTOM:
            return self.cleaned_data["start"], self.cleaned_data["end"]
        return DateRanges(range_type).get_date_range()


class FlagUpdateForm(forms.Form):
    everyone = forms.BooleanField(required=False)
    testing = forms.BooleanField(required=False)
    superusers = forms.BooleanField(required=False)
    rollout = forms.BooleanField(required=False)
    percent = forms.IntegerField(
        required=False,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Percentage for rollout (0-100)",
    )
    teams = forms.ModelMultipleChoiceField(
        queryset=Team.objects.all(), required=False, widget=forms.MultipleHiddenInput()
    )
    users = forms.ModelMultipleChoiceField(
        queryset=User.objects.all(), required=False, widget=forms.MultipleHiddenInput()
    )

    def clean_percent(self):
        percent = self.cleaned_data.get("percent")
        rollout = self.cleaned_data.get("rollout")

        if rollout and percent is None:
            raise forms.ValidationError("Percentage is required when rollout is enabled")

        return percent


class OcsConfigurationForm(forms.Form):
    """Form for editing the single OcsConfiguration instance with individual fields."""

    chat_widget_enabled = forms.BooleanField(
        required=False, label="Enable Chat Widget", help_text="Enable the chat widget on the site"
    )

    chatbot_id = forms.CharField(
        max_length=255,
        required=False,
        label="Chatbot ID",
        help_text="ID of the chatbot to use for the widget",
        widget=forms.TextInput(attrs={"placeholder": "Enter chatbot ID"}),
    )

    button_text = forms.CharField(
        max_length=100,
        required=False,
        label="Button Text",
        initial="Ask me!",
        help_text="Text displayed on the chat button",
        widget=forms.TextInput(attrs={"placeholder": "Ask me!"}),
    )

    welcome_messages = forms.CharField(
        required=False,
        label="Welcome Messages",
        help_text="Enter one message per line",
        widget=forms.Textarea(
            attrs={"rows": 3, "placeholder": "Hi! Welcome to our support chat.\nHow can we help you today?"}
        ),
        initial="Hi! Welcome to our support chat.\nHow can we help you today?",
    )

    starter_questions = forms.CharField(
        required=False,
        label="Starter Questions",
        help_text="Enter one question per line",
        widget=forms.Textarea(
            attrs={"rows": 3, "placeholder": "How do I create a bot?\nHow do I connect my bot to WhatsApp?"}
        ),
        initial="How do I create a bot?\nHow do I connect my bot to WhatsApp?",
    )

    position = forms.ChoiceField(
        choices=[("left", "Left"), ("right", "Right")],
        initial="right",
        label="Widget Position",
        help_text="Position of the chat widget on the page",
    )

    language = forms.CharField(
        max_length=10,
        required=False,
        label="Language",
        initial="en",
        help_text="Language code for the widget (e.g., 'en', 'es', 'fr'). Defaults to 'en'.",
        widget=forms.TextInput(attrs={"placeholder": "en"}),
    )

    translations_url = forms.URLField(
        required=False,
        label="Translations URL",
        help_text="URL to load custom translations from.",
        widget=forms.URLInput(attrs={"placeholder": "https://example.com/translations/en.json"}),
    )

    def __init__(self, *args, **kwargs):
        self.instance = kwargs.pop("instance", None)
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.config:
            config = self.instance.config
            chat_widget = config.chat_widget

            self.fields["chat_widget_enabled"].initial = chat_widget.enabled
            self.fields["chatbot_id"].initial = chat_widget.chatbot_id
            self.fields["button_text"].initial = chat_widget.button_text
            self.fields["welcome_messages"].initial = "\n".join(chat_widget.welcome_messages)
            self.fields["starter_questions"].initial = "\n".join(chat_widget.starter_questions)
            self.fields["position"].initial = chat_widget.position
            self.fields["language"].initial = chat_widget.language
            self.fields["translations_url"].initial = chat_widget.translations_url

    def save(self):
        welcome_messages = [msg.strip() for msg in self.cleaned_data["welcome_messages"].split("\n") if msg.strip()]
        starter_questions = [q.strip() for q in self.cleaned_data["starter_questions"].split("\n") if q.strip()]

        chat_widget_config = ChatWidgetConfig(
            enabled=self.cleaned_data["chat_widget_enabled"],
            chatbot_id=self.cleaned_data["chatbot_id"],
            button_text=self.cleaned_data["button_text"],
            welcome_messages=welcome_messages,
            starter_questions=starter_questions,
            position=self.cleaned_data["position"],
            language=self.cleaned_data["language"],
            translations_url=self.cleaned_data["translations_url"],
        )

        site_config = SiteConfig(chat_widget=chat_widget_config)

        # Get or create the configuration instance
        if self.instance:
            config_instance = self.instance
        else:
            config_instance = OcsConfiguration.objects.first()
            if not config_instance:
                config_instance = OcsConfiguration()

        config_instance.config = site_config
        config_instance.save()

        return config_instance
