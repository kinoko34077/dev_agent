"""Resource control-plane boundaries with lazy compatibility exports.

Internal modules should import the leaf module that owns their dependency.
The package-level names remain available for public consumers without loading
the budget, ledger, router, and survival implementations together.
"""

from importlib import import_module


_LAZY_EXPORTS = {
    "BudgetExceeded": (".budget", "BudgetExceeded"),
    "BudgetAuthority": (".budget", "BudgetAuthority"),
    "BudgetGovernor": (".budget", "BudgetGovernor"),
    "BudgetPolicy": (".budget", "BudgetPolicy"),
    "BudgetReconciliationRequired": (".budget", "BudgetReconciliationRequired"),
    "BudgetReservation": (".budget", "BudgetReservation"),
    "MaintenanceActive": (".budget", "MaintenanceActive"),
    "ResourceUnavailable": (".budget", "ResourceUnavailable"),
    "UnknownPrice": (".budget", "UnknownPrice"),
    "DispatchDenied": (".control", "DispatchDenied"),
    "DispatchReservation": (".control", "DispatchReservation"),
    "ResourceControlPlane": (".control", "ResourceControlPlane"),
    "BudgetPeriod": (".ledger", "BudgetPeriod"),
    "MoneyAmount": (".ledger", "MoneyAmount"),
    "QuotaObservation": (".ledger", "QuotaObservation"),
    "ResourceLedger": (".ledger", "ResourceLedger"),
    "ResourcePrice": (".ledger", "ResourcePrice"),
    "ResourceSpec": (".ledger", "ResourceSpec"),
    "UnknownQuotaAdmission": (".ledger", "UnknownQuotaAdmission"),
    "NoRoute": (".router", "NoRoute"),
    "ResourceReadView": (".router", "ResourceReadView"),
    "ResourceRouter": (".router", "ResourceRouter"),
    "RouteRequest": (".router", "RouteRequest"),
    "RouteSelection": (".router", "RouteSelection"),
    "RoutingSnapshot": (".snapshot", "RoutingSnapshot"),
    "SurvivalGovernor": (".survival", "SurvivalGovernor"),
    "SurvivalMode": (".survival", "SurvivalMode"),
    "SurvivalSnapshot": (".survival", "SurvivalSnapshot"),
    "SurvivalState": (".survival", "SurvivalState"),
    "QuotaBlockDecision": (".quota_policy", "QuotaBlockDecision"),
    "classify_provider_error": (".quota_policy", "classify_provider_error"),
    "next_reset_at": (".quota_policy", "next_reset_at"),
    "provider_reset_window": (".quota_policy", "provider_reset_window"),
}


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


__all__ = list(_LAZY_EXPORTS)
