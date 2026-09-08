"""Read-only validation and summarization of persisted JUnit XML reports."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class TestReport:
    tests: int
    failures: int
    errors: int
    skipped: int
    duration_seconds: float

    @property
    def passed(self) -> int:
        return self.tests - self.failures - self.errors - self.skipped

    def to_dict(self) -> dict[str, int | float]:
        return asdict(self) | {"passed": self.passed}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _nonnegative_int(value: str | None, name: str) -> int:
    if value is None:
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return parsed


def _nonnegative_float(value: str | None, name: str) -> float:
    if value is None:
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite non-negative number") from exc
    if parsed < 0 or not math.isfinite(parsed):
        raise ValueError(f"{name} must be a finite non-negative number")
    return parsed


def parse_junit_report(path: str | Path) -> TestReport:
    """Parse a JUnit XML file without importing the runtime or test code."""
    report_path = Path(path)
    try:
        root = ET.parse(report_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"cannot read JUnit report: {exc}") from exc

    if _local_name(root.tag) == "testsuite":
        suites = [root]
    else:
        suites = [item for item in root if _local_name(item.tag) == "testsuite"]
    if not suites:
        raise ValueError("JUnit report contains no testsuite")

    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    duration = 0.0
    for suite in suites:
        for name in totals:
            totals[name] += _nonnegative_int(suite.attrib.get(name), name)
        duration += _nonnegative_float(suite.attrib.get("time"), "time")
    if totals["failures"] + totals["errors"] + totals["skipped"] > totals["tests"]:
        raise ValueError("JUnit report result counts exceed total tests")
    return TestReport(**totals, duration_seconds=duration)


def validate_junit_report(path: str | Path) -> tuple[bool, str]:
    try:
        report = parse_junit_report(path)
    except (OSError, ValueError) as exc:
        return False, str(exc)
    return True, f"JUnit report is readable: {report.passed}/{report.tests} passed"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a persisted JUnit XML report")
    parser.add_argument("path", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        report = parse_junit_report(args.path)
    except (OSError, ValueError) as exc:
        print(f"TEST_REPORT_ERROR: {exc}")
        return 1
    if args.as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"PASS: {report.passed}/{report.tests} passed; failures={report.failures}; errors={report.errors}; skipped={report.skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
