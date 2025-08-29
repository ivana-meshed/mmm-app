#!/usr/bin/env python3
"""
Test script to validate optimization implementations
"""
import time
import requests
import pandas as pd
import pyarrow.parquet as pq
import subprocess
import os
import json

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
        r_cores = os.getenv('R_MAX_CORES', 'Not set')
        omp_threads = os.getenv('OMP_NUM_THREADS', 'Not set') 
        
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
        test_data = pd.DataFrame({
            'date': pd.date_range('2024-01-01', periods=10000, freq='D'),
            'cost_column_1': np.random.rand(10000) * 1000,
            'cost_column_2': np.random.rand(10000) * 500, 
            'impressions': np.random.randint(0, 100000, 10000),
            'category': np.random.choice(['A', 'B', 'C', 'D'], 10000)
        })
        
        # Test CSV writing/reading
        csv_start = time.time()
        test_data.to_csv('/tmp/test.csv', index=False)
        csv_write_time = time.time() - csv_start
        
        csv_start = time.time() 
        csv_loaded = pd.read_csv('/tmp/test.csv')
        csv_read_time = time.time() - csv_start
        
        # Test Parquet writing/reading
        parquet_start = time.time()
        test_data.to_parquet('/tmp/test.parquet', compression='snappy')
        parquet_write_time = time.time() - parquet_start
        
        parquet_start = time.time()
        parquet_loaded = pd.read_parquet('/tmp/test.parquet') 
        parquet_read_time = time.time() - parquet_start
        
        # Compare file sizes
        csv_size = os.path.getsize('/tmp/test.csv') / 1024**2  # MB
        parquet_size = os.path.getsize('/tmp/test.parquet') / 1024**2  # MB
        
        print(f"📊 CSV - Write: {csv_write_time:.3f}s, Read: {csv_read_time:.3f}s, Size: {csv_size:.1f}MB")
        print(f"📊 Parquet - Write: {parquet_write_time:.3f}s, Read: {parquet_read_time:.3f}s, Size: {parquet_size:.1f}MB")
        
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

def test_container_warming():
    """Test container warming functionality"""
    print("\n🧪 Testing Container Warming...")
    
    try:
        # Test health endpoint
        response = requests.get('http://localhost:8080/health', timeout=10)
        
        if response.status_code == 200:
            print("✅ Health endpoint accessible")
            
            # Check if warming status file exists
            if os.path.exists('/tmp/warming_status.json'):
                with open('/tmp/warming_status.json', 'r') as f:
                    warming_status = json.load(f)
                
                print(f"✅ Warming status: {warming_status}")
                
                successful_components = sum(1 for status in warming_status['warming_status'].values() if status)
                total_components = len(warming_status['warming_status'])
                
                if successful_components >= 3:  # At least 3 of 4 components
                    print("✅ Container warming test PASSED")
                    return True
                else:
                    print("❌ Container warming test FAILED - insufficient components warmed")
                    return False
            else:
                print("⚠️ Warming status file not found - container may not be fully warmed")
                return False
        else:
            print(f"❌ Health endpoint returned status {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Container warming test ERROR: {e}")
        return False

def test_r_performance():
    """Test R environment performance"""
    print("\n🧪 Testing R Performance...")
    
    try:
        r_test_script = """
        # Test parallel processing
        library(parallel)
        library(future)
        
        # Check core configuration
        max_cores <- as.numeric(Sys.getenv("R_MAX_CORES", "1"))
        cat("R_MAX_CORES:", max_cores, "\\n")
        
        # Test parallel computation
        start_time <- Sys.time()
        plan(multisession, workers = max_cores)
        
        # Parallel computation test
        result <- future_lapply(1:1000, function(x) {
            sum(rnorm(1000))
        }, future.seed = TRUE)
        
        end_time <- Sys.time()
        elapsed <- as.numeric(end_time - start_time)
        
        cat("Parallel computation time:", elapsed, "seconds\\n")
        
        # Test arrow/parquet reading
        if (requireNamespace("arrow", quietly = TRUE)) {
            cat("Arrow package available\\n")
        } else {
            cat("Arrow package NOT available\\n")
        }
        
        cat("R performance test completed\\n")
        """
        
        result = subprocess.run(
            ['R', '--slave', '--vanilla', '-e', r_test_script],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            print("✅ R performance test output:")
            print(result.stdout)
            print("✅ R performance test PASSED")
            return True
        else:
            print("❌ R performance test FAILED:")
            print(result.stderr)
            return False
            
    except Exception as e:
        print(f"❌ R performance test ERROR: {e}")
        return False

def main():
    """Run all optimization tests"""
    print("🚀 Running Optimization Tests\n")
    
    tests = [
        ("Resource Scaling", test_resource_scaling),
        ("Parquet Performance", test_parquet_performance), 
        ("Container Warming", test_container_warming),
        ("R Performance", test_r_performance)
    ]
    
    results = {}
    
    for test_name, test_func in tests:
        try:
            results[test_name] = test_func()
        except Exception as e:
            print(f"❌ {test_name} test crashed: {e}")
            results[test_name] = False
        print()  # Add spacing between tests
    
    # Summary
    print("📋 Test Results Summary:")
    print("=" * 50)
    
    passed = sum(1 for result in results.values() if result)
    total = len(results)
    
    for test_name, passed_test in results.items():
        status = "✅ PASSED" if passed_test else "❌ FAILED"
        print(f"{test_name}: {status}")
    
    print(f"\nOverall: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All optimization tests PASSED!")
        return 0
    else:
        print("⚠️ Some optimization tests FAILED")
        return 1

if __name__ == "__main__":
    import sys
    import numpy as np  # Import here for test
    exit_code = main()
    sys.exit(exit_code)