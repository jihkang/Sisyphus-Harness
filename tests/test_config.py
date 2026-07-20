from __future__ import annotations

from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from sisyphus_harness.config import (
    AgentLimits,
    CadencePolicy,
    ConfigError,
    EvolutionSettings,
    ProviderSettings,
    load_harness_config,
    load_verification_config,
)


VALID_CONFIG = """
[commands.tests]
argv = ["python3", "-m", "unittest"]
timeout_seconds = 30
criteria = ["unit tests pass"]

[verify]
commands = ["tests"]
"""

MINIMAL_HARNESS_CONFIG = """
[provider]
base_url = "http://127.0.0.1:8080/v1"
model = "local-model"

[commands.tests]
argv = ["python3", "-m", "unittest"]
timeout_seconds = 30
criteria = ["unit tests pass"]

[verify]
commands = ["tests"]
"""


class ConfigTests(unittest.TestCase):
    def load(self, content: str):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(textwrap.dedent(content), encoding="utf-8")
            return load_verification_config(path)

    def assert_config_error(self, content: str, message: str) -> None:
        with self.assertRaisesRegex(ConfigError, message):
            self.load(content)

    def load_harness(self, content: str):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(textwrap.dedent(content), encoding="utf-8")
            return load_harness_config(path)

    def test_loads_structured_commands_and_criteria(self) -> None:
        config = self.load(VALID_CONFIG)

        self.assertEqual(config.selected_names, ("tests",))
        self.assertEqual(
            config.selected_commands[0].argv,
            ("python3", "-m", "unittest"),
        )
        self.assertEqual(
            config.selected_commands[0].criteria,
            ("unit tests pass",),
        )

    def test_rejects_zero_commands(self) -> None:
        self.assert_config_error(
            """
            [commands]
            [verify]
            commands = []
            """,
            "non-empty",
        )

    def test_rejects_unknown_top_level_key(self) -> None:
        self.assert_config_error(
            VALID_CONFIG + "\nunsafe = true\n",
            "unknown fields",
        )

    def test_rejects_unknown_command_key(self) -> None:
        self.assert_config_error(
            """
            [commands.tests]
            argv = ["python3"]
            timeout_seconds = 1
            criteria = ["runs"]
            shell = true

            [verify]
            commands = ["tests"]
            """,
            "commands.tests contains unknown fields: shell",
        )

    def test_rejects_shell_string_instead_of_argv(self) -> None:
        self.assert_config_error(
            """
            [commands.tests]
            argv = "python3 -m unittest"
            timeout_seconds = 1
            criteria = ["runs"]

            [verify]
            commands = ["tests"]
            """,
            "argv must be a list",
        )

    def test_rejects_unknown_selected_command(self) -> None:
        self.assert_config_error(
            VALID_CONFIG.replace('["tests"]', '["missing"]'),
            "references unknown commands",
        )

    def test_rejects_duplicate_selected_command(self) -> None:
        self.assert_config_error(
            VALID_CONFIG.replace('["tests"]', '["tests", "tests"]'),
            "must not contain duplicates",
        )

    def test_rejects_non_finite_timeout(self) -> None:
        self.assert_config_error(
            VALID_CONFIG.replace("timeout_seconds = 30", "timeout_seconds = inf"),
            "positive timeout",
        )

    def test_rejects_duplicate_criteria(self) -> None:
        self.assert_config_error(
            VALID_CONFIG.replace(
                'criteria = ["unit tests pass"]',
                'criteria = ["unit tests pass", "unit tests pass"]',
            ),
            "unique acceptance criteria",
        )

    def test_loads_complete_harness_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                textwrap.dedent(
                    """
                    [provider]
                    base_url = "http://127.0.0.1:8080/v1"
                    model = "local-model"
                    timeout_seconds = 120
                    temperature = 0.2
                    max_tokens = 2048

                    [agent]
                    max_steps = 12
                    max_runtime_seconds = 600
                    max_file_bytes = 131072
                    max_tool_output_chars = 12000
                    max_protocol_errors = 2
                    max_compactions = 3

                    [cadence]
                    compaction_interval_steps = 3
                    context_char_limit = 12000
                    keep_recent_events = 2
                    reflection_interval_steps = 2
                    observation_interval_steps = 2
                    verification_interval_mutations = 2
                    stagnation_limit = 3

                    [prompts]
                    strategy = "Inspect and make a focused change."

                    [commands.tests]
                    argv = ["python3", "-m", "unittest"]
                    timeout_seconds = 30
                    criteria = ["tests pass"]

                    [verify]
                    commands = ["tests"]
                    """
                ),
                encoding="utf-8",
            )
            config = load_harness_config(path)

        self.assertEqual(config.provider.model, "local-model")
        self.assertEqual(config.limits.max_steps, 12)
        self.assertEqual(config.cadence.compaction_interval_steps, 3)
        self.assertEqual(config.strategy_prompt, "Inspect and make a focused change.")

    def test_harness_config_rejects_unknown_cadence_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                textwrap.dedent(
                    """
                    [provider]
                    base_url = "http://localhost/v1"
                    model = "model"

                    [cadence]
                    unsafe_override = true

                    [commands.tests]
                    argv = ["python3"]
                    timeout_seconds = 1
                    criteria = ["runs"]

                    [verify]
                    commands = ["tests"]
                    """
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ConfigError, "cadence contains unknown fields"):
                load_harness_config(path)

    def test_direct_cadence_construction_enforces_bounds(self) -> None:
        with self.assertRaisesRegex(ValueError, "context_char_limit"):
            CadencePolicy(context_char_limit=100)

    def test_direct_settings_construction_enforces_every_boundary(self) -> None:
        invalid_settings = (
            (
                lambda: ProviderSettings(base_url="", model="model"),
                "base URL and model",
            ),
            (
                lambda: ProviderSettings(
                    base_url="file:///tmp/provider",
                    model="model",
                ),
                "HTTP",
            ),
            (
                lambda: ProviderSettings(
                    base_url="http://user:secret@localhost/v1",
                    model="model",
                ),
                "without credentials",
            ),
            (
                lambda: ProviderSettings(
                    base_url="http://localhost:invalid/v1",
                    model="model",
                ),
                "invalid",
            ),
            (
                lambda: ProviderSettings(
                    base_url="http://localhost/v1",
                    model="model",
                    timeout_seconds=0,
                ),
                "timeout",
            ),
            (
                lambda: ProviderSettings(
                    base_url="http://localhost/v1",
                    model="model",
                    temperature=float("inf"),
                ),
                "temperature",
            ),
            (
                lambda: ProviderSettings(
                    base_url="http://localhost/v1",
                    model="model",
                    max_tokens=0,
                ),
                "max_tokens",
            ),
            (lambda: AgentLimits(max_steps=0), "max_steps"),
            (lambda: AgentLimits(max_runtime_seconds=0), "max_runtime_seconds"),
            (lambda: AgentLimits(max_file_bytes=100), "max_file_bytes"),
            (
                lambda: AgentLimits(max_tool_output_chars=999),
                "output limits",
            ),
            (
                lambda: AgentLimits(max_protocol_errors=-1),
                "max_protocol_errors",
            ),
            (lambda: AgentLimits(max_compactions=-1), "compaction"),
            (
                lambda: CadencePolicy(compaction_interval_steps=0),
                "positive",
            ),
            (
                lambda: CadencePolicy(compaction_interval_steps=65),
                "compaction_interval_steps",
            ),
            (
                lambda: CadencePolicy(keep_recent_events=33),
                "keep_recent_events",
            ),
            (
                lambda: CadencePolicy(reflection_interval_steps=65),
                "reflection_interval_steps",
            ),
            (
                lambda: CadencePolicy(observation_interval_steps=65),
                "observation_interval_steps",
            ),
            (
                lambda: CadencePolicy(verification_interval_mutations=33),
                "verification_interval_mutations",
            ),
            (
                lambda: CadencePolicy(stagnation_limit=33),
                "stagnation_limit",
            ),
            (
                lambda: EvolutionSettings(max_metric_calls=0),
                "max_metric_calls",
            ),
            (
                lambda: EvolutionSettings(max_candidate_proposals=0),
                "max_candidate_proposals",
            ),
            (lambda: EvolutionSettings(seed=-1), "seed"),
            (
                lambda: EvolutionSettings(min_holdout_delta=float("nan")),
                "score deltas",
            ),
            (
                lambda: EvolutionSettings(min_train_delta=0.0),
                "greater than 0",
            ),
        )
        for construct, message in invalid_settings:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    construct()

    def test_file_and_toml_errors_are_normalized(self) -> None:
        missing = Path(self.id()) / "missing.toml"
        with self.assertRaisesRegex(ConfigError, "does not exist"):
            load_verification_config(missing)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.toml"
            path.write_text("[commands", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "invalid TOML"):
                load_verification_config(path)

            path.write_text("value = 1", encoding="utf-8")
            with patch(
                "sisyphus_harness.config.tomllib.loads",
                return_value=[],
            ):
                with self.assertRaisesRegex(ConfigError, "root must be a table"):
                    load_verification_config(path)

    def test_verification_parser_rejects_malformed_tables_and_values(self) -> None:
        cases = (
            (
                """
                [commands]
                tests = "python3"
                [verify]
                commands = ["tests"]
                """,
                "commands.tests must be a table",
            ),
            (
                VALID_CONFIG.replace("timeout_seconds = 30", 'timeout_seconds = "30"'),
                "timeout_seconds must be numeric",
            ),
            (
                """
                verify = "tests"

                [commands.tests]
                argv = ["python3"]
                timeout_seconds = 1
                criteria = ["runs"]
                """,
                "requires a \\[verify\\] table",
            ),
            (
                VALID_CONFIG.replace(
                    'commands = ["tests"]',
                    'commands = ["tests"]\nunknown = true',
                ),
                "verify contains unknown fields",
            ),
            (
                VALID_CONFIG.replace(
                    'argv = ["python3", "-m", "unittest"]',
                    'argv = ["python3", " "]',
                ),
                "argv\\[1\\] must be a non-empty string",
            ),
            (
                VALID_CONFIG.replace('commands = ["tests"]', "commands = []"),
                "at least one command",
            ),
        )
        for content, message in cases:
            with self.subTest(message=message):
                self.assert_config_error(content, message)

    def test_harness_parser_rejects_wrong_types_and_ranges(self) -> None:
        cases = (
            (
                MINIMAL_HARNESS_CONFIG.replace(
                    '[provider]\nbase_url = "http://127.0.0.1:8080/v1"\nmodel = "local-model"\n',
                    'provider = "invalid"\n',
                ),
                "requires a \\[provider\\] table",
            ),
            (
                MINIMAL_HARNESS_CONFIG.replace(
                    'model = "local-model"',
                    'model = "local-model"\ntimeout_seconds = true',
                ),
                "timeout_seconds must be numeric",
            ),
            (
                MINIMAL_HARNESS_CONFIG.replace(
                    'model = "local-model"',
                    'model = "local-model"\ntemperature = inf',
                ),
                "temperature must be finite",
            ),
            (
                MINIMAL_HARNESS_CONFIG.replace(
                    'model = "local-model"',
                    'model = "local-model"\nmax_tokens = true',
                ),
                "max_tokens must be an integer",
            ),
            (
                MINIMAL_HARNESS_CONFIG.replace(
                    'model = "local-model"',
                    'model = "local-model"\napi_key_env = ""',
                ),
                "api_key_env must be a non-empty string",
            ),
            (
                'agent = "invalid"\n' + MINIMAL_HARNESS_CONFIG,
                "agent must be a table",
            ),
            (
                'cadence = "invalid"\n' + MINIMAL_HARNESS_CONFIG,
                "cadence must be a table",
            ),
            (
                'prompts = "invalid"\n' + MINIMAL_HARNESS_CONFIG,
                "prompts must be a table",
            ),
            (
                'evolution = "invalid"\n' + MINIMAL_HARNESS_CONFIG,
                "evolution must be a table",
            ),
            (
                MINIMAL_HARNESS_CONFIG.replace(
                    'model = "local-model"',
                    'model = "local-model"\ntemperature = 3',
                ),
                "temperature must be at least",
            ),
            (
                MINIMAL_HARNESS_CONFIG.replace(
                    "[commands.tests]",
                    "[agent]\nmax_steps = 0\n\n[commands.tests]",
                ),
                "max_steps must be between",
            ),
        )
        for content, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ConfigError, message):
                    self.load_harness(content)


if __name__ == "__main__":
    unittest.main()
