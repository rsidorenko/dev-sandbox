"""Umbrella safety contract for MVP release helper scripts."""

from __future__ import annotations

import ast
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = _BACKEND_DIR / "scripts"
_SCRIPT_RELATIVE_PATHS = (
    "run_mvp_release_readiness.py",
    "run_mvp_repo_release_health_check.py",
    "run_mvp_release_checklist.py",
    "run_mvp_release_preflight.py",
    "run_mvp_config_doctor.py",
    "print_mvp_release_handoff_summary.py",
)
_EXPECTED_STATUS_MARKERS = {
    "run_mvp_release_readiness.py": ("mvp_release_readiness: ok", "mvp_release_readiness: fail"),
    "run_mvp_repo_release_health_check.py": (
        "mvp_repo_release_health_check: ok",
        "mvp_repo_release_health_check: fail",
    ),
    "run_mvp_release_checklist.py": ("mvp_release_checklist: ok", "mvp_release_checklist: fail"),
    "run_mvp_release_preflight.py": ("mvp_release_preflight: ok", "mvp_release_preflight: fail"),
    "run_mvp_config_doctor.py": ("mvp_config_doctor: ok", "mvp_config_doctor: fail"),
    "print_mvp_release_handoff_summary.py": (
        "mvp_release_handoff_summary: ok",
        "mvp_release_handoff_summary: fail",
    ),
}
_FORBIDDEN_FRAGMENTS = (
    "DATABASE_URL=",
    "BOT_TOKEN=",
    "TELEGRAM_WEBHOOK_SECRET_TOKEN=",
    "ADM02_ENSURE_ACCESS_ENABLE=",
    "OPERATIONAL_RETENTION_DELETE_ENABLE=",
    "postgres://",
    "postgresql://",
    "Bearer ",
    "PRIVATE KEY",
    "BEGIN ",
    "token=",
    "vpn://",
    "provider_issuance_ref",
    "issue_idempotency_key",
    "schema_version",
    "customer_ref",
    "provider_ref",
    "checkout_attempt_id",
    "internal_user_id",
)

_SAFE_LITERAL_CONTAINER_MARKERS = ("FORBIDDEN", "MARKER", "REQUIRED")
_FORBIDDEN_NON_RUNTIME_ASSIGNMENT_FRAGMENTS = (
    "DATABASE_URL=",
    "BOT_TOKEN=",
    "TELEGRAM_WEBHOOK_SECRET_TOKEN=",
    "ADM02_ENSURE_ACCESS_ENABLE=",
    "OPERATIONAL_RETENTION_DELETE_ENABLE=",
)

_FORBIDDEN_EXECUTION_FRAGMENTS = (
    "pytest",
    "python scripts/run_mvp_release_preflight.py",
    "python scripts/run_mvp_config_doctor.py",
    "python scripts/run_mvp_release_checklist.py",
    "python scripts/print_mvp_release_handoff_summary.py",
    "python scripts/run_postgres_mvp_smoke_local.py",
    "docker compose",
    "docker-compose",
)


def _read_script(rel_path: str) -> str:
    return (_SCRIPTS_DIR / rel_path).read_text(encoding="utf-8")


def _build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parent_map: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_map[child] = parent
    return parent_map


def _is_safe_literal_container(node: ast.AST, parent_map: dict[ast.AST, ast.AST]) -> bool:
    current: ast.AST | None = node
    while current is not None:
        parent = parent_map.get(current)
        if isinstance(parent, ast.Assign):
            for target in parent.targets:
                if isinstance(target, ast.Name) and any(
                    marker in target.id for marker in _SAFE_LITERAL_CONTAINER_MARKERS
                ):
                    return True
        if isinstance(parent, ast.AnnAssign) and isinstance(parent.target, ast.Name):
            if any(marker in parent.target.id for marker in _SAFE_LITERAL_CONTAINER_MARKERS):
                return True
        current = parent
    return False


def _string_nodes_outside_safe_containers(body: str) -> list[ast.Constant]:
    tree = ast.parse(body)
    parent_map = _build_parent_map(tree)
    result: list[ast.Constant] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if not _is_safe_literal_container(node, parent_map):
                result.append(node)
    return result


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        root = _call_name(func.value)
        if root:
            return f"{root}.{func.attr}"
        return func.attr
    return ""


