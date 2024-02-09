import json
from functools import cached_property

import httpx
import pandas as pd
from pydantic import BaseModel

from apps.analysis.core import BaseStep, Params, PipeOut, StepContext, required
from apps.analysis.exceptions import StepError
from apps.analysis.models import Resource, ResourceType
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
    param_schema = ResourceLoaderParams
    output_type = str

    def load(self, params: ResourceLoaderParams) -> StepContext[str]:
        with params.resource.file.open("r") as file:
            data = file.read()
            lines = len(data.splitlines())
            self.log.info(f"Loaded {lines} lines of text")
            return StepContext(data, name="text")


class ResourceDataframeLoader(BaseLoader[pd.DataFrame]):
    param_schema = ResourceLoaderParams
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


class CommCareAppLoaderParams(Params):
    app_list: list[CommCareAppMeta] = None
    auth_provider_id: required(int) = None
    cc_url: required(str) = None
    cc_domain: required(str) = None
    cc_app_id: required(str) = None

    def get_dynamic_config_form_class(self):
        from .forms import CommCareAppLoaderParamsForm

        return CommCareAppLoaderParamsForm

    def get_static_config_form_class(self):
        from .forms import CommCareAppLoaderStaticConfigForm

        return CommCareAppLoaderStaticConfigForm

    @property
    def api_url(self):
        return f"{self.cc_url}/a/{self.cc_domain}/api/v0.5/application/{self.cc_app_id}/"

    @property
    def api_params(self):
        return {"format": "json"}

    def get_auth_service(self) -> AuthService:
        return _get_auth_service(self.auth_provider_id)


def _get_auth_service(auth_provider_id: int) -> AuthService:
    try:
        provider = AuthProvider.objects.get(id=auth_provider_id)
    except AuthProvider.DoesNotExist:
        raise StepError("Unable to load the configured authentication provider")
    return provider.get_auth_service()


class CommCareAppLoader(BaseLoader[str]):
    """Load data from a CommCare app API."""

    param_schema = CommCareAppLoaderParams
    output_type = str

    def load(self, params: CommCareAppLoaderParams) -> StepContext[str]:
        self.log.info(f"Loading app from CommCare: {params.cc_domain}:{params.cc_app_id}")
        with params.get_auth_service().get_http_client() as client:
            try:
                response = client.get(params.api_url, params=params.api_params)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise StepError("Unable to load data from CommCare API", exc)
            app_data = response.json()

        return StepContext(json.dumps(app_data))
