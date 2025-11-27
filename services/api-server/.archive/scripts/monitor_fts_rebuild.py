#!/usr/bin/env python3
"""
Monitor FTS index rebuild progress
"""
import time
import requests
from requests.auth import HTTPBasicAuth

TARGET_COUNT = 60344
CHECK_INTERVAL = 15  # seconds

print("=" * 70)
print("FTS INDEX REBUILD PROGRESS MONITOR")
print("=" * 70)
print(f"Target: {TARGET_COUNT:,} documents")
print(f"Check interval: {CHECK_INTERVAL}s")
print("")

auth = HTTPBasicAuth("Administrator", "password123")
url = "http://localhost:8094/api/index/code_vector_index/count"

iteration = 1
while True:
    try:
        response = requests.get(url, auth=auth, timeout=10)
        data = response.json()
        count = data.get('count', 0)

        pct = (count * 100) // TARGET_COUNT if TARGET_COUNT > 0 else 0

        print(f"[{iteration:2d}] Count: {count:6,} / {TARGET_COUNT:,} ({pct:3d}%)")

        if count >= TARGET_COUNT:
            print("")
            print("✓ Index rebuild complete!")
            print(f"  Final count: {count:,}")
            print("=" * 70)
            break

        iteration += 1
        time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        print("\n\nMonitoring stopped by user")
        break
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(CHECK_INTERVAL)