def _literal_fragments_from_expr(node: ast.AST) -> list[str]:
    fragments: list[str] = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        fragments.append(node.value)
    elif isinstance(node, ast.List | ast.Tuple | ast.Set):
        for elt in node.elts:
            fragments.extend(_literal_fragments_from_expr(elt))
    elif isinstance(node, ast.JoinedStr):
        fragments.extend(
            value.value  # type: ignore[union-attr]
            for value in node.values
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        )
    return fragments


def _runtime_emission_fragments(body: str) -> list[str]:
    tree = ast.parse(body)
    fragments: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func)
        is_runtime_call = name in {"print", "subprocess.run", "_run_stage", "os.system"}
        if not is_runtime_call:
            continue
        for arg in node.args:
            fragments.extend(_literal_fragments_from_expr(arg))
        for keyword in node.keywords:
            if keyword.arg in {"args", "command"}:
                fragments.extend(_literal_fragments_from_expr(keyword.value))
    return fragments


def test_release_helper_scripts_exist() -> None:
    for rel_path in _SCRIPT_RELATIVE_PATHS:
        assert (_SCRIPTS_DIR / rel_path).exists()


def test_release_helper_scripts_contain_expected_status_markers() -> None:
    for rel_path, markers in _EXPECTED_STATUS_MARKERS.items():
        body = _read_script(rel_path)
        assert markers[0] in body
        assert markers[1] in body


def test_release_helper_scripts_have_no_forbidden_secret_like_assignment_fragments() -> None:
    for rel_path in _SCRIPT_RELATIVE_PATHS:
        body = _read_script(rel_path)
        lowered_runtime_fragments = " ".join(_runtime_emission_fragments(body)).lower()
        non_safe_literal_blob = " ".join(node.value for node in _string_nodes_outside_safe_containers(body)).lower()
        for forbidden in _FORBIDDEN_FRAGMENTS:
            normalized = forbidden.lower()
            assert normalized not in lowered_runtime_fragments
        for forbidden in _FORBIDDEN_NON_RUNTIME_ASSIGNMENT_FRAGMENTS:
            assert forbidden.lower() not in non_safe_literal_blob


def test_release_helpers_do_not_invoke_docker_or_local_smoke_automatically() -> None:
    for rel_path in _SCRIPT_RELATIVE_PATHS:
        runtime_blob = " ".join(_runtime_emission_fragments(_read_script(rel_path))).lower()
        assert "python scripts/run_postgres_mvp_smoke_local.py" not in runtime_blob
        assert "docker compose" not in runtime_blob
        assert "docker-compose" not in runtime_blob


def test_readiness_delegation_scope_is_bounded() -> None:
    body = _read_script("run_mvp_release_readiness.py")
    assert "scripts/run_mvp_repo_release_health_check.py" in body
    assert "scripts/run_mvp_release_checklist.py" in body
    assert "scripts/run_mvp_release_preflight.py" in body
    assert "scripts/run_mvp_config_doctor.py" in body


def test_static_scripts_do_not_delegate_to_pytest_or_other_helper_runners() -> None:
    for rel_path in (
        "run_mvp_repo_release_health_check.py",
        "run_mvp_release_checklist.py",
        "print_mvp_release_handoff_summary.py",
    ):
        runtime_blob = " ".join(_runtime_emission_fragments(_read_script(rel_path))).lower()
        for forbidden in _FORBIDDEN_EXECUTION_FRAGMENTS:
            assert forbidden not in runtime_blob


def test_preflight_allows_targeted_pytest_invocation() -> None:
    lowered = _read_script("run_mvp_release_preflight.py").lower()
    assert "pytest" in lowered


def test_config_doctor_does_not_use_db_network_docker_or_subprocess_calls() -> None:
    lowered = _read_script("run_mvp_config_doctor.py").lower()
    assert "subprocess" not in lowered
    assert "socket." not in lowered
    assert "requests." not in lowered
    assert "urllib." not in lowered
    assert "httpx." not in lowered
    assert "docker" not in lowered
