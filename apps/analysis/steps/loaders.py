import json
from functools import cached_property

import httpx
import pandas as pd
from pydantic import BaseModel

from apps.analysis.core import BaseStep, Params, PipeOut, StepContext, required
from apps.analysis.exceptions import StepError
from apps.analysis.models import Resource, ResourceType
from apps.experiments.models import Experiment
from apps.service_providers.auth_service import AuthService
from apps.service_providers.models import AuthProvider


class BaseLoader(BaseStep[None, PipeOut]):
    """Base class for steps that load data from a resource."""

    input_type = None

    def run(self, params: Params, context: StepContext = None) -> tuple[str, dict]:
        return self.load(params)

    def load(self, params: Params) -> tuple[str, dict]:
        raise NotImplementedError()


class ResourceLoaderParams(Params):
    resource_id: required(int) = None

    def get_dynamic_config_form_class(self):
        from .forms import ResourceLoaderParamsForm

        return ResourceLoaderParamsForm

    @cached_property
    def resource(self):
        return Resource.objects.get(id=self.resource_id)


class ResourceTextLoader(BaseLoader[str]):
    params = ResourceLoaderParams()
    output_type = str

    def load(self, params: ResourceLoaderParams) -> StepContext[str]:
        with params.resource.file.open("r") as file:
            data = file.read()
            lines = len(data.splitlines())
            self.log.info(f"Loaded {lines} lines of text")
            return StepContext(data, name="text")


class ResourceDataframeLoader(BaseLoader[pd.DataFrame]):
    params = ResourceLoaderParams()
    output_type = pd.DataFrame

    def load(self, params: ResourceLoaderParams) -> StepContext[pd.DataFrame]:
        parser = self._get_parser(params.resource.type)
        with params.resource.file.open("r") as file:
            data = parser(file)
            self.log.info(f"Loaded {len(data)} rows")
            return StepContext(data, name="dataframe")

    def _get_parser(self, type_: ResourceType):
        if type_ == ResourceType.CSV:
            return pd.read_csv
        elif type_ == ResourceType.JSON:

            def _json_parser(file):
                return pd.json_normalize(json.load(file))

            return _json_parser
        elif type_ == ResourceType.JSONL:

            def _jsonl_parser(file):
                lines = file.read().splitlines()
                df_inter = pd.DataFrame(lines)
                df_inter.columns = ["json_element"]
                return pd.json_normalize(df_inter["json_element"].apply(json.loads))

            return _jsonl_parser

        elif type_ == ResourceType.TEXT:

            def _text_parser(file):
                lines = file.read().splitlines()
                df = pd.DataFrame(lines)
                df.columns = ["line"]
                return df

            return _text_parser

        elif type_ == ResourceType.XLSX:
            return pd.read_excel
        else:
            raise ValueError(f"Unsupported resource type: {type_}")


class CommCareAppMeta(BaseModel):
    domain: str
    app_id: str
    name: str

    def app_url(self, cc_url):
        return f"{cc_url}/a/{self.domain}/api/v0.5/application/{self.app_id}/"


class CommCareAppLoaderParams(Params):
    app_list: list[CommCareAppMeta] = None
    auth_provider_id: required(int) = None
    cc_url: required(str) = None
    selected_apps: required(list[CommCareAppMeta]) = None

    def get_dynamic_config_form_class(self):
        from .forms import CommCareAppLoaderParamsForm

        return CommCareAppLoaderParamsForm

    def get_static_config_form_class(self):
        from .forms import CommCareAppLoaderStaticConfigForm

        return CommCareAppLoaderStaticConfigForm

    @property
    def api_params(self):
        return {"format": "json"}

    def get_auth_service(self) -> AuthService:
        return _get_auth_service(self.auth_provider_id)

    def check(self):
        super().check()
        if not self.selected_apps:
            raise StepError("No apps selected for loading")


def _get_auth_service(auth_provider_id: int) -> AuthService:
    try:
        provider = AuthProvider.objects.get(id=auth_provider_id)
    except AuthProvider.DoesNotExist:
        raise StepError("Unable to load the configured authentication provider")
    return provider.get_auth_service()


class CommCareAppLoader(BaseLoader[str]):
    """Load data from a CommCare app API."""

    params = CommCareAppLoaderParams()
    output_type = str

    def load(self, params: CommCareAppLoaderParams) -> list[StepContext[str]]:
        self.log.info(f"Loading data from {len(params.selected_apps)} CommCare apps")
        data = []
        auth_service = params.get_auth_service()
        with auth_service.get_http_client() as client:
            for app_meta in params.selected_apps:
                try:
                    app_data = auth_service.call_with_retries(
                        self._fetch_app_json, app_meta, params.cc_url, client, params.api_params
                    )
                    self.log.info(f"Loaded app {app_meta.name}")
                    data.append(StepContext(app_data, name=app_meta.name))
                except httpx.HTTPError as e:
                    self.log.error(f"Error loading app {app_meta.name} ({app_meta.domain}:{app_meta.app_id}): {e}")
        if not data:
            raise StepError("Unable to load any data from CommCare")
        return data

    def _fetch_app_json(self, app_meta: CommCareAppMeta, cc_url: str, http_client: httpx.Client, params: dict) -> dict:
        self.log.debug(f"Loading app from CommCare: {app_meta.name} ({app_meta.domain}:{app_meta.app_id})")
        try:
            response = http_client.get(app_meta.app_url(cc_url), params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                retry_after = exc.response.headers.get("Retry-After")
                retry_msg = f"retrying after {retry_after} seconds" if retry_after else "retrying"
                self.log.warning(f"Received 429 response, {retry_msg}")
            raise
        return response.json()


class ExperimentLoaderParams(Params):
    experiment_id: required(int) = None

    def get_dynamic_config_form_class(self):
        from .forms import ExperimentLoaderConfigForm

        return ExperimentLoaderConfigForm

    def get_static_config_form_class(self):
        from .forms import ExperimentLoaderConfigForm

        return ExperimentLoaderConfigForm

    def get_experiment(self) -> Experiment:
        return _get_experiment(self.experiment_id)


class ExperimentLoader(BaseLoader[str]):
    """Load data from an existing experiment."""

    params = ExperimentLoaderParams()
    output_type = str

    def load(self, params: ExperimentLoaderParams) -> list[StepContext[str]]:
        experiment = params.get_experiment()
        self.log.info(f"Attempting to distract the '{experiment.name}' chatbot")
        data = experiment.prompt_text
        return StepContext(data, name="text")


def _get_experiment(experiment_id: int) -> Experiment:
    try:
        experiment = Experiment.objects.get(id=experiment_id)
    except Experiment.DoesNotExist:
        raise StepError("Unable to load the configured experiment")
    return experiment
