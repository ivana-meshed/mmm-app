#!/usr/bin/env python3
"""
Collect baseline performance metrics before optimization deployment
"""

import datetime
import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import Dict, List

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class BaselineMetric:
    timestamp: datetime.datetime
    job_id: str
    job_type: str  # small, medium, large
    iterations: int
    trials: int
    country: str
    duration_minutes: float
    cpu_hours: float
    memory_gb_hours: float
    cost_estimate: float
    success: bool
    error_message: str = None


class BaselineCollector:
    def __init__(self, service_url: str, output_file: str):
        self.service_url = service_url.rstrip("/")
        self.output_file = output_file
        self.metrics: List[BaselineMetric] = []

    def create_synthetic_jobs(self) -> List[Dict]:
        """Create representative training jobs for baseline measurement"""
        return [
            # Small jobs
            {
                "type": "small",
                "iterations": 50,
                "trials": 2,
                "countries": ["fr", "de"],
            },
            {
                "type": "small",
                "iterations": 75,
                "trials": 3,
                "countries": ["uk", "es"],
            },
            # Medium jobs
            {
                "type": "medium",
                "iterations": 150,
                "trials": 4,
                "countries": ["fr", "de"],
            },
            {
                "type": "medium",
                "iterations": 200,
                "trials": 5,
                "countries": ["uk"],
            },
            # Large jobs
            {
                "type": "large",
                "iterations": 300,
                "trials": 7,
                "countries": ["fr"],
            },
            {
                "type": "large",
                "iterations": 400,
                "trials": 8,
                "countries": ["de"],
            },
        ]

    def run_baseline_collection(self, duration_days: int = 7):
        """Collect baseline metrics over specified duration"""
        logger.info(f"Starting {duration_days}-day baseline collection...")

        jobs = self.create_synthetic_jobs()
        jobs_per_day = len(jobs)
        total_jobs = jobs_per_day * duration_days

        logger.info(f"Will run {total_jobs} jobs over {duration_days} days")

        for day in range(duration_days):
            logger.info(
                f"Day {day + 1}/{duration_days}: Running {jobs_per_day} baseline jobs"
            )

            for job_idx, job_config in enumerate(jobs):
                for country in job_config["countries"]:
                    job_id = f"baseline-{day+1}-{job_idx+1}-{country}"

                    metric = self.run_single_baseline_job(
                        job_id=job_id,
                        job_type=job_config["type"],
                        iterations=job_config["iterations"],
                        trials=job_config["trials"],
                        country=country,
                    )

                    self.metrics.append(metric)

                    # Brief pause between jobs
                    time.sleep(30)

            # Longer pause between days (simulate realistic usage)
            if day < duration_days - 1:
                logger.info("Waiting 4 hours before next batch...")
                time.sleep(4 * 3600)  # 4 hours

    def run_single_baseline_job(
        self,
        job_id: str,
        job_type: str,
        iterations: int,
        trials: int,
        country: str,
    ) -> BaselineMetric:
        """Run a single baseline training job"""
        logger.info(
            f"Running baseline job {job_id}: {job_type}, {iterations} iter, {trials} trials, {country}"
        )

        start_time = datetime.datetime.now()

        # Training payload (simplified - you'd use real data)
        payload = {
            "job_id": job_id,
            "country": country,
            "iterations": iterations,
            "trials": trials,
            "revision": "baseline",
            "baseline_test": True,
            "paid_media_spends": [
                "GA_SUPPLY_COST",
                "GA_DEMAND_COST",
                "META_DEMAND_COST",
            ],
            "paid_media_vars": [
                "GA_SUPPLY_COST",
                "GA_DEMAND_COST",
                "META_DEMAND_COST",
            ],
            "context_vars": ["IS_WEEKEND"],
            "factor_vars": ["IS_WEEKEND"],
            "organic_vars": ["ORGANIC_TRAFFIC"],
        }

        try:
            # Start training job
            response = requests.post(
                f"{self.service_url}/train",
                json=payload,
                timeout=7200,  # 2 hour timeout
            )

            if response.status_code == 200:
                end_time = datetime.datetime.now()
                duration = (end_time - start_time).total_seconds() / 60

                # Estimate resource usage based on job characteristics
                cpu_hours = self.estimate_cpu_hours(
                    iterations, trials, duration
                )
                memory_gb_hours = self.estimate_memory_hours(
                    iterations, trials, duration
                )
                cost = self.estimate_cost(cpu_hours, memory_gb_hours)

                metric = BaselineMetric(
                    timestamp=start_time,
                    job_id=job_id,
                    job_type=job_type,
                    iterations=iterations,
                    trials=trials,
                    country=country,
                    duration_minutes=duration,
                    cpu_hours=cpu_hours,
                    memory_gb_hours=memory_gb_hours,
                    cost_estimate=cost,
                    success=True,
                )

                logger.info(f"✅ {job_id} completed in {duration:.1f} minutes")
                return metric

            else:
                logger.error(f"❌ {job_id} failed: HTTP {response.status_code}")
                return self.create_failed_metric(
                    job_id,
                    job_type,
                    iterations,
                    trials,
                    country,
                    start_time,
                    f"HTTP {response.status_code}",
                )

        except Exception as e:
            logger.error(f"❌ {job_id} error: {e}")
            return self.create_failed_metric(
                job_id,
                job_type,
                iterations,
                trials,
                country,
                start_time,
                str(e),
            )

    def create_failed_metric(
        self,
        job_id: str,
        job_type: str,
        iterations: int,
        trials: int,
        country: str,
        start_time: datetime.datetime,
        error: str,
    ) -> BaselineMetric:
        """Create a failed job metric"""
        return BaselineMetric(
            timestamp=start_time,
            job_id=job_id,
            job_type=job_type,
            iterations=iterations,
            trials=trials,
            country=country,
            duration_minutes=0,
            cpu_hours=0,
            memory_gb_hours=0,
            cost_estimate=0,
            success=False,
            error_message=error,
        )

    def estimate_cpu_hours(
        self, iterations: int, trials: int, duration_minutes: float
    ) -> float:
        """Estimate CPU hours used based on job characteristics"""
        # Assume 4 vCPU baseline, scale by complexity
        base_cpu = 4
        complexity_factor = (iterations * trials) / 1000
        cpu_hours = (
            (duration_minutes / 60) * base_cpu * (1 + complexity_factor * 0.1)
        )
        return round(cpu_hours, 2)

    def estimate_memory_hours(
        self, iterations: int, trials: int, duration_minutes: float
    ) -> float:
        """Estimate memory GB-hours used"""
        base_memory_gb = 16
        memory_gb_hours = (duration_minutes / 60) * base_memory_gb
        return round(memory_gb_hours, 2)

    def estimate_cost(self, cpu_hours: float, memory_gb_hours: float) -> float:
        """Estimate cost based on Cloud Run pricing"""
        # Cloud Run pricing (approximate)
        cpu_cost_per_hour = 0.072  # $0.072 per vCPU hour
        memory_cost_per_gb_hour = 0.008  # $0.008 per GB hour

        total_cost = (cpu_hours * cpu_cost_per_hour) + (
            memory_gb_hours * memory_cost_per_gb_hour
        )
        return round(total_cost, 4)

    def generate_baseline_report(self) -> str:
        """Generate comprehensive baseline report"""
        if not self.metrics:
            return "No baseline metrics collected."

        # Convert to DataFrame
        df = pd.DataFrame([asdict(m) for m in self.metrics])

        # Calculate statistics
        successful_jobs = df[df["success"] == True]

        stats_by_type = (
            successful_jobs.groupby("job_type")
            .agg(
                {
                    "duration_minutes": ["count", "mean", "std", "min", "max"],
                    "cpu_hours": ["mean", "sum"],
                    "memory_gb_hours": ["mean", "sum"],
                    "cost_estimate": ["mean", "sum"],
                }
            )
            .round(2)
        )

        total_cost = successful_jobs["cost_estimate"].sum()
        total_duration = successful_jobs["duration_minutes"].sum() / 60  # hours
        success_rate = (len(successful_jobs) / len(df)) * 100

        report = f"""
# MMM Trainer Baseline Performance Report

## Collection Summary
- **Period**: {self.metrics[0].timestamp.date()} to {self.metrics[-1].timestamp.date()}
- **Total Jobs**: {len(df)}
- **Successful Jobs**: {len(successful_jobs)}
- **Success Rate**: {success_rate:.1f}%
- **Total Training Time**: {total_duration:.1f} hours
- **Estimated Total Cost**: ${total_cost:.2f}

## Performance by Job Type

{stats_by_type.to_string()}


### Average Performance
"""

        for job_type in ["small", "medium", "large"]:
            type_data = successful_jobs[successful_jobs["job_type"] == job_type]
            if len(type_data) > 0:
                avg_duration = type_data["duration_minutes"].mean()
                avg_cost = type_data["cost_estimate"].mean()
                count = len(type_data)

                report += f"""
- **{job_type.title()} Jobs** ({count} samples):
  - Duration: {avg_duration:.1f} ± {type_data['duration_minutes'].std():.1f} minutes
  - Cost: ${avg_cost:.3f} per job
  - CPU Utilization: {type_data['cpu_hours'].mean():.1f} hours avg
"""

        # Resource utilization analysis
        total_cpu_hours = successful_jobs["cpu_hours"].sum()
        total_memory_hours = successful_jobs["memory_gb_hours"].sum()

        report += f"""

### Resource Utilization
- **Total CPU Hours**: {total_cpu_hours:.1f}
- **Total Memory GB-Hours**: {total_memory_hours:.1f}
- **Average CPU per Job**: {total_cpu_hours / len(successful_jobs):.1f} hours
- **Average Memory per Job**: {total_memory_hours / len(successful_jobs):.1f} GB-hours

### Optimization Targets
Based on baseline analysis, the optimization should target:
"""

        # Identify optimization opportunities
        if successful_jobs["duration_minutes"].mean() > 45:
            report += "- 🎯 **Duration Reduction**: Current average exceeds 45 minutes\n"

        cpu_efficiency = total_cpu_hours / total_duration
        if cpu_efficiency < 3:  # Less than 75% of 4 vCPU
            report += "- 🎯 **CPU Utilization**: Underutilized CPU resources\n"

        failure_rate = (len(df) - len(successful_jobs)) / len(df) * 100
        if failure_rate > 10:
            report += f"- 🎯 **Reliability**: {failure_rate:.1f}% failure rate needs improvement\n"

        report += f"""

## Expected Optimization Impact
- **Duration**: 40-50% reduction (target: {successful_jobs['duration_minutes'].mean() * 0.5:.1f} minutes avg)
- **Resource Efficiency**: 30-40% better utilization
- **Cost**: Despite higher resource allocation, expect 20-30% cost reduction due to efficiency
- **Reliability**: Target >95% success rate

## Data for Comparison
Raw baseline data saved to: `{self.output_file}`
Use this data to validate optimization effectiveness.
"""

        return report

    def save_metrics(self):
        """Save metrics to JSON file"""
        data = [asdict(m) for m in self.metrics]

        # Convert datetime objects to ISO format
        for item in data:
            item["timestamp"] = item["timestamp"].isoformat()

        with open(self.output_file, "w") as f:
            json.dump(data, f, indent=2)

        logger.info(f"📁 Baseline metrics saved to {self.output_file}")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Collect MMM Trainer baseline metrics"
    )
    parser.add_argument(
        "--service-url", required=True, help="Production service URL"
    )
    parser.add_argument(
        "--duration-days",
        type=int,
        default=3,
        help="Collection duration in days",
    )
    parser.add_argument(
        "--output", default="baseline_metrics.json", help="Output file"
    )
    parser.add_argument(
        "--report", default="baseline_report.md", help="Report output file"
    )

    args = parser.parse_args()

    print(f"🚀 Starting baseline collection for {args.duration_days} days")
    print(f"Service: {args.service_url}")
    print(f"Output: {args.output}")

    collector = BaselineCollector(args.service_url, args.output)

    # Collect baseline data
    collector.run_baseline_collection(args.duration_days)

    # Generate and save report
    report = collector.generate_baseline_report()

    with open(args.report, "w") as f:
        f.write(report)

    # Save raw metrics
    collector.save_metrics()

    print(f"\n📊 Baseline collection completed!")
    print(f"📋 Report: {args.report}")
    print(f"📁 Raw data: {args.output}")


if __name__ == "__main__":
    main()
