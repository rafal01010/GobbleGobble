from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Annotated, Literal

from dotenv import dotenv_values
from dotenv.parser import parse_stream
from pydantic import (
    Field,
    SecretStr,
    StringConstraints,
    ValidationError,
    field_validator,
)
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.exceptions import SettingsError
from pydantic_settings.sources.utils import parse_env_vars

Mode = Literal["live", "diagnostic", "test"]
LocationProvider = Literal["google_grounding_lite", "google_geocoding", "geoapify"]
DiscoveryProvider = Literal["google_grounding_lite", "google_places", "geoapify"]
RouteProvider = Literal["google_grounding_lite", "google_routes", "geoapify"]
WebProvider = Literal["openai"]
ModelId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Seconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]
ConfigurationVariable = Literal[
    "configuration",
    "GG_LOCATION_PROVIDER",
    "GG_DISCOVERY_PROVIDER",
    "GG_ROUTE_PROVIDER",
    "GG_WEB_PROVIDER",
    "GG_OWNER_MODEL",
    "GG_RESEARCH_MODEL",
    "GG_GEOGRAPHIC_TIMEOUT_SECONDS",
    "GG_MODEL_TIMEOUT_SECONDS",
    "OPENAI_API_KEY",
    "GOOGLE_MAPS_API_KEY",
    "GEOAPIFY_API_KEY",
]

_BACKEND_ENV = Path(__file__).resolve().parents[2] / ".env"
_VARIABLES: dict[str, ConfigurationVariable] = {
    "location_provider": "GG_LOCATION_PROVIDER",
    "discovery_provider": "GG_DISCOVERY_PROVIDER",
    "route_provider": "GG_ROUTE_PROVIDER",
    "web_provider": "GG_WEB_PROVIDER",
    "owner_model": "GG_OWNER_MODEL",
    "research_model": "GG_RESEARCH_MODEL",
    "geographic_timeout_seconds": "GG_GEOGRAPHIC_TIMEOUT_SECONDS",
    "model_timeout_seconds": "GG_MODEL_TIMEOUT_SECONDS",
    "OPENAI_API_KEY": "OPENAI_API_KEY",
    "GOOGLE_MAPS_API_KEY": "GOOGLE_MAPS_API_KEY",
    "GEOAPIFY_API_KEY": "GEOAPIFY_API_KEY",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GG_",
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    mode: Mode
    location_provider: LocationProvider
    discovery_provider: DiscoveryProvider
    route_provider: RouteProvider
    web_provider: WebProvider
    owner_model: ModelId = Field(repr=False)
    research_model: ModelId = Field(repr=False)
    geographic_timeout_seconds: Seconds
    model_timeout_seconds: Seconds
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="OPENAI_API_KEY",
        repr=False,
    )
    google_maps_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="GOOGLE_MAPS_API_KEY",
        repr=False,
    )
    geoapify_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="GEOAPIFY_API_KEY",
        repr=False,
    )

    @field_validator(
        "openai_api_key", "google_maps_api_key", "geoapify_api_key", mode="before"
    )
    @classmethod
    def _blank_secret(cls, value: object) -> object:
        text = value.get_secret_value() if isinstance(value, SecretStr) else value
        return None if isinstance(text, str) and not text.strip() else value


@dataclass(frozen=True)
class ConfigurationIssue:
    variable: ConfigurationVariable
    code: Literal["missing", "invalid", "unreadable"]


class ConfigurationError(Exception):
    def __init__(self, issues: tuple[ConfigurationIssue, ...]) -> None:
        self.issues = issues
        super().__init__(
            "; ".join(f"{issue.variable}: {issue.code}" for issue in issues)
        )


@dataclass(frozen=True)
class SafeDiagnostics:
    mode: Mode
    location_provider: LocationProvider
    discovery_provider: DiscoveryProvider
    route_provider: RouteProvider
    web_provider: WebProvider
    owner_model_configured: bool
    research_model_configured: bool
    geographic_timeout_seconds: float
    model_timeout_seconds: float
    openai_key_present: bool
    google_maps_key_present: bool
    geoapify_key_present: bool


def _dotenv_source(env_file: Path | str | None) -> DotEnvSettingsSource:
    source = DotEnvSettingsSource(Settings, env_file=None)
    if env_file is None:
        return source
    try:
        contents = Path(env_file).read_text(encoding="utf-8")
    except FileNotFoundError:
        return source
    if any(binding.error for binding in parse_stream(StringIO(contents))):
        raise ConfigurationError(
            (ConfigurationIssue("configuration", "invalid"),)
        ) from None
    source.env_vars = parse_env_vars(dotenv_values(stream=StringIO(contents)))
    known_names = {variable.lower() for variable in _VARIABLES.values()} | {"gg_mode"}
    if source.env_vars.keys() - known_names:
        raise ConfigurationError(
            (ConfigurationIssue("configuration", "invalid"),)
        ) from None
    return source


def load_settings(
    *, mode: Mode, env_file: Path | str | None = _BACKEND_ENV
) -> Settings:
    try:
        values = _dotenv_source(env_file)() | EnvSettingsSource(Settings)()
        values["mode"] = mode
        settings = Settings(**values)
    except ValidationError as error:
        issues = tuple(
            dict.fromkeys(
                ConfigurationIssue(
                    _VARIABLES.get(str(item["loc"][0]), "configuration")
                    if item["loc"]
                    else "configuration",
                    "missing" if item["type"] == "missing" else "invalid",
                )
                for item in error.errors(
                    include_url=False, include_context=False, include_input=False
                )
            )
        )
        raise ConfigurationError(issues) from None
    except (OSError, UnicodeError):
        raise ConfigurationError(
            (ConfigurationIssue("configuration", "unreadable"),)
        ) from None
    except SettingsError:
        raise ConfigurationError(
            (ConfigurationIssue("configuration", "invalid"),)
        ) from None

    if settings.mode == "live":
        missing_credentials = []
        if settings.openai_api_key is None:
            missing_credentials.append(ConfigurationIssue("OPENAI_API_KEY", "missing"))
        providers = (
            settings.location_provider,
            settings.discovery_provider,
            settings.route_provider,
        )
        if (
            any(provider.startswith("google_") for provider in providers)
            and settings.google_maps_api_key is None
        ):
            missing_credentials.append(
                ConfigurationIssue("GOOGLE_MAPS_API_KEY", "missing")
            )
        if "geoapify" in providers and settings.geoapify_api_key is None:
            missing_credentials.append(
                ConfigurationIssue("GEOAPIFY_API_KEY", "missing")
            )
        if missing_credentials:
            raise ConfigurationError(tuple(missing_credentials)) from None
    return settings


def safe_diagnostics(settings: Settings) -> SafeDiagnostics:
    return SafeDiagnostics(
        mode=settings.mode,
        location_provider=settings.location_provider,
        discovery_provider=settings.discovery_provider,
        route_provider=settings.route_provider,
        web_provider=settings.web_provider,
        owner_model_configured=bool(settings.owner_model),
        research_model_configured=bool(settings.research_model),
        geographic_timeout_seconds=settings.geographic_timeout_seconds,
        model_timeout_seconds=settings.model_timeout_seconds,
        openai_key_present=settings.openai_api_key is not None,
        google_maps_key_present=settings.google_maps_api_key is not None,
        geoapify_key_present=settings.geoapify_api_key is not None,
    )
