"""Summarize real Declarator runs and enforce the recorded release gates.

Usage: python declarator_results.py BASELINE_DIR OPTIMIZED_DIR SERVER_PID_BEFORE SERVER_PID_AFTER OUTPUT
Inputs contain synthetic, non-sensitive metrics only. No application credentials are read.
"""

import json
import sys
from pathlib import Path


def seconds(value: str) -> float:
    total = 0.0
    for part in value.split(":"):
        total = total * 60 + float(part)
    return total


def load(folder: str):
    root = Path(folder)
    return json.loads((root / "result.json").read_text()), [
        json.loads(line) for line in (root / "checkpoints.jsonl").read_text().splitlines()
    ]


def process(sample, pid):
    return next(row for row in sample["processes"] if row["pid"] == pid)


def summarize(rows, end, pid):
    begin = rows[0]
    elapsed = end["elapsedSeconds"] - begin["elapsedSeconds"]
    api_before, api_after = process(begin, pid), process(end, pid)
    metrics = end["metrics"]
    return {
        "elapsedSeconds": end["elapsedSeconds"],
        "settledFights": end["cycles"],
        "acceptedPlayerBets": end["bets"],
        "webSocketFrames": end["frames"],
        "activeNativeSockets": end["wsActive"],
        "plannedReconnects": end["reconnects"],
        "apiCpuCorePercent": 100 * (seconds(api_after["cpuTime"]) - seconds(api_before["cpuTime"])) / elapsed,
        "apiRssMiB": api_after["rssKiB"] / 1024,
        "deskMainThreadCorePercent": 100 * (metrics["TaskDuration"] - begin["metrics"]["TaskDuration"]) / elapsed,
        "deskScriptSeconds": metrics["ScriptDuration"] - begin["metrics"]["ScriptDuration"],
        "deskHeapMiBAfterGC": metrics["JSHeapUsedSize"] / 1024**2,
        "domNodes": metrics["Nodes"],
        "eventListeners": metrics["JSEventListeners"],
        "detachedScriptStates": metrics["DetachedScriptStates"],
        "longTasks": end["longTasks"],
        "uiActionMilliseconds": end["actions"],
        "apiMilliseconds": end["latency"],
        "balanced": end["reconciliation"]["balanced"],
        "errors": end["errors"],
    }


def main(baseline: str, optimized: str, before_pid: str, after_pid: str, output: str):
    before_result, before_rows = load(baseline)
    after_result, after_rows = load(optimized)
    before_pid, after_pid = int(before_pid), int(after_pid)
    before = next((row for row in before_rows if row["elapsedSeconds"] >= 1800), before_rows[-1])
    after = next(row for row in after_rows if row["label"] == "gate-1800")
    final = after_rows[-1]
    server = [json.loads(line) for line in (Path(optimized) / "server.jsonl").read_text().splitlines()]
    warm = [row for row in after_rows if row["elapsedSeconds"] >= 1800]
    rss = [process(row, after_pid)["rssKiB"] / 1024 for row in warm]
    gates = {
        "baselineCompleted": before_result["passed"] and before_result["seconds"] >= 1800,
        "threeHoursCompleted": after_result["passed"] and after_result["seconds"] >= 10800,
        "allDurationCheckpoints": all(any(row["label"] == f"gate-{gate}" for row in after_rows) for gate in (1800, 3600, 10800)),
        "everyReconciliationBalanced": all(
            row["reconciliation"]["balanced"] and row["reconciliation"]["checks"]["pool_mismatches"] == 0 for row in after_rows
        ),
        "noBrowserOrTransportErrors": all(not row["errors"] for row in after_rows) and not after_result["errors"],
        "allNativeSocketsRemain": final["wsActive"] == 12,
        "everyNativeClientReceivedOrResyncedEverySettlement": all(
            row["received"] + row["recoveries"] >= after_result["cycles"] for row in after_result["settlementDelivery"]
        ),
        "criticalActionP95Under2Seconds": all(row["p95"] < 2000 for row in final["actions"].values()),
        "criticalActionMaxUnder5Seconds": all(row["max"] < 5000 for row in final["actions"].values()),
        "postWarmupApiRssGrowthUnder64MiB": max(rss) - rss[0] <= 64,
        "browserHeapAfterGcUnder50MiB": max(row["metrics"]["JSHeapUsedSize"] for row in warm) < 50 * 1024**2,
        "noDetachedScriptStates": all(row["metrics"]["DetachedScriptStates"] == 0 for row in after_rows),
        "boundedServerSubscriptions": max(row["tenantSubscribers"] for row in server) <= 17
        and server[-1]["tenantSubscribers"] == 16,
        "oneOrFewerActiveGhostJobs": max(row["activeGhostJobs"] for row in server) <= 1,
        "legacyRetryArraysEmpty": max(row["maxLegacyRetryArray"] for row in server) == 0,
    }
    summary = {
        "passed": all(gates.values()),
        "gates": gates,
        "matchedThirtyMinuteBefore": summarize(before_rows, before, before_pid),
        "matchedThirtyMinuteAfter": summarize(after_rows, after, after_pid),
        "threeHourAfter": summarize(after_rows, final, after_pid),
        "postWarmupApiRssMiB": {"at30Minutes": rss[0], "min": min(rss), "max": max(rss), "final": rss[-1]},
        "server": {
            "samples": len(server),
            "maxSubscriptions": max(row["tenantSubscribers"] for row in server),
            "finalSubscriptions": server[-1]["tenantSubscribers"],
            "maxActiveGhostJobs": max(row["activeGhostJobs"] for row in server),
            "maxDatabaseConnectionsIncludingObserver": max(sum(row["databaseConnections"].values()) for row in server),
        },
        "limitations": [
            "Baseline and optimized use separate real local services on the same host; "
            "only the first thirty minutes are a matched before/after comparison.",
            "API endpoint p95 uses the latest 2048 samples per endpoint; action p95 covers the full run.",
            "Manual ghost action timing measures browser click responsiveness; "
            "financial effects are checked through real books, receipts, settlement and reconciliation.",
            "DOM and listener counts include growing accessible winning-history beads; "
            "they are not required to remain constant as new fights are added.",
        ],
    }
    Path(output).write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"passed": summary["passed"], "gates": gates}, indent=2))
    if not summary["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main(*sys.argv[1:])
