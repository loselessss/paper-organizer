import unittest

from paper_organizer.infra.ollama_acceleration import (
    OLLAMA_IGPU_ENVIRONMENT,
    configure_ollama_igpu,
)


class OllamaAccelerationTests(unittest.TestCase):
    def test_enable_and_disable_update_user_and_process_environments(self):
        written = []
        broadcasts = []
        environment = {}

        configure_ollama_igpu(
            True,
            user_value_writer=lambda name, value: written.append((name, value)),
            broadcaster=lambda: broadcasts.append(True),
            process_environment=environment,
        )
        configure_ollama_igpu(
            False,
            user_value_writer=lambda name, value: written.append((name, value)),
            broadcaster=lambda: broadcasts.append(True),
            process_environment=environment,
        )

        self.assertEqual(
            written,
            [
                (OLLAMA_IGPU_ENVIRONMENT, "1"),
                (OLLAMA_IGPU_ENVIRONMENT, None),
            ],
        )
        self.assertEqual(broadcasts, [True, True])
        self.assertNotIn(OLLAMA_IGPU_ENVIRONMENT, environment)


if __name__ == "__main__":
    unittest.main()
