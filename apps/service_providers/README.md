# Service Providers App

This app contains various models that are used to represent service providers such as LLM APIs,
Voice synthesis APIs, etc.

Much of the app relies on certain conventions and structure in the classes which allow for a more generic
approach to using the service providers.

## Service Provider Models

Each type of provider should have its own model e.g LlmProvider, VoiceProvider etc. These models are used
to store the configuration for the different providers that they represent. Since each provider may require
a different set of configration parameters we store this as an encrypted JSON field in the model.

To differentiate between the subtypes we use the `type` field on the model which is an enum (Django Choices enum).
This enum must have a `form_cls` property which returns the appropriate form class for the provider type.

Here is an example:

```python


class MyProviderType(models.TextChoices):
    type_a = "typeA", _("Type A")
    type_b = "typeB", _("Type B")

    @property
    def form_cls(self) -> Type[forms.ProviderTypeConfigForm]:
        """Returns the config form class for the provider type.
        These forms are used to generate the UI for the provider config
        and to validate the config data."""
        match self:
            case MyProviderType.type_a:
                return forms.MyProviderTypeAConfigForm
            case MyProviderType.type_b:
                return forms.MyProviderTypeBConfigForm
        raise Exception(f"No config form configured for {self}")
   

class MyProvider(BaseTeamModel):
    type = models.CharField(max_length=20, choices=MyProviderType.choices)
    name = models.CharField(max_length=100)
    config = encrypt(models.JSONField(default=dict))
```


## Service Provider Config Forms

The create / update UI for the service providers are generated dynamically based on the provider type. Each provider
subtype should have its own form class which is used to generate the UI and to validate the config data.

```python
class MyProviderTypeAConfigForm(ObfuscatingMixin, ProviderTypeConfigForm):
    """Form for configuring a MyProviderTypeA."""
    obfuscate_fields = ["api_key"]
    
    username = forms.CharField()
    api_key = forms.CharField()
```


## Service Provider Tables

Each service provider type should have its own table definition which is used to display the list of providers.
These can be found in `tables.py` and should be named `<ProviderType>Table` e.g `LlmProviderTable`.

```python
MyProviderTable = make_table(const.MY, MyProvider))
```

## Service Provider Enum

Finally, the `apps.service_providers.utils.ServiceProvider` enum is used to represent the different types
of service providers and offers a single point of reference for the classes for each provider type.

It must be updated for each new provider type.

## Views

The views for service providers are generic and as long as each service provider fits the model described above
there should be no need to make updates the views.

There are four views configured which are used to create, update, delete any of the service provider types.
See `urls.py` for the URL configuration.

## Referencing in the UI

The service providers are referenced in the UI using the `service_providers/service_provider_home.html` template
which will render the list of providers for the given type with options to create new providers or edit existing ones.

```
{% include 'service_providers/service_provider_home.html'
    with provider_type="voice"
    title="Speech Service Providers"
    subtitle="Text to speech" %}
```
(NOTE: Newlines added for readability, remove them when using the template)
