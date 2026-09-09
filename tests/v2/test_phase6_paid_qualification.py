import pytest

from scripts.qualify_phase6_paid_provider import CONFIRMATION, PaidQualificationBlocked, require_billing_authorization


def test_paid_qualification_requires_explicit_billing_flag():
    with pytest.raises(PaidQualificationBlocked, match="allow-billing"):
        require_billing_authorization(allow_billing=False, confirmation=CONFIRMATION)


def test_paid_qualification_requires_exact_confirmation_phrase():
    with pytest.raises(PaidQualificationBlocked, match="confirm"):
        require_billing_authorization(allow_billing=True, confirmation="yes")
