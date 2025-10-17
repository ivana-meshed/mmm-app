#!/usr/bin/env python3
"""
Test script to validate optimization implementations
"""
import json
import os
import subprocess
import time

import pandas as pd
import pyarrow.parquet as pq
import requests


def test_resource_scaling():
    """Test that the container is using upgraded resources"""
    print("🧪 Testing Resource Scaling...")

    try:
        import psutil

        cpu_count = psutil.cpu_count()
        memory = psutil.virtual_memory()

        print(f"✅ CPU cores available: {cpu_count}")
        print(f"✅ Total memory: {memory.total // 1024**3} GB")

        # Verify environment variables are set
        r_cores = os.getenv("R_MAX_CORES", "Not set")
        omp_threads = os.getenv("OMP_NUM_THREADS", "Not set")

        print(f"✅ R_MAX_CORES: {r_cores}")
        print(f"✅ OMP_NUM_THREADS: {omp_threads}")

        if cpu_count >= 8 and memory.total >= 30 * 1024**3:  # 30GB+
            print("✅ Resource scaling test PASSED")
            return True
        else:
            print("❌ Resource scaling test FAILED - insufficient resources")
            return False

    except Exception as e:
        print(f"❌ Resource scaling test ERROR: {e}")
        return False


def test_parquet_performance():
    """Test Parquet vs CSV performance"""
    print("\n🧪 Testing Parquet Performance...")

    try:
        # Create test dataset
        test_data = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=10000, freq="D"),
                "cost_column_1": np.random.rand(10000) * 1000,
                "cost_column_2": np.random.rand(10000) * 500,
                "impressions": np.random.randint(0, 100000, 10000),
                "category": np.random.choice(["A", "B", "C", "D"], 10000),
            }
        )

        # Test CSV writing/reading
        csv_start = time.time()
        test_data.to_csv("/tmp/test.csv", index=False)
        csv_write_time = time.time() - csv_start

        csv_start = time.time()
        csv_loaded = pd.read_csv("/tmp/test.csv")
        csv_read_time = time.time() - csv_start

        # Test Parquet writing/reading
        parquet_start = time.time()
        test_data.to_parquet("/tmp/test.parquet", compression="snappy")
        parquet_write_time = time.time() - parquet_start

        parquet_start = time.time()
        parquet_loaded = pd.read_parquet("/tmp/test.parquet")
        parquet_read_time = time.time() - parquet_start

        # Compare file sizes
        csv_size = os.path.getsize("/tmp/test.csv") / 1024**2  # MB
        parquet_size = os.path.getsize("/tmp/test.parquet") / 1024**2  # MB

        print(
            f"📊 CSV - Write: {csv_write_time:.3f}s, Read: {csv_read_time:.3f}s, Size: {csv_size:.1f}MB"
        )
        print(
            f"📊 Parquet - Write: {parquet_write_time:.3f}s, Read: {parquet_read_time:.3f}s, Size: {parquet_size:.1f}MB"
        )

        read_speedup = csv_read_time / parquet_read_time
        size_reduction = (1 - parquet_size / csv_size) * 100

        print(f"✅ Read speedup: {read_speedup:.1f}x")
        print(f"✅ Size reduction: {size_reduction:.1f}%")

        if read_speedup > 1.5 and size_reduction > 20:
            print("✅ Parquet performance test PASSED")
            return True
        else:
            print("❌ Parquet performance test FAILED")
            return False

    except Exception as e:
        print(f"❌ Parquet performance test ERROR: {e}")
        return False
