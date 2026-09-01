from __future__ import annotations

from pathlib import Path


def test_production_compose_passes_trial_channel_gate_settings_to_backend() -> None:
    """Явный environment[] не должен скрыть opt-in гейт от backend-контейнера."""
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.prod.yml"
    compose = compose_path.read_text(encoding="utf-8")

    assert "TRIAL_CHANNEL_SUBSCRIBE_ENABLE: ${TRIAL_CHANNEL_SUBSCRIBE_ENABLE:-}" in compose
    assert "TRIAL_CHANNEL_USERNAME: ${TRIAL_CHANNEL_USERNAME:-bravada_vpn}" in compose


def test_production_env_example_documents_trial_channel_gate_settings() -> None:
    env_example_path = Path(__file__).resolve().parents[2] / ".env.example"
    env_example = env_example_path.read_text(encoding="utf-8")

    assert "TRIAL_CHANNEL_SUBSCRIBE_ENABLE=" in env_example
    assert "TRIAL_CHANNEL_USERNAME=bravada_vpn" in env_example
