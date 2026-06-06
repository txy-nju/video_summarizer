import json
import os
import re
import statistics
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Any

ROOT = Path(__file__).resolve().parent.parent
PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")


def parse_pytest_summary(output: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "passed": None,
        "failed": 0,
        "skipped": 0,
        "error": 0,
        "xfailed": 0,
        "xpassed": 0,
    }

    summary_line = ""
    for line in output.splitlines()[::-1]:
        if " in " in line and ("passed" in line or "failed" in line or "error" in line or "skipped" in line):
            summary_line = line
            break

    if not summary_line:
        return result

    patterns = {
        "passed": r"(\d+)\s+passed",
        "failed": r"(\d+)\s+failed",
        "skipped": r"(\d+)\s+skipped",
        "error": r"(\d+)\s+error",
        "xfailed": r"(\d+)\s+xfailed",
        "xpassed": r"(\d+)\s+xpassed",
    }

    for key, pat in patterns.items():
        m = re.search(pat, summary_line)
        if m:
            result[key] = int(m.group(1))

    return result


def run_one(mode: str, round_no: int) -> Dict[str, Any]:
    env = os.environ.copy()
    env["CONCURRENCY_MODE"] = "send_api"
    env["ENABLE_METRICS_LOGGING"] = "true"
    env["METRICS_SAMPLE_RATE"] = "1.0"

    cmd = [PYTHON, "-m", "pytest", "-q"]
    started = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    parsed = parse_pytest_summary(combined)

    return {
        "mode": mode,
        "round": round_no,
        "exit_code": proc.returncode,
        "elapsed_ms": elapsed_ms,
        "summary": parsed,
    }


def aggregate(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    times = [r["elapsed_ms"] for r in records]
    failures = sum((r["summary"].get("failed") or 0) for r in records)
    errors = sum((r["summary"].get("error") or 0) for r in records)
    passes = [r["summary"].get("passed") for r in records if r["summary"].get("passed") is not None]

    return {
        "rounds": len(records),
        "all_exit_zero": all(r["exit_code"] == 0 for r in records),
        "total_failed": failures,
        "total_error": errors,
        "elapsed_ms_avg": round(statistics.mean(times), 2) if times else None,
        "elapsed_ms_median": round(statistics.median(times), 2) if times else None,
        "elapsed_ms_min": min(times) if times else None,
        "elapsed_ms_max": max(times) if times else None,
        "passed_per_round": passes,
    }


def main() -> None:
    all_records: List[Dict[str, Any]] = []

    for round_no in (1, 2, 3):
        all_records.append(run_one("send_api", round_no))

    report = {
        "records": all_records,
        "send_api": aggregate(all_records),
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
