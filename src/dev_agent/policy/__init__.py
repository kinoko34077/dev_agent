"""Default-deny policy and approval boundaries."""

from .approvals import ApprovalPolicy
from .permissions import PathPolicy

__all__ = ["ApprovalPolicy", "PathPolicy"]
