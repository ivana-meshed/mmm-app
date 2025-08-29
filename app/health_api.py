#!/usr/bin/env python3
import json
import os
import sys
from datetime import datetime

def get_health_status():
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "service": "mmm-trainer",
            "cpu_usage": round(cpu, 1),
            "memory_usage": round(memory.percent, 1),
            "available_memory_gb": round(memory.available / (1024**3), 2),
            "scheduler_warmup": True
        }
    except Exception as e:
        return {
            "status": "error",
            "timestamp": datetime.now().isoformat(),
            "error": str(e)
        }

if __name__ == "__main__":
    health = get_health_status()
    print(json.dumps(health, indent=2))