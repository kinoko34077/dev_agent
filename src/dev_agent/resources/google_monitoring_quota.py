"""Read-only Google Cloud Monitoring observations for Gemini API quota research.

This module deliberately stops short of producing a ResourceRouter quota
observation.  Google publishes authoritative limit and usage time series for
Gemini API quota, but usage is a DELTA metric and the mapping from
project/model/limit_name/window to dev_agent's quota domains must be accepted
separately before any derived remaining headroom can affect routing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request

from ..providers.openai_compatible.http import REDIRECT_STATUS_CODES, _read_bounded, urlopen_no_redirect


MONITORING_TOKEN_ENV = "DEV_AGENT_GCP_MONITORING_ACCESS_TOKEN"
MONITORING_READ_SCOPE = "https://www.googleapis.com/auth/monitoring.read"
MONITORING_PERMISSION = "monitoring.timeSeries.list"
MONITORING_BASE_URL = "https://monitoring.googleapis.com/v3"

FREE_TIER_REQUEST_LIMIT = "generativelanguage.googleapis.com/quota/generate_content_free_tier_requests/limit"
FREE_TIER_REQUEST_USAGE = "generativelanguage.googleapis.com/quota/generate_content_free_tier_requests/usage"
FREE_TIER_INPUT_TOKEN_LIMIT = "generativelanguage.googleapis.com/quota/generate_content_free_tier_input_token_count/limit"
FREE_TIER_INPUT_TOKEN_USAGE = "generativelanguage.googleapis.com/quota/generate_content_free_tier_input_token_count/usage"
FREE_TIER_METRICS = (
    FREE_TIER_REQUEST_LIMIT,
    FREE_TIER_REQUEST_USAGE,
    FREE_TIER_INPUT_TOKEN_LIMIT,
    FREE_TIER_INPUT_TOKEN_USAGE,
)


class MonitoringQuotaError(RuntimeError):
    """The read-only Monitoring quota observation could not be trusted."""


class MonitoringAuthRequired(MonitoringQuotaError):
    """The caller must supply read-only Monitoring authorization."""

    permission = MONITORING_PERMISSION
    scope = MONITORING_READ_SCOPE
    token_env = MONITORING_TOKEN_ENV

    def __init__(self) -> None:
        # Do not include the token environment name in the human-readable
        # message.  Structured callers can read token_env explicitly.
        super().__init__("Google Cloud Monitoring read authorization is required")


def _require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _utc_rfc3339(value: datetime, *, name: str) -> str:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _escape_filter_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _point_value(raw: Any) -> int | float | bool | str | None:
    if not isinstance(raw, Mapping):
        return None
    for key in ("int64Value", "doubleValue", "boolValue", "stringValue"):
        if key not in raw:
            continue
        value = raw[key]
        if key == "int64Value":
            if isinstance(value, bool):
                return None
            if isinstance(value, int):
                return value
            if isinstance(value, str):
                try:
                    return int(value)
                except ValueError:
                    return None
            return None
        if key == "doubleValue":
            if isinstance(value, bool):
                return None
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                try:
                    return float(value)
                except ValueError:
                    return None
            return None
        if key == "boolValue":
            return value if isinstance(value, bool) else None
        return value if isinstance(value, str) else None
    return None


def _normalize_series(document: Mapping[str, Any], *, expected_metric_type: str) -> list[dict[str, Any]]:
    raw_series = document.get("timeSeries", [])
    if not isinstance(raw_series, list):
        raise MonitoringQuotaError("Monitoring response timeSeries must be an array")
    normalized: list[dict[str, Any]] = []
    for raw in raw_series:
        if not isinstance(raw, Mapping):
            raise MonitoringQuotaError("Monitoring response contains a malformed time series")
        metric = raw.get("metric")
        resource = raw.get("resource")
        points = raw.get("points", [])
        if not isinstance(metric, Mapping) or not isinstance(points, list):
            raise MonitoringQuotaError("Monitoring time series is missing metric or points")
        metric_type = metric.get("type")
        if metric_type != expected_metric_type:
            raise MonitoringQuotaError("Monitoring response returned an unexpected metric type")
        metric_labels = metric.get("labels", {})
        resource_labels = resource.get("labels", {}) if isinstance(resource, Mapping) else {}
        if not isinstance(metric_labels, Mapping) or not isinstance(resource_labels, Mapping):
            raise MonitoringQuotaError("Monitoring labels must be objects")
        normalized_points: list[dict[str, Any]] = []
        for point in points:
            if not isinstance(point, Mapping):
                raise MonitoringQuotaError("Monitoring time series contains a malformed point")
            interval = point.get("interval", {})
            if not isinstance(interval, Mapping):
                raise MonitoringQuotaError("Monitoring point interval must be an object")
            value = _point_value(point.get("value"))
            if value is None:
                raise MonitoringQuotaError("Monitoring point has no supported scalar value")
            normalized_points.append(
                {
                    "start_time": interval.get("startTime") if isinstance(interval.get("startTime"), str) else None,
                    "end_time": interval.get("endTime") if isinstance(interval.get("endTime"), str) else None,
                    "value": value,
                }
            )
        normalized.append(
            {
                "metric_type": metric_type,
                "metric_labels": {str(key): value for key, value in metric_labels.items() if isinstance(value, (str, int, float, bool))},
                "resource_type": resource.get("type") if isinstance(resource, Mapping) and isinstance(resource.get("type"), str) else None,
                "resource_labels": {str(key): value for key, value in resource_labels.items() if isinstance(value, (str, int, float, bool))},
                "points": normalized_points,
            }
        )
    return normalized


class GoogleMonitoringQuotaClient:
    """Fetch bounded first-party Gemini quota time series without mutation."""

    def __init__(
        self,
        *,
        project_id: str,
        model_id: str,
        access_token: str | None = None,
        token_env: str = MONITORING_TOKEN_ENV,
        base_url: str = MONITORING_BASE_URL,
        timeout_seconds: float = 15.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.project_id = _require_text(project_id, name="project_id")
        self.model_id = _require_text(model_id, name="model_id")
        if not isinstance(token_env, str) or not token_env.strip():
            raise ValueError("token_env must be a non-empty string")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.access_token = access_token
        self.token_env = token_env.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self._opener = opener or urlopen_no_redirect

    def _token(self) -> str:
        token = self.access_token or os.environ.get(self.token_env)
        if not isinstance(token, str) or not token.strip():
            raise MonitoringAuthRequired()
        return token.strip()

    def _url(self, metric_type: str, *, start_time: datetime, end_time: datetime) -> str:
        metric = _require_text(metric_type, name="metric_type")
        start = _utc_rfc3339(start_time, name="start_time")
        end = _utc_rfc3339(end_time, name="end_time")
        if end_time <= start_time:
            raise ValueError("end_time must be after start_time")
        metric_filter = (
            f'metric.type = "{_escape_filter_string(metric)}" AND '
            f'metric.labels.model = "{_escape_filter_string(self.model_id)}"'
        )
        query = urlencode(
            {
                "filter": metric_filter,
                "interval.startTime": start,
                "interval.endTime": end,
                "view": "FULL",
                "pageSize": "100",
            }
        )
        project = quote(self.project_id, safe="-._~")
        return f"{self.base_url}/projects/{project}/timeSeries?{query}"

    def read_metric(self, metric_type: str, *, start_time: datetime, end_time: datetime) -> list[dict[str, Any]]:
        token = self._token()  # Fail before request construction/network when auth is absent.
        url = self._url(metric_type, start_time=start_time, end_time=end_time)
        request = Request(
            url,
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
            method="GET",
        )
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                raw = json.loads(_read_bounded(response).decode("utf-8"))
        except HTTPError as exc:
            if exc.code in REDIRECT_STATUS_CODES:
                raise MonitoringQuotaError(f"Monitoring endpoint attempted HTTP {exc.code} redirect") from exc
            if exc.code in {401, 403}:
                raise MonitoringAuthRequired() from exc
            raise MonitoringQuotaError(f"Monitoring request failed with HTTP {exc.code}") from exc
        except (URLError, OSError) as exc:
            raise MonitoringQuotaError("Monitoring transport failed") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MonitoringQuotaError("Monitoring response decode failed") from exc
        if not isinstance(raw, Mapping):
            raise MonitoringQuotaError("Monitoring response must be an object")
        return _normalize_series(raw, expected_metric_type=metric_type)

    def read_free_tier_snapshot(self, *, start_time: datetime, end_time: datetime) -> dict[str, Any]:
        metrics = {
            metric_type: self.read_metric(metric_type, start_time=start_time, end_time=end_time)
            for metric_type in FREE_TIER_METRICS
        }
        return {
            "schema_version": 1,
            "evidence_type": "gemini_google_monitoring_quota_timeseries",
            "authority": "google-cloud-monitoring",
            "project_id": self.project_id,
            "model_id": self.model_id,
            "observed_interval": {
                "start": _utc_rfc3339(start_time, name="start_time"),
                "end": _utc_rfc3339(end_time, name="end_time"),
            },
            "metrics": metrics,
            "admission_ready": False,
            "interpretation": (
                "Authoritative first-party limit/usage time series only. No remaining quota is derived, "
                "and this document is not a ResourceRouter quota observation until quota-domain, window, "
                "freshness, reset, and missing-series semantics are separately accepted."
            ),
        }


__all__ = [
    "FREE_TIER_INPUT_TOKEN_LIMIT",
    "FREE_TIER_INPUT_TOKEN_USAGE",
    "FREE_TIER_METRICS",
    "FREE_TIER_REQUEST_LIMIT",
    "FREE_TIER_REQUEST_USAGE",
    "GoogleMonitoringQuotaClient",
    "MonitoringAuthRequired",
    "MonitoringQuotaError",
    "MONITORING_PERMISSION",
    "MONITORING_READ_SCOPE",
    "MONITORING_TOKEN_ENV",
]
