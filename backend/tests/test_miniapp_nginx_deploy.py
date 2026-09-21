"""Серверные vhost Mini App переживают замену каталога VPN-деплоя."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COPY_VHOSTS = ROOT / "scripts/ci/copy_miniapp_vhosts.sh"
MINIAPP_FILES = (
    "api.bravada-connect.ru.conf",
    "chat.bravada-connect.ru.conf",
    "bravada-gpt-root.conf",
)


def test_miniapp_vhosts_survive_artifact_swap_without_overwriting_repo_configs(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    current.mkdir()
    for name in MINIAPP_FILES:
        (current / name).write_text(f"server version: {name}", encoding="utf-8")
    (current / "default.conf").write_text("vpn config", encoding="utf-8")

    backup = tmp_path / "backup"
    subprocess.run(["bash", str(COPY_VHOSTS), str(current), str(backup)], check=True)

    release = tmp_path / "release"
    release.mkdir()
    (release / "default.conf").write_text("new vpn config", encoding="utf-8")
    (release / MINIAPP_FILES[0]).write_text("new tracked api config", encoding="utf-8")
    subprocess.run(["bash", str(COPY_VHOSTS), str(backup), str(release)], check=True)

    assert (release / "default.conf").read_text(encoding="utf-8") == "new vpn config"
    assert (release / MINIAPP_FILES[0]).read_text(encoding="utf-8") == "new tracked api config"
    for name in MINIAPP_FILES[1:]:
        assert (release / name).read_text(encoding="utf-8") == f"server version: {name}"
    assert sorted(path.name for path in backup.iterdir()) == sorted(MINIAPP_FILES)
