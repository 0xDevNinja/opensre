"""Tests for app.services.eks.eks_client.EKSClient.

Focus on the credential-resolution path inside ``EKSClient._build``: stored
integration credentials must take priority over ``role_arn`` AssumeRole, so
``list_eks_clusters`` (the EKS connection-verification path) works for AWS
integrations configured with IAM user keys instead of an assumable role.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.eks import eks_client
from app.services.eks.eks_client import EKSClient


def _stub_boto3_clients(boto_client: MagicMock) -> tuple[MagicMock, MagicMock]:
    """Configure ``boto3.client`` to return distinct stubs for ``sts`` and ``eks``."""
    sts = MagicMock()
    sts.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "AKIAASSUMED",
            "SecretAccessKey": "assumed-secret",
            "SessionToken": "assumed-token",
        }
    }
    eks = MagicMock()

    def _route(name: str, *args: object, **kwargs: object) -> MagicMock:
        if name == "sts":
            return sts
        if name == "eks":
            return eks
        raise AssertionError(f"unexpected boto3.client({name!r}) call")

    boto_client.side_effect = _route
    return sts, eks


def test_eks_client_uses_stored_credentials_when_provided() -> None:
    """Stored credentials must skip the STS AssumeRole call entirely."""
    with patch.object(eks_client.boto3, "client") as boto_client:
        sts, eks = _stub_boto3_clients(boto_client)
        EKSClient(
            role_arn="arn:aws:iam::123:role/r",
            external_id="",
            region="us-east-2",
            credentials={
                "access_key_id": "AKIASTORED",
                "secret_access_key": "secret",
                "session_token": "",
            },
        )

    sts.assume_role.assert_not_called()
    # boto3.client('eks', ...) must receive the stored access key, not the
    # assumed-role one, with empty SessionToken coerced to None.
    eks_calls = [c for c in boto_client.call_args_list if c.args and c.args[0] == "eks"]
    assert len(eks_calls) == 1
    _, kwargs = eks_calls[0]
    assert kwargs["aws_access_key_id"] == "AKIASTORED"
    assert kwargs["aws_secret_access_key"] == "secret"
    assert kwargs["aws_session_token"] is None
    assert kwargs["region_name"] == "us-east-2"


def test_eks_client_falls_back_to_assume_role_when_no_credentials() -> None:
    with patch.object(eks_client.boto3, "client") as boto_client:
        sts, _ = _stub_boto3_clients(boto_client)
        EKSClient(
            role_arn="arn:aws:iam::123:role/r",
            external_id="ext",
            region="us-west-2",
        )

    sts.assume_role.assert_called_once_with(
        RoleArn="arn:aws:iam::123:role/r",
        RoleSessionName="TracerEKSInvestigation",
        ExternalId="ext",
    )


def test_eks_client_falls_back_to_assume_role_when_credentials_incomplete() -> None:
    """A credentials dict missing one of the IAM user keys must not block the
    AssumeRole fallback — partially configured integrations still work."""
    with patch.object(eks_client.boto3, "client") as boto_client:
        sts, _ = _stub_boto3_clients(boto_client)
        EKSClient(
            role_arn="arn:aws:iam::123:role/r",
            external_id="",
            region="us-east-1",
            credentials={"access_key_id": "AKIATEST"},  # missing secret
        )

    sts.assume_role.assert_called_once()


def test_eks_client_omits_external_id_when_empty() -> None:
    """Existing behaviour: an empty external_id must not be sent on AssumeRole."""
    with patch.object(eks_client.boto3, "client") as boto_client:
        sts, _ = _stub_boto3_clients(boto_client)
        EKSClient(role_arn="arn:aws:iam::123:role/r", external_id="", region="us-east-1")

    _, kwargs = sts.assume_role.call_args
    assert "ExternalId" not in kwargs
