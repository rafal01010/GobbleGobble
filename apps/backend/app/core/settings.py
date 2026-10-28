from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from dotenv import dotenv_values
from dotenv.parser import parse_stream
from pydantic import (
    AnyHttpUrl,
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
ReviewProvider = Literal["google_reviews"]
ModelId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Seconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]
ConfigurationVariable = Literal[
    "configuration",
    "GG_DATABASE_PATH",
    "GG_TRUSTED_FRONTEND_ORIGINS",
    "GG_SESSION_COOKIE_SECURE",
    "GG_LOCATION_PROVIDER",
    "GG_DISCOVERY_PROVIDER",
    "GG_ROUTE_PROVIDER",
    "GG_WEB_PROVIDER",
    "GG_REVIEW_PROVIDER",
    "GG_REVIEW_TIMEOUT_SECONDS",
    "GG_OWNER_MODEL",
    "GG_RESEARCH_MODEL",
    "GG_GEOGRAPHIC_TIMEOUT_SECONDS",
    "GG_MODEL_TIMEOUT_SECONDS",
    "OPENAI_API_KEY",
    "GOOGLE_MAPS_API_KEY",
    "GEOAPIFY_API_KEY",
]

_BACKEND_ENV = Path(__file__).resolve().parents[2] / ".env"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_VARIABLES: dict[str, ConfigurationVariable] = {
    "database_path": "GG_DATABASE_PATH",
    "trusted_frontend_origins": "GG_TRUSTED_FRONTEND_ORIGINS",
    "session_cookie_secure": "GG_SESSION_COOKIE_SECURE",
    "location_provider": "GG_LOCATION_PROVIDER",
    "discovery_provider": "GG_DISCOVERY_PROVIDER",
    "route_provider": "GG_ROUTE_PROVIDER",
    "web_provider": "GG_WEB_PROVIDER",
    "review_provider": "GG_REVIEW_PROVIDER",
    "review_timeout_seconds": "GG_REVIEW_TIMEOUT_SECONDS",
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
    review_provider: ReviewProvider | None = None
    review_timeout_seconds: Seconds | None = None
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


class ReviewSettings(BaseSettings):
    """Anonymous review-only configuration, independent of all credentials."""

    model_config = SettingsConfigDict(
        env_prefix="GG_", frozen=True, extra="forbid", hide_input_in_errors=True
    )

    mode: Mode
    review_provider: ReviewProvider | None = None
    review_timeout_seconds: Seconds | None = None


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GG_", frozen=True, extra="forbid", hide_input_in_errors=True
    )

    database_path: Path = _REPOSITORY_ROOT / "data/runtime/gobblegobble.sqlite3"

    @field_validator("database_path", mode="before")
    @classmethod
    def _local_database_path(cls, value: object) -> object:
        if not isinstance(value, str | Path):
            return value
        text = str(value)
        if not text.strip() or text == ":memory:" or ":" in text or "\x00" in text:
            raise ValueError("expected a durable local file path")
        path = Path(text)
        return (path if path.is_absolute() else _REPOSITORY_ROOT / path).resolve()


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GG_", frozen=True, extra="forbid", hide_input_in_errors=True
    )

    trusted_frontend_origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
    session_cookie_secure: bool = False

    @field_validator("trusted_frontend_origins")
    @classmethod
    def _exact_origins(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values or len(set(values)) != len(values):
            raise ValueError("expected unique concrete origins")
        for value in values:
            try:
                parsed = urlsplit(value)
                host = parsed.hostname
                port = parsed.port
                host_part = f"[{host}]" if host and ":" in host else host
                canonical = f"{parsed.scheme}://{host_part}"
                if port is not None:
                    canonical += f":{port}"
                valid = (
                    value.isascii()
                    and str(AnyHttpUrl(value)).removesuffix("/") == value
                    and not any(char.isspace() for char in value)
                    and "*" not in value
                    and parsed.scheme in ("http", "https")
                    and bool(host)
                    and parsed.username is None
                    and parsed.password is None
                    and not parsed.path
                    and not parsed.query
                    and not parsed.fragment
                    and value == canonical
                    and port not in (0, 80 if parsed.scheme == "http" else 443)
                )
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("expected an exact HTTP or HTTPS origin")
        return values


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
    review_provider: ReviewProvider | None = None
    review_timeout_seconds: float | None = None


@dataclass(frozen=True)
class ReviewDiagnostics:
    mode: Mode
    review_provider: ReviewProvider | None
    review_timeout_seconds: float | None


def review_diagnostics(settings: Settings | ReviewSettings) -> ReviewDiagnostics:
    return ReviewDiagnostics(
        settings.mode, settings.review_provider, settings.review_timeout_seconds
    )


def _dotenv_source(
    env_file: Path | str | None, settings_type: type[BaseSettings] = Settings
) -> DotEnvSettingsSource:
    source = DotEnvSettingsSource(settings_type, env_file=None)
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
    prefix = settings_type.model_config.get("env_prefix", "")
    selected_names = {
        (
            field.validation_alias
            if isinstance(field.validation_alias, str)
            else f"{prefix}{name}"
        ).lower()
        for name, field in settings_type.model_fields.items()
    }
    source.env_vars = {
        name: value for name, value in source.env_vars.items() if name in selected_names
    }
    return source


def _load_settings[SettingsType: BaseSettings](
    settings_type: type[SettingsType],
    *,
    env_file: Path | str | None,
    mode: Mode | None = None,
) -> SettingsType:
    try:
        values = (
            _dotenv_source(env_file, settings_type)()
            | EnvSettingsSource(settings_type)()
        )
        if mode is not None:
            values["mode"] = mode
        return settings_type(**values)
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
    except OSError, UnicodeError:
        raise ConfigurationError(
            (ConfigurationIssue("configuration", "unreadable"),)
        ) from None
    except SettingsError:
        raise ConfigurationError(
            (ConfigurationIssue("configuration", "invalid"),)
        ) from None


def load_database_settings(
    *, env_file: Path | str | None = _BACKEND_ENV
) -> DatabaseSettings:
    return _load_settings(DatabaseSettings, env_file=env_file)


def load_api_settings(*, env_file: Path | str | None = _BACKEND_ENV) -> ApiSettings:
    return _load_settings(ApiSettings, env_file=env_file)


def load_review_settings(
    *, mode: Mode, env_file: Path | str | None = _BACKEND_ENV
) -> ReviewSettings:
    """Load reviews without selecting geography, models, keys or billing."""
    return _load_settings(ReviewSettings, mode=mode, env_file=env_file)


def load_settings(
    *, mode: Mode, env_file: Path | str | None = _BACKEND_ENV
) -> Settings:
    settings = _load_settings(Settings, mode=mode, env_file=env_file)

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


def load_location_settings(*, env_file: Path | str | None = _BACKEND_ENV) -> Settings:
    """Load configured settings without requiring unrelated model credentials."""
    return _load_settings(Settings, mode="live", env_file=env_file)


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
        review_provider=settings.review_provider,
        review_timeout_seconds=settings.review_timeout_seconds,
    )
