#!/usr/bin/env python3
"""Read Gemini quota time series up to the explicit Monitoring auth boundary."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os

from src.dev_agent.resources.google_monitoring_quota import (
    GoogleMonitoringQuotaClient,
    MonitoringAuthRequired,
    MonitoringQuotaError,
    MONITORING_PERMISSION,
    MONITORING_READ_SCOPE,
    MONITORING_TOKEN_ENV,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True, help="Google Cloud project id or number that owns the Gemini quota")
    parser.add_argument("--model", required=True, help="Exact Gemini model label to query, for example gemini-3.8-flash")
    parser.add_argument("--window-seconds", type=int, default=600, help="Bounded observation lookback window (default: 600)")
    parser.add_argument("--token-env", default=MONITORING_TOKEN_ENV, help="Environment variable containing an ephemeral OAuth access token")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.window_seconds <= 0 or args.window_seconds > 3600:
        raise SystemExit("--window-seconds must be between 1 and 3600")
    end = datetime.now(timezone.utc)
    start = end - timedelta(seconds=args.window_seconds)
    client = GoogleMonitoringQuotaClient(
        project_id=args.project_id,
        model_id=args.model,
        access_token=os.environ.get(args.token_env),
        token_env=args.token_env,
    )
    try:
        snapshot = client.read_free_tier_snapshot(start_time=start, end_time=end)
    except MonitoringAuthRequired as exc:
        remote_rejection = exc.network_called
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "ACCESS_REQUIRED" if remote_rejection else "AUTH_REQUIRED",
                    "network_called": exc.network_called,
                    "http_status": exc.http_status,
                    "project_id": args.project_id,
                    "model_id": args.model,
                    "required_permission": MONITORING_PERMISSION,
                    "required_oauth_scope": MONITORING_READ_SCOPE,
                    "token_env": args.token_env,
                    "next_step": (
                        "Verify that the supplied OAuth token has read-only Monitoring authority for this project, then rerun this command."
                        if remote_rejection
                        else "Provide an ephemeral OAuth access token with read-only Monitoring authority, then rerun this command."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 3 if remote_rejection else 2
    except MonitoringQuotaError as exc:
        print(json.dumps({"schema_version": 1, "status": "OBSERVATION_FAILED", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"status": "OBSERVED_RAW", **snapshot}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
