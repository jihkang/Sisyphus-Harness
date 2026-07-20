from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import tomllib
from urllib.parse import urlsplit

from .contracts.policy import CadencePolicy
from .contracts.verification import CommandSpec


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VerificationConfig:
    commands: dict[str, CommandSpec]
    selected_names: tuple[str, ...]

    @property
    def selected_commands(self) -> tuple[CommandSpec, ...]:
        return tuple(self.commands[name] for name in self.selected_names)


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    base_url: str
    model: str
    timeout_seconds: float = 180.0
    temperature: float = 0.1
    max_tokens: int = 4096
    api_key_env: str | None = None

    def __post_init__(self) -> None:
        if not self.base_url.strip() or not self.model.strip():
            raise ValueError("provider base URL and model must be non-empty")
        try:
            parsed_url = urlsplit(self.base_url)
            parsed_url.port
        except ValueError as exc:
            raise ValueError("provider base URL is invalid") from exc
        if (
            parsed_url.scheme not in {"http", "https"}
            or parsed_url.hostname is None
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError(
                "provider base URL must be an HTTP(S) URL without credentials, "
                "query, or fragment"
            )
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("provider timeout must be positive and finite")
        if not math.isfinite(self.temperature) or not 0 <= self.temperature <= 2:
            raise ValueError("provider temperature must be between 0 and 2")
        if not 1 <= self.max_tokens <= 131_072:
            raise ValueError("provider max_tokens must be between 1 and 131072")


@dataclass(frozen=True, slots=True)
class AgentLimits:
    max_steps: int = 24
    max_runtime_seconds: float = 1800.0
    max_file_bytes: int = 262_144
    max_tool_output_chars: int = 24_000
    max_protocol_errors: int = 4
    max_compactions: int = 8

    def __post_init__(self) -> None:
        if not 1 <= self.max_steps <= 256:
            raise ValueError("max_steps must be between 1 and 256")
        if (
            not math.isfinite(self.max_runtime_seconds)
            or self.max_runtime_seconds <= 0
        ):
            raise ValueError("max_runtime_seconds must be positive and finite")
        if not 1024 <= self.max_file_bytes <= 16_777_216:
            raise ValueError("max_file_bytes is outside the supported range")
        if not 1000 <= self.max_tool_output_chars <= 1_000_000:
            raise ValueError("agent byte and output limits are too small")
        if not 0 <= self.max_protocol_errors <= 32:
            raise ValueError("max_protocol_errors is outside the supported range")
        if not 0 <= self.max_compactions <= 64:
            raise ValueError("agent error and compaction limits must be non-negative")


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    provider: ProviderSettings
    limits: AgentLimits
    cadence: CadencePolicy
    strategy_prompt: str
    verification: VerificationConfig
    evolution: EvolutionSettings


@dataclass(frozen=True, slots=True)
class EvolutionSettings:
    max_metric_calls: int = 12
    max_candidate_proposals: int = 4
    seed: int = 0
    min_train_delta: float = 0.01
    min_holdout_delta: float = 0.05

    def __post_init__(self) -> None:
        if not 1 <= self.max_metric_calls <= 100_000:
            raise ValueError("max_metric_calls is outside the supported range")
        if not 1 <= self.max_candidate_proposals <= 10_000:
            raise ValueError("max_candidate_proposals is outside the supported range")
        if self.seed < 0:
            raise ValueError("evolution seed must be non-negative")
        for value in (self.min_train_delta, self.min_holdout_delta):
            if not math.isfinite(value) or not 0 < value <= 1:
                raise ValueError(
                    "evolution score deltas must be greater than 0 and at most 1"
                )


def load_verification_config(path: Path) -> VerificationConfig:
    raw = _load_toml(path)
    _reject_unknown(raw, {"commands", "verify"}, "configuration")
    return _parse_verification(raw)


def load_harness_config(path: Path) -> HarnessConfig:
    raw = _load_toml(path)
    _reject_unknown(
        raw,
        {
            "provider",
            "agent",
            "cadence",
            "prompts",
            "evolution",
            "commands",
            "verify",
        },
        "configuration",
    )
    provider = _required_table(raw, "provider")
    _reject_unknown(
        provider,
        {
            "base_url",
            "model",
            "timeout_seconds",
            "temperature",
            "max_tokens",
            "api_key_env",
        },
        "provider",
    )
    provider_settings = ProviderSettings(
        base_url=_nonempty_string(provider.get("base_url"), "provider.base_url"),
        model=_nonempty_string(provider.get("model"), "provider.model"),
        timeout_seconds=_positive_number(
            provider.get("timeout_seconds", 180.0),
            "provider.timeout_seconds",
        ),
        temperature=_bounded_number(
            provider.get("temperature", 0.1),
            "provider.temperature",
            minimum=0.0,
            maximum=2.0,
        ),
        max_tokens=_bounded_integer(
            provider.get("max_tokens", 4096),
            "provider.max_tokens",
            minimum=1,
            maximum=131_072,
        ),
        api_key_env=_optional_string(provider.get("api_key_env"), "provider.api_key_env"),
    )

    agent = _optional_table(raw, "agent")
    _reject_unknown(
        agent,
        {
            "max_steps",
            "max_runtime_seconds",
            "max_file_bytes",
            "max_tool_output_chars",
            "max_protocol_errors",
            "max_compactions",
        },
        "agent",
    )
    limits = AgentLimits(
        max_steps=_bounded_integer(
            agent.get("max_steps", 24),
            "agent.max_steps",
            minimum=1,
            maximum=256,
        ),
        max_runtime_seconds=_positive_number(
            agent.get("max_runtime_seconds", 1800.0),
            "agent.max_runtime_seconds",
        ),
        max_file_bytes=_bounded_integer(
            agent.get("max_file_bytes", 262_144),
            "agent.max_file_bytes",
            minimum=1024,
            maximum=16_777_216,
        ),
        max_tool_output_chars=_bounded_integer(
            agent.get("max_tool_output_chars", 24_000),
            "agent.max_tool_output_chars",
            minimum=1000,
            maximum=1_000_000,
        ),
        max_protocol_errors=_bounded_integer(
            agent.get("max_protocol_errors", 4),
            "agent.max_protocol_errors",
            minimum=0,
            maximum=32,
        ),
        max_compactions=_bounded_integer(
            agent.get("max_compactions", 8),
            "agent.max_compactions",
            minimum=0,
            maximum=64,
        ),
    )

    cadence = _optional_table(raw, "cadence")
    _reject_unknown(
        cadence,
        {
            "compaction_interval_steps",
            "context_char_limit",
            "keep_recent_events",
            "reflection_interval_steps",
            "observation_interval_steps",
            "verification_interval_mutations",
            "stagnation_limit",
        },
        "cadence",
    )
    cadence_policy = CadencePolicy(
        compaction_interval_steps=_bounded_integer(
            cadence.get("compaction_interval_steps", 6),
            "cadence.compaction_interval_steps",
            minimum=1,
            maximum=64,
        ),
        context_char_limit=_bounded_integer(
            cadence.get("context_char_limit", 48_000),
            "cadence.context_char_limit",
            minimum=4000,
            maximum=1_000_000,
        ),
        keep_recent_events=_bounded_integer(
            cadence.get("keep_recent_events", 4),
            "cadence.keep_recent_events",
            minimum=1,
            maximum=32,
        ),
        reflection_interval_steps=_bounded_integer(
            cadence.get("reflection_interval_steps", 4),
            "cadence.reflection_interval_steps",
            minimum=1,
            maximum=64,
        ),
        observation_interval_steps=_bounded_integer(
            cadence.get("observation_interval_steps", 3),
            "cadence.observation_interval_steps",
            minimum=1,
            maximum=64,
        ),
        verification_interval_mutations=_bounded_integer(
            cadence.get("verification_interval_mutations", 3),
            "cadence.verification_interval_mutations",
            minimum=1,
            maximum=32,
        ),
        stagnation_limit=_bounded_integer(
            cadence.get("stagnation_limit", 4),
            "cadence.stagnation_limit",
            minimum=2,
            maximum=32,
        ),
    )
    prompts = _optional_table(raw, "prompts")
    _reject_unknown(prompts, {"strategy"}, "prompts")
    strategy_prompt = _nonempty_string(
        prompts.get(
            "strategy",
            "Inspect before editing, make the smallest coherent change, and finish only "
            "when the acceptance criteria are satisfied.",
        ),
        "prompts.strategy",
    )
    evolution = _optional_table(raw, "evolution")
    _reject_unknown(
        evolution,
        {
            "max_metric_calls",
            "max_candidate_proposals",
            "seed",
            "min_train_delta",
            "min_holdout_delta",
        },
        "evolution",
    )
    evolution_settings = EvolutionSettings(
        max_metric_calls=_bounded_integer(
            evolution.get("max_metric_calls", 12),
            "evolution.max_metric_calls",
            minimum=1,
            maximum=100_000,
        ),
        max_candidate_proposals=_bounded_integer(
            evolution.get("max_candidate_proposals", 4),
            "evolution.max_candidate_proposals",
            minimum=1,
            maximum=10_000,
        ),
        seed=_bounded_integer(
            evolution.get("seed", 0),
            "evolution.seed",
            minimum=0,
            maximum=2_147_483_647,
        ),
        min_train_delta=_bounded_number(
            evolution.get("min_train_delta", 0.01),
            "evolution.min_train_delta",
            minimum=0.0,
            maximum=1.0,
            strict=True,
        ),
        min_holdout_delta=_bounded_number(
            evolution.get("min_holdout_delta", 0.05),
            "evolution.min_holdout_delta",
            minimum=0.0,
            maximum=1.0,
            strict=True,
        ),
    )
    return HarnessConfig(
        provider=provider_settings,
        limits=limits,
        cadence=cadence_policy,
        strategy_prompt=strategy_prompt,
        verification=_parse_verification(raw),
        evolution=evolution_settings,
    )


def _load_toml(path: Path) -> dict[str, object]:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration file does not exist: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("configuration root must be a table")
    return raw


def _parse_verification(raw: dict[str, object]) -> VerificationConfig:
    raw_commands = raw.get("commands")
    if not isinstance(raw_commands, dict) or not raw_commands:
        raise ConfigError("configuration requires a non-empty [commands] table")
    commands: dict[str, CommandSpec] = {}
    for name, value in raw_commands.items():
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("verification command names must be non-empty strings")
        if not isinstance(value, dict):
            raise ConfigError(f"commands.{name} must be a table")
        unknown = sorted(set(value).difference({"argv", "timeout_seconds", "criteria"}))
        if unknown:
            raise ConfigError(
                f"commands.{name} contains unknown fields: {', '.join(unknown)}"
            )
        argv = _string_list(value.get("argv"), f"commands.{name}.argv")
        criteria = _string_list(
            value.get("criteria"),
            f"commands.{name}.criteria",
        )
        timeout = value.get("timeout_seconds")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ConfigError(f"commands.{name}.timeout_seconds must be numeric")
        try:
            commands[name] = CommandSpec(
                name=name,
                argv=tuple(argv),
                timeout_seconds=float(timeout),
                criteria=tuple(criteria),
            )
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc

    raw_verify = raw.get("verify")
    if not isinstance(raw_verify, dict):
        raise ConfigError("configuration requires a [verify] table")
    unknown_verify = sorted(set(raw_verify).difference({"commands"}))
    if unknown_verify:
        raise ConfigError(
            f"verify contains unknown fields: {', '.join(unknown_verify)}"
        )
    selected = _string_list(raw_verify.get("commands"), "verify.commands")
    if not selected:
        raise ConfigError("verify.commands must contain at least one command name")
    if len(set(selected)) != len(selected):
        raise ConfigError("verify.commands must not contain duplicates")
    missing = [name for name in selected if name not in commands]
    if missing:
        raise ConfigError(
            f"verify.commands references unknown commands: {', '.join(missing)}"
        )
    return VerificationConfig(commands=commands, selected_names=tuple(selected))


def _string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ConfigError(f"{field} must be a list of strings")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{field}[{index}] must be a non-empty string")
        result.append(item.strip())
    return result


def _reject_unknown(
    table: dict[str, object],
    allowed: set[str],
    field: str,
) -> None:
    unknown = sorted(set(table).difference(allowed))
    if unknown:
        label = "top-level fields" if field == "configuration" else "fields"
        raise ConfigError(f"{field} contains unknown {label}: {', '.join(unknown)}")


def _required_table(raw: dict[str, object], field: str) -> dict[str, object]:
    value = raw.get(field)
    if not isinstance(value, dict):
        raise ConfigError(f"configuration requires a [{field}] table")
    return value


def _optional_table(raw: dict[str, object], field: str) -> dict[str, object]:
    value = raw.get(field, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{field} must be a table")
    return value


def _nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _nonempty_string(value, field)


def _positive_number(value: object, field: str) -> float:
    return _bounded_number(value, field, minimum=0.0, maximum=float("inf"), strict=True)


def _bounded_number(
    value: object,
    field: str,
    *,
    minimum: float,
    maximum: float,
    strict: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ConfigError(f"{field} must be finite")
    below = result <= minimum if strict else result < minimum
    if below or result > maximum:
        comparator = "greater than" if strict else "at least"
        raise ConfigError(
            f"{field} must be {comparator} {minimum} and at most {maximum}"
        )
    return result


def _bounded_integer(
    value: object,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{field} must be an integer")
    if value < minimum or value > maximum:
        raise ConfigError(f"{field} must be between {minimum} and {maximum}")
    return value
