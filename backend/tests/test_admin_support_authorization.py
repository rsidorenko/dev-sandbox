from app.admin_support.authorization import AllowlistAdm01Authorization, AllowlistAdm02Authorization
from app.admin_support.contracts import AdminActorRef
from app.shared.test_helpers import run_async as _run


def test_allowlisted_principal_allowed() -> None:
    auth = AllowlistAdm01Authorization(["adm-a", "adm-b"])

    async def main() -> None:
        ok = await auth.check_adm01_lookup_allowed(
            AdminActorRef(internal_admin_principal_id="adm-a"),
            correlation_id="c1",
        )
        assert ok is True

    _run(main())


def test_unknown_principal_denied() -> None:
    auth = AllowlistAdm01Authorization(["adm-a"])

    async def main() -> None:
        ok = await auth.check_adm01_lookup_allowed(
            AdminActorRef(internal_admin_principal_id="other"),
            correlation_id="c1",
        )
        assert ok is False

    _run(main())


def test_empty_allowlist_denies_everyone() -> None:
    auth = AllowlistAdm01Authorization([])

    async def main() -> None:
        ok = await auth.check_adm01_lookup_allowed(
            AdminActorRef(internal_admin_principal_id="adm-a"),
            correlation_id="c1",
        )
        assert ok is False

    _run(main())


def test_exact_match_no_case_fold_or_trim() -> None:
    auth = AllowlistAdm01Authorization(["Adm-1"])

    async def main() -> None:
        assert (
            await auth.check_adm01_lookup_allowed(
                AdminActorRef(internal_admin_principal_id="adm-1"),
                correlation_id="c1",
            )
            is False
        )
        assert (
            await auth.check_adm01_lookup_allowed(
                AdminActorRef(internal_admin_principal_id=" Adm-1 "),
                correlation_id="c1",
            )
            is False
        )

    _run(main())


def test_adm02_allowlisted_principal_allowed() -> None:
    auth = AllowlistAdm02Authorization(["adm-a", "adm-b"])

    async def main() -> None:
        ok = await auth.check_adm02_diagnostics_allowed(
            AdminActorRef(internal_admin_principal_id="adm-a"),
            correlation_id="c1",
        )
        assert ok is True

    _run(main())


def test_adm02_unknown_principal_denied() -> None:
    auth = AllowlistAdm02Authorization(["adm-a"])

    async def main() -> None:
        ok = await auth.check_adm02_diagnostics_allowed(
            AdminActorRef(internal_admin_principal_id="other"),
            correlation_id="c1",
        )
        assert ok is False

    _run(main())


def test_adm02_empty_allowlist_denies_everyone() -> None:
    auth = AllowlistAdm02Authorization([])

    async def main() -> None:
        ok = await auth.check_adm02_diagnostics_allowed(
            AdminActorRef(internal_admin_principal_id="adm-a"),
            correlation_id="c1",
        )
        assert ok is False

    _run(main())


def test_adm02_exact_match_no_case_fold_or_trim() -> None:
    auth = AllowlistAdm02Authorization(["Adm-2"])

    async def main() -> None:
        assert (
            await auth.check_adm02_diagnostics_allowed(
                AdminActorRef(internal_admin_principal_id="adm-2"),
                correlation_id="c1",
            )
            is False
        )
        assert (
            await auth.check_adm02_diagnostics_allowed(
                AdminActorRef(internal_admin_principal_id=" Adm-2 "),
                correlation_id="c1",
            )
            is False
        )

    _run(main())


def test_adm01_and_adm02_allowlist_classes_distinct_surfaces() -> None:
    assert hasattr(AllowlistAdm01Authorization, "check_adm01_lookup_allowed")
    assert not hasattr(AllowlistAdm01Authorization, "check_adm02_diagnostics_allowed")
    assert hasattr(AllowlistAdm02Authorization, "check_adm02_diagnostics_allowed")
    assert not hasattr(AllowlistAdm02Authorization, "check_adm01_lookup_allowed")
