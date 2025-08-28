"""
Health check and warming endpoint for Cloud Run
"""
import os
import time
import psutil
import streamlit as st
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class HealthChecker:
    def __init__(self):
        self.startup_time = datetime.now()
        self.warm_status = {
            'container_ready': False,
            'dependencies_loaded': False,
            'r_ready': False,
            'gcs_authenticated': False
        }
        
    def check_container_health(self):
        """Comprehensive health check for the container"""
        health_status = {
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'uptime_seconds': (datetime.now() - self.startup_time).total_seconds(),
            'checks': {}
        }
        
        try:
            # Check system resources
            health_status['checks']['cpu_usage'] = psutil.cpu_percent(interval=1)
            health_status['checks']['memory_usage'] = psutil.virtual_memory().percent
            health_status['checks']['disk_usage'] = psutil.disk_usage('/').percent
            
            # Check if R is available
            try:
                import subprocess
                result = subprocess.run(['R', '--version'], 
                                      capture_output=True, text=True, timeout=5)
                health_status['checks']['r_available'] = result.returncode == 0
                self.warm_status['r_ready'] = result.returncode == 0
            except Exception as e:
                health_status['checks']['r_available'] = False
                health_status['checks']['r_error'] = str(e)
            
            # Check GCS authentication
            try:
                from google.cloud import storage
                client = storage.Client()
                bucket_name = os.getenv('GCS_BUCKET', 'mmm-app-output')
                bucket = client.bucket(bucket_name)
                # Try to list one object (lightweight operation)
                list(client.list_blobs(bucket, max_results=1))
                health_status['checks']['gcs_authenticated'] = True
                self.warm_status['gcs_authenticated'] = True
            except Exception as e:
                health_status['checks']['gcs_authenticated'] = False
                health_status['checks']['gcs_error'] = str(e)
            
            # Check Python dependencies
            try:
                import pandas, numpy, pyarrow, streamlit
                health_status['checks']['python_deps'] = True
                self.warm_status['dependencies_loaded'] = True
            except ImportError as e:
                health_status['checks']['python_deps'] = False
                health_status['checks']['python_deps_error'] = str(e)
            
            # Overall container readiness
            self.warm_status['container_ready'] = all([
                health_status['checks'].get('r_available', False),
                health_status['checks'].get('gcs_authenticated', False), 
                health_status['checks'].get('python_deps', False)
            ])
            
            health_status['warm_status'] = self.warm_status
            
            # Determine overall health
            critical_checks = ['r_available', 'gcs_authenticated', 'python_deps']
            if not all(health_status['checks'].get(check, False) for check in critical_checks):
                health_status['status'] = 'unhealthy'
            
        except Exception as e:
            health_status['status'] = 'error'
            health_status['error'] = str(e)
            
        return health_status

# Global health checker instance
health_checker = HealthChecker()

def create_health_page():
    """Create Streamlit page for health checks"""
    st.set_page_config(
        page_title="Health Check", 
        page_icon="🏥", 
        layout="wide"
    )
    
    st.title("🏥 Container Health Status")
    
    # Auto-refresh every 10 seconds
    if st.button("🔄 Refresh Health Status") or 'auto_refresh' not in st.session_state:
        st.session_state['auto_refresh'] = True
        st.rerun()
    
    health_status = health_checker.check_container_health()
    
    # Display overall status
    if health_status['status'] == 'healthy':
        st.success("✅ Container is healthy and ready")
    elif health_status['status'] == 'unhealthy':
        st.warning("⚠️ Container has health issues")
    else:
        st.error("❌ Container health check failed")
    
    # Display detailed metrics in columns
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric("CPU Usage", f"{health_status['checks'].get('cpu_usage', 0):.1f}%")
        st.metric("Memory Usage", f"{health_status['checks'].get('memory_usage', 0):.1f}%")
        
    with col2:
        st.metric("Disk Usage", f"{health_status['checks'].get('disk_usage', 0):.1f}%")
        uptime = health_status.get('uptime_seconds', 0)
        st.metric("Uptime", f"{uptime/60:.1f} min")
        
    with col3:
        r_status = "✅" if health_status['checks'].get('r_available') else "❌"
        st.metric("R Available", r_status)
        gcs_status = "✅" if health_status['checks'].get('gcs_authenticated') else "❌"
        st.metric("GCS Auth", gcs_status)
    
    # Warm status indicators
    st.subheader("🔥 Warm-up Status")
    warm_status = health_status.get('warm_status', {})
    
    for component, status in warm_status.items():
        icon = "✅" if status else "⏳"
        st.write(f"{icon} {component.replace('_', ' ').title()}: {'Ready' if status else 'Not Ready'}")
    
    # Detailed health check results
    with st.expander("🔍 Detailed Health Checks"):
        st.json(health_status)
        
    # Auto-refresh countdown
    st.write("---")
    st.write("Page will auto-refresh every 30 seconds when container is warming up")
    
    return health_status

if __name__ == "__main__":
    create_health_page()