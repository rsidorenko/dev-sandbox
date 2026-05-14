from app.admin_support.contracts import (
    InternalAdminPrincipalExtractionInput,
    InternalAdminPrincipalExtractionOutcome,
)
from app.admin_support.principal_extraction import DefaultInternalAdminPrincipalExtractor
from app.shared.test_helpers import run_async as _run


def test_default_extractor_success_trims_principal() -> None:
    extractor = DefaultInternalAdminPrincipalExtractor()

    async def main() -> None:
        result = await extractor.extract_trusted_internal_admin_principal(
            InternalAdminPrincipalExtractionInput(
                principal_id_candidate="  adm-1  ",
                trusted_source=True,
            ),
        )

        assert result.outcome is InternalAdminPrincipalExtractionOutcome.SUCCESS
        assert result.principal is not None
        assert result.principal.internal_admin_principal_id == "adm-1"

    _run(main())


def test_default_extractor_missing_principal() -> None:
    extractor = DefaultInternalAdminPrincipalExtractor()

    async def main() -> None:
        result = await extractor.extract_trusted_internal_admin_principal(
            InternalAdminPrincipalExtractionInput(
                principal_id_candidate=None,
                trusted_source=True,
            ),
        )

        assert result.outcome is InternalAdminPrincipalExtractionOutcome.MISSING_PRINCIPAL
        assert result.principal is None

    _run(main())


def test_default_extractor_malformed_blank_or_whitespace_principal() -> None:
    extractor = DefaultInternalAdminPrincipalExtractor()

    async def main() -> None:
        blank = await extractor.extract_trusted_internal_admin_principal(
            InternalAdminPrincipalExtractionInput(
                principal_id_candidate="",
                trusted_source=True,
            ),
        )
        whitespace = await extractor.extract_trusted_internal_admin_principal(
            InternalAdminPrincipalExtractionInput(
                principal_id_candidate="   ",
                trusted_source=True,
            ),
        )

        assert blank.outcome is InternalAdminPrincipalExtractionOutcome.MALFORMED_PRINCIPAL
        assert blank.principal is None
        assert whitespace.outcome is InternalAdminPrincipalExtractionOutcome.MALFORMED_PRINCIPAL
        assert whitespace.principal is None

    _run(main())


def test_default_extractor_untrusted_source_short_circuit() -> None:
    extractor = DefaultInternalAdminPrincipalExtractor()

    async def main() -> None:
        result = await extractor.extract_trusted_internal_admin_principal(
            InternalAdminPrincipalExtractionInput(
                principal_id_candidate="adm-1",
                trusted_source=False,
            ),
        )

        assert result.outcome is InternalAdminPrincipalExtractionOutcome.UNTRUSTED_SOURCE
        assert result.principal is None

    _run(main())
