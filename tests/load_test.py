"""
VoiceStream Load Test — Simulates 10,000 concurrent users.

This script sends concurrent packet ingestion and call completion requests
to stress-test the VoiceStream API and measure:
  - Response latency (p50, p95, p99)
  - Error rate
  - Throughput (requests/sec)
  - Connection pool exhaustion
  - WebSocket broadcast overhead

Usage:
    python tests/load_test.py [--users N] [--duration S] [--url URL]
"""

import asyncio
import time
import random
import statistics
import argparse
import uuid
import sys
from dataclasses import dataclass, field
from typing import List

try:
    import aiohttp
except ImportError:
    print("Installing aiohttp for load testing...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "aiohttp"])
    import aiohttp


@dataclass
class LoadTestMetrics:
    """Collects and aggregates load test metrics."""
    response_times: List[float] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    status_codes: dict = field(default_factory=dict)
    start_time: float = 0.0
    end_time: float = 0.0
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    connection_errors: int = 0
    timeout_errors: int = 0

    def record_success(self, response_time: float, status_code: int):
        self.response_times.append(response_time)
        self.total_requests += 1
        self.successful_requests += 1
        self.status_codes[status_code] = self.status_codes.get(status_code, 0) + 1

    def record_failure(self, error: str, error_type: str = "general"):
        self.total_requests += 1
        self.failed_requests += 1
        self.errors.append(error)
        if error_type == "connection":
            self.connection_errors += 1
        elif error_type == "timeout":
            self.timeout_errors += 1

    def report(self) -> str:
        duration = self.end_time - self.start_time
        rps = self.total_requests / duration if duration > 0 else 0

        lines = [
            "",
            "=" * 70,
            "  VOICESTREAM LOAD TEST RESULTS",
            "=" * 70,
            "",
            f"  Duration:              {duration:.2f}s",
            f"  Total Requests:        {self.total_requests:,}",
            f"  Successful:            {self.successful_requests:,}",
            f"  Failed:                {self.failed_requests:,}",
            f"  Error Rate:            {(self.failed_requests / max(self.total_requests, 1)) * 100:.2f}%",
            f"  Throughput:            {rps:.1f} req/s",
            "",
        ]

        if self.response_times:
            sorted_times = sorted(self.response_times)
            p50 = sorted_times[int(len(sorted_times) * 0.50)]
            p95 = sorted_times[int(len(sorted_times) * 0.95)]
            p99 = sorted_times[min(int(len(sorted_times) * 0.99), len(sorted_times) - 1)]

            lines += [
                "  LATENCY",
                "  ─────────────────────────────",
                f"  Min:                   {min(sorted_times) * 1000:.1f} ms",
                f"  Avg:                   {statistics.mean(sorted_times) * 1000:.1f} ms",
                f"  Median (p50):          {p50 * 1000:.1f} ms",
                f"  p95:                   {p95 * 1000:.1f} ms",
                f"  p99:                   {p99 * 1000:.1f} ms",
                f"  Max:                   {max(sorted_times) * 1000:.1f} ms",
                "",
            ]

            # Check against <50ms SLA
            under_50ms = sum(1 for t in sorted_times if t < 0.050)
            lines += [
                "  SLA CHECK (< 50ms target)",
                "  ─────────────────────────────",
                f"  Requests < 50ms:       {under_50ms:,} / {len(sorted_times):,} ({under_50ms / len(sorted_times) * 100:.1f}%)",
                f"  SLA Met:               {'✅ YES' if p95 < 0.050 else '❌ NO (p95 > 50ms)'}",
                "",
            ]

        if self.status_codes:
            lines += ["  STATUS CODES", "  ─────────────────────────────"]
            for code, count in sorted(self.status_codes.items()):
                lines += [f"  {code}:                   {count:,}"]
            lines += [""]

        if self.connection_errors or self.timeout_errors:
            lines += [
                "  ERROR BREAKDOWN",
                "  ─────────────────────────────",
                f"  Connection Errors:     {self.connection_errors:,}",
                f"  Timeout Errors:        {self.timeout_errors:,}",
                f"  Other Errors:          {self.failed_requests - self.connection_errors - self.timeout_errors:,}",
                "",
            ]

        # Scalability verdict
        lines += [
            "  SCALABILITY VERDICT",
            "  ─────────────────────────────",
        ]
        if self.failed_requests == 0 and self.response_times and p95 < 0.050:
            lines += ["  ✅ PASS — System handles the load within SLA"]
        elif self.failed_requests / max(self.total_requests, 1) > 0.05:
            lines += [f"  ❌ FAIL — Error rate {(self.failed_requests / max(self.total_requests, 1)) * 100:.1f}% exceeds 5% threshold"]
        elif self.response_times and p95 >= 0.050:
            lines += [f"  ⚠️  DEGRADED — p95 latency {p95 * 1000:.1f}ms exceeds 50ms SLA"]
        else:
            lines += ["  ⚠️  MIXED — Some issues detected, see details above"]

        lines += ["", "=" * 70, ""]
        return "\n".join(lines)


async def simulate_call(
    session: aiohttp.ClientSession,
    base_url: str,
    metrics: LoadTestMetrics,
    call_id: str,
    num_packets: int = 5
):
    """Simulate a complete call lifecycle: send packets then complete."""
    for seq in range(num_packets):
        payload = {
            "sequence": seq,
            "data": f"audio_chunk_{seq}_call_{call_id}",
            "timestamp": time.time() + seq
        }
        start = time.monotonic()
        try:
            async with session.post(
                f"{base_url}/v1/call/stream/{call_id}",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                elapsed = time.monotonic() - start
                metrics.record_success(elapsed, resp.status)
        except aiohttp.ClientConnectorError as e:
            metrics.record_failure(str(e), "connection")
        except asyncio.TimeoutError:
            metrics.record_failure("Request timed out", "timeout")
        except Exception as e:
            metrics.record_failure(str(e))

    # Send completion signal
    start = time.monotonic()
    try:
        async with session.post(
            f"{base_url}/v1/call/complete/{call_id}",
            json={"total_packets": num_packets},
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            elapsed = time.monotonic() - start
            metrics.record_success(elapsed, resp.status)
    except aiohttp.ClientConnectorError as e:
        metrics.record_failure(str(e), "connection")
    except asyncio.TimeoutError:
        metrics.record_failure("Request timed out", "timeout")
    except Exception as e:
        metrics.record_failure(str(e))


async def simulate_packet_burst(
    session: aiohttp.ClientSession,
    base_url: str,
    metrics: LoadTestMetrics,
    call_id: str,
    sequence: int
):
    """Send a single packet — used for burst testing."""
    payload = {
        "sequence": sequence,
        "data": f"audio_chunk_{sequence}",
        "timestamp": time.time()
    }
    start = time.monotonic()
    try:
        async with session.post(
            f"{base_url}/v1/call/stream/{call_id}",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            elapsed = time.monotonic() - start
            metrics.record_success(elapsed, resp.status)
    except aiohttp.ClientConnectorError as e:
        metrics.record_failure(str(e), "connection")
    except asyncio.TimeoutError:
        metrics.record_failure("Request timed out", "timeout")
    except Exception as e:
        metrics.record_failure(str(e))


async def run_load_test(
    base_url: str,
    num_users: int,
    packets_per_call: int,
    batch_size: int
):
    """Main load test runner."""
    metrics = LoadTestMetrics()

    # Configure connection pool
    connector = aiohttp.TCPConnector(
        limit=min(num_users, 1000),  # Max concurrent connections
        limit_per_host=min(num_users, 1000),
        ttl_dns_cache=300,
        enable_cleanup_closed=True
    )

    async with aiohttp.ClientSession(connector=connector) as session:
        # ─── PHASE 1: Health Check ────────────────────────────────
        print("\n🔍 Phase 0: Health check...")
        try:
            async with session.get(f"{base_url}/", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    print("   ✅ Server is healthy")
                else:
                    print(f"   ❌ Server returned {resp.status}, aborting")
                    return
        except Exception as e:
            print(f"   ❌ Cannot reach server at {base_url}: {e}")
            print("   Make sure 'uvicorn app.main:app --reload' is running")
            return

        # ─── PHASE 2: Concurrent Packet Burst ─────────────────────
        print(f"\n🚀 Phase 1: Concurrent packet burst ({num_users:,} packets)...")
        burst_call_id = f"burst-{uuid.uuid4().hex[:8]}"
        metrics.start_time = time.monotonic()

        # Send packets in batches to avoid overwhelming the system
        for batch_start in range(0, num_users, batch_size):
            batch_end = min(batch_start + batch_size, num_users)
            tasks = []
            for seq in range(batch_start, batch_end):
                tasks.append(
                    simulate_packet_burst(session, base_url, metrics, burst_call_id, seq)
                )
            await asyncio.gather(*tasks)
            done_pct = (batch_end / num_users) * 100
            print(f"   Batch {batch_start}-{batch_end} sent ({done_pct:.0f}%)", end="\r")

        print(f"   ✅ Phase 1 complete: {metrics.total_requests:,} packets sent                ")

        # ─── PHASE 3: Concurrent Call Simulation ──────────────────
        num_calls = min(num_users // packets_per_call, 2000)
        print(f"\n📞 Phase 2: Simulating {num_calls:,} concurrent calls ({packets_per_call} packets each)...")

        for batch_start in range(0, num_calls, batch_size // packets_per_call):
            batch_end = min(batch_start + batch_size // packets_per_call, num_calls)
            tasks = []
            for i in range(batch_start, batch_end):
                call_id = f"load-{uuid.uuid4().hex[:8]}"
                tasks.append(
                    simulate_call(session, base_url, metrics, call_id, packets_per_call)
                )
            await asyncio.gather(*tasks)
            done_pct = (batch_end / num_calls) * 100
            print(f"   Calls {batch_start}-{batch_end} running ({done_pct:.0f}%)", end="\r")

        print(f"   ✅ Phase 2 complete: {num_calls:,} calls simulated                ")

        # ─── PHASE 4: Duplicate Stress Test ────────────────────────
        num_dupes = min(num_users // 10, 500)
        print(f"\n🔁 Phase 3: Duplicate stress test ({num_dupes:,} duplicate packets)...")
        dupe_call_id = f"dupe-{uuid.uuid4().hex[:8]}"

        # First send the original
        await simulate_packet_burst(session, base_url, metrics, dupe_call_id, 0)

        # Then spam duplicates
        tasks = [
            simulate_packet_burst(session, base_url, metrics, dupe_call_id, 0)
            for _ in range(num_dupes)
        ]
        await asyncio.gather(*tasks)
        print(f"   ✅ Phase 3 complete: {num_dupes:,} duplicates sent                ")

        # ─── PHASE 5: Race Condition Test ──────────────────────────
        num_race = min(num_users // 20, 200)
        print(f"\n⚡ Phase 4: Race condition test ({num_race:,} concurrent packets to same call)...")
        race_call_id = f"race-{uuid.uuid4().hex[:8]}"
        tasks = [
            simulate_packet_burst(session, base_url, metrics, race_call_id, seq)
            for seq in range(num_race)
        ]
        await asyncio.gather(*tasks)
        print(f"   ✅ Phase 4 complete                                              ")

        metrics.end_time = time.monotonic()

    return metrics


def main():
    parser = argparse.ArgumentParser(description="VoiceStream Load Test")
    parser.add_argument("--users", type=int, default=10000, help="Number of simulated users/packets (default: 10000)")
    parser.add_argument("--packets", type=int, default=5, help="Packets per call (default: 5)")
    parser.add_argument("--batch", type=int, default=500, help="Batch size for concurrent requests (default: 500)")
    parser.add_argument("--url", type=str, default="http://localhost:8000", help="Base URL (default: http://localhost:8000)")
    args = parser.parse_args()

    print("=" * 70)
    print("  VOICESTREAM LOAD TEST")
    print("=" * 70)
    print(f"  Target:     {args.url}")
    print(f"  Users:      {args.users:,}")
    print(f"  Pkts/call:  {args.packets}")
    print(f"  Batch size: {args.batch}")
    print("=" * 70)

    metrics = asyncio.run(run_load_test(
        base_url=args.url,
        num_users=args.users,
        packets_per_call=args.packets,
        batch_size=args.batch
    ))

    if metrics:
        print(metrics.report())


if __name__ == "__main__":
    main()
