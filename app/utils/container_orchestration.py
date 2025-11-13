"""
Container orchestration abstraction layer.

Provides a unified interface for running containerized tasks that works with both
Google Cloud Run Jobs and AWS ECS Tasks.

The appropriate implementation is selected based on the CLOUD_PROVIDER environment variable:
- "gcp" (default): Use Google Cloud Run Jobs
- "aws": Use AWS ECS Tasks
"""

import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class JobState(Enum):
    """Unified job state enum."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class JobExecution:
    """Represents a job execution with unified fields."""

    def __init__(
        self,
        execution_id: str,
        state: JobState,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        log_uri: Optional[str] = None,
        error_message: Optional[str] = None,
        task_arn: Optional[str] = None,
    ):
        self.execution_id = execution_id
        self.state = state
        self.start_time = start_time
        self.end_time = end_time
        self.log_uri = log_uri
        self.error_message = error_message
        self.task_arn = task_arn

    @property
    def duration_minutes(self) -> Optional[float]:
        """Calculate duration in minutes if both start and end times are available."""
        if self.start_time and self.end_time:
            delta = self.end_time - self.start_time
            return delta.total_seconds() / 60.0
        return None


class ContainerOrchestrationProvider(ABC):
    """Abstract base class for container orchestration providers."""

    @abstractmethod
    def run_job(
        self, job_name: str, overrides: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Start a new job execution.

        Args:
            job_name: Name of the job/task to run
            overrides: Optional environment variable overrides

        Returns:
            Execution ID
        """
        pass

    @abstractmethod
    def get_execution(self, execution_id: str) -> JobExecution:
        """
        Get details about a job execution.

        Args:
            execution_id: ID of the execution

        Returns:
            JobExecution object with status and details
        """
        pass

    @abstractmethod
    def list_executions(
        self, job_name: str, limit: int = 100
    ) -> List[JobExecution]:
        """
        List recent executions for a job.

        Args:
            job_name: Name of the job/task
            limit: Maximum number of executions to return

        Returns:
            List of JobExecution objects
        """
        pass

    @abstractmethod
    def cancel_execution(self, execution_id: str) -> None:
        """
        Cancel a running execution.

        Args:
            execution_id: ID of the execution to cancel
        """
        pass


class GCPCloudRunProvider(ContainerOrchestrationProvider):
    """Google Cloud Run Jobs implementation."""

    def __init__(self):
        from google.cloud import run_v2

        self.jobs_client = run_v2.JobsClient()
        self.executions_client = run_v2.ExecutionsClient()
        self.project_id = os.getenv("PROJECT_ID")
        self.region = os.getenv("REGION", "europe-west1")

        if not self.project_id:
            raise ValueError(
                "PROJECT_ID environment variable must be set for GCP"
            )

    def _map_state(self, gcp_state: Any) -> JobState:
        """Map GCP execution state to unified JobState."""
        # Cloud Run states: PENDING, RUNNING, SUCCEEDED, FAILED, CANCELLED
        state_map = {
            "PENDING": JobState.PENDING,
            "RUNNING": JobState.RUNNING,
            "SUCCEEDED": JobState.SUCCEEDED,
            "FAILED": JobState.FAILED,
            "CANCELLED": JobState.CANCELLED,
        }
        state_str = str(gcp_state).split(".")[-1]
        return state_map.get(state_str, JobState.UNKNOWN)

    def run_job(
        self, job_name: str, overrides: Optional[Dict[str, Any]] = None
    ) -> str:
        """Start a new Cloud Run Job execution."""
        job_path = f"projects/{self.project_id}/locations/{self.region}/jobs/{job_name}"

        request = {"name": job_path}

        # Add environment variable overrides if provided
        if overrides:
            from google.cloud.run_v2.types import RunJobRequest

            # Build overrides
            env_overrides = [
                {"name": k, "value": str(v)} for k, v in overrides.items()
            ]
            request["overrides"] = {
                "container_overrides": [{"env": env_overrides}]
            }

        operation = self.jobs_client.run_job(request=request)
        execution = operation.result()
        return execution.name.split("/")[-1]

    def get_execution(self, execution_id: str) -> JobExecution:
        """Get Cloud Run Job execution details."""
        # execution_id can be either short name or full resource path
        if not execution_id.startswith("projects/"):
            # Construct full path from short name
            # execution_id format: {job-name}-{unique-id}
            execution_path = f"projects/{self.project_id}/locations/{self.region}/jobs/{execution_id.rsplit('-', 1)[0]}/executions/{execution_id}"
        else:
            execution_path = execution_id

        execution = self.executions_client.get_execution(name=execution_path)

        state = self._map_state(execution.completion_status)
        start_time = None
        end_time = None

        if execution.start_time:
            start_time = execution.start_time.replace(tzinfo=timezone.utc)
        if execution.completion_time:
            end_time = execution.completion_time.replace(tzinfo=timezone.utc)

        error_message = None
        if state == JobState.FAILED and execution.completion_status:
            error_message = str(execution.completion_status)

        log_uri = f"https://console.cloud.google.com/run/jobs/executions/details/{self.region}/{execution_id}"

        return JobExecution(
            execution_id=execution_id,
            state=state,
            start_time=start_time,
            end_time=end_time,
            log_uri=log_uri,
            error_message=error_message,
        )

    def list_executions(
        self, job_name: str, limit: int = 100
    ) -> List[JobExecution]:
        """List Cloud Run Job executions."""
        job_path = f"projects/{self.project_id}/locations/{self.region}/jobs/{job_name}"

        request = {"parent": job_path, "page_size": limit}

        executions = []
        for execution in self.executions_client.list_executions(
            request=request
        ):
            exec_id = execution.name.split("/")[-1]
            executions.append(self.get_execution(exec_id))

        return executions

    def cancel_execution(self, execution_id: str) -> None:
        """Cancel a Cloud Run Job execution."""
        if not execution_id.startswith("projects/"):
            execution_path = f"projects/{self.project_id}/locations/{self.region}/jobs/{execution_id.rsplit('-', 1)[0]}/executions/{execution_id}"
        else:
            execution_path = execution_id

        self.executions_client.cancel_execution(name=execution_path)


class AWSECSProvider(ContainerOrchestrationProvider):
    """AWS ECS Tasks implementation."""

    def __init__(self):
        import boto3

        self.ecs_client = boto3.client("ecs")
        self.logs_client = boto3.client("logs")
        self.ec2_client = boto3.client("ec2")
        
        # Get AWS account ID dynamically
        sts_client = boto3.client("sts")
        self.account_id = sts_client.get_caller_identity()["Account"]
        
        self.cluster = os.getenv("ECS_CLUSTER")
        self.region = os.getenv("AWS_REGION", "us-east-1")

        if not self.cluster:
            raise ValueError("ECS_CLUSTER environment variable must be set for AWS")

        # Get VPC configuration for task execution
        # Try environment variables first, then query from ECS service
        self.subnets = self._get_subnets()
        self.security_groups = self._get_security_groups()
    
    def _get_subnets(self) -> list:
        """Get subnets for task execution from env vars or ECS service."""
        subnets_env = os.getenv("ECS_SUBNETS", "")
        if subnets_env:
            return [s.strip() for s in subnets_env.split(",") if s.strip()]
        
        # Try to get from existing service configuration
        try:
            services = self.ecs_client.list_services(cluster=self.cluster, maxResults=1)
            if services.get("serviceArns"):
                service_details = self.ecs_client.describe_services(
                    cluster=self.cluster,
                    services=[services["serviceArns"][0]]
                )
                if service_details.get("services"):
                    network_config = service_details["services"][0].get("networkConfiguration", {})
                    awsvpc_config = network_config.get("awsvpcConfiguration", {})
                    return awsvpc_config.get("subnets", [])
        except Exception:
            pass
        
        return []
    
    def _get_security_groups(self) -> list:
        """Get security groups for task execution from env vars or ECS service."""
        sg_env = os.getenv("ECS_SECURITY_GROUPS", "")
        if sg_env:
            return [s.strip() for s in sg_env.split(",") if s.strip()]
        
        # Try to get from existing service configuration
        try:
            services = self.ecs_client.list_services(cluster=self.cluster, maxResults=1)
            if services.get("serviceArns"):
                service_details = self.ecs_client.describe_services(
                    cluster=self.cluster,
                    services=[services["serviceArns"][0]]
                )
                if service_details.get("services"):
                    network_config = service_details["services"][0].get("networkConfiguration", {})
                    awsvpc_config = network_config.get("awsvpcConfiguration", {})
                    return awsvpc_config.get("securityGroups", [])
        except Exception:
            pass
        
        return []

    def _map_state(self, ecs_status: str, stop_code: Optional[str] = None) -> JobState:
        """Map ECS task status to unified JobState."""
        if ecs_status == "PENDING":
            return JobState.PENDING
        elif ecs_status == "RUNNING":
            return JobState.RUNNING
        elif ecs_status == "STOPPED":
            if stop_code == "EssentialContainerExited":
                # Check exit code to determine success/failure
                return JobState.SUCCEEDED  # Will be refined by exit code check
            elif stop_code == "UserInitiated":
                return JobState.CANCELLED
            else:
                return JobState.FAILED
        else:
            return JobState.UNKNOWN

    def run_job(
        self, job_name: str, overrides: Optional[Dict[str, Any]] = None
    ) -> str:
        """Start a new ECS Task."""
        # job_name is the task definition family name
        run_task_params = {
            "cluster": self.cluster,
            "taskDefinition": job_name,
            "launchType": "FARGATE",
            "networkConfiguration": {
                "awsvpcConfiguration": {
                    "subnets": self.subnets,
                    "securityGroups": self.security_groups,
                    "assignPublicIp": "DISABLED",
                }
            },
        }

        # Add environment variable overrides if provided
        if overrides:
            env_overrides = [
                {"name": k, "value": str(v)} for k, v in overrides.items()
            ]
            run_task_params["overrides"] = {
                "containerOverrides": [
                    {"name": "training", "environment": env_overrides}
                ]
            }

        response = self.ecs_client.run_task(**run_task_params)

        if not response.get("tasks"):
            raise RuntimeError("Failed to start ECS task")

        task_arn = response["tasks"][0]["taskArn"]
        # Return just the task ID (last part of ARN)
        return task_arn.split("/")[-1]

    def get_execution(self, execution_id: str) -> JobExecution:
        """Get ECS Task execution details."""
        # execution_id can be short task ID or full ARN
        if not execution_id.startswith("arn:"):
            # Construct full ARN using dynamically retrieved account ID
            task_arn = f"arn:aws:ecs:{self.region}:{self.account_id}:task/{self.cluster}/{execution_id}"
        else:
            task_arn = execution_id

        response = self.ecs_client.describe_tasks(
            cluster=self.cluster, tasks=[task_arn]
        )

        if not response.get("tasks"):
            return JobExecution(
                execution_id=execution_id,
                state=JobState.UNKNOWN,
                error_message="Task not found",
            )

        task = response["tasks"][0]

        # Extract state
        status = task.get("lastStatus", "UNKNOWN")
        stop_code = task.get("stopCode")
        state = self._map_state(status, stop_code)

        # Check container exit code for success/failure
        if state == JobState.SUCCEEDED and task.get("containers"):
            for container in task["containers"]:
                if container.get("exitCode", 0) != 0:
                    state = JobState.FAILED
                    break

        # Extract timestamps
        start_time = None
        end_time = None
        if task.get("startedAt"):
            start_time = task["startedAt"]
        if task.get("stoppedAt"):
            end_time = task["stoppedAt"]

        # Extract error message
        error_message = None
        if state == JobState.FAILED:
            if task.get("stoppedReason"):
                error_message = task["stoppedReason"]
            elif task.get("containers"):
                for container in task["containers"]:
                    if container.get("reason"):
                        error_message = container["reason"]
                        break

        # Build log URI
        log_uri = f"https://{self.region}.console.aws.amazon.com/ecs/v2/clusters/{self.cluster}/tasks/{execution_id}"

        return JobExecution(
            execution_id=execution_id,
            state=state,
            start_time=start_time,
            end_time=end_time,
            log_uri=log_uri,
            error_message=error_message,
            task_arn=task_arn,
        )

    def list_executions(
        self, job_name: str, limit: int = 100
    ) -> List[JobExecution]:
        """List ECS Tasks for a task family."""
        # List tasks by family
        task_arns = []

        # List running tasks
        response = self.ecs_client.list_tasks(
            cluster=self.cluster, family=job_name, maxResults=min(limit, 100)
        )
        task_arns.extend(response.get("taskArns", []))

        # List stopped tasks
        if len(task_arns) < limit:
            response = self.ecs_client.list_tasks(
                cluster=self.cluster,
                family=job_name,
                desiredStatus="STOPPED",
                maxResults=min(limit - len(task_arns), 100),
            )
            task_arns.extend(response.get("taskArns", []))

        # Get details for each task
        executions = []
        for task_arn in task_arns:
            task_id = task_arn.split("/")[-1]
            try:
                executions.append(self.get_execution(task_id))
            except Exception:
                continue

        return executions

    def cancel_execution(self, execution_id: str) -> None:
        """Cancel an ECS Task."""
        if not execution_id.startswith("arn:"):
            task_arn = f"arn:aws:ecs:{self.region}:{self.account_id}:task/{self.cluster}/{execution_id}"
        else:
            task_arn = execution_id

        self.ecs_client.stop_task(cluster=self.cluster, task=task_arn, reason="User cancelled")


# Singleton instance
_provider: Optional[ContainerOrchestrationProvider] = None


def get_orchestration_provider() -> ContainerOrchestrationProvider:
    """
    Get the appropriate container orchestration provider based on CLOUD_PROVIDER env var.

    Returns:
        ContainerOrchestrationProvider instance (Cloud Run or ECS)
    """
    global _provider
    if _provider is None:
        provider_name = os.getenv("CLOUD_PROVIDER", "gcp").lower()
        if provider_name == "aws":
            _provider = AWSECSProvider()
        else:
            _provider = GCPCloudRunProvider()
    return _provider


# Convenience functions that delegate to the provider
def run_training_job(
    job_name: Optional[str] = None, overrides: Optional[Dict[str, Any]] = None
) -> str:
    """
    Start a new training job execution.

    Args:
        job_name: Name of the job/task (defaults to TRAINING_JOB_NAME or TRAINING_TASK_FAMILY)
        overrides: Optional environment variable overrides

    Returns:
        Execution ID
    """
    if job_name is None:
        provider_name = os.getenv("CLOUD_PROVIDER", "gcp").lower()
        if provider_name == "aws":
            job_name = os.getenv("TRAINING_TASK_FAMILY")
        else:
            job_name = os.getenv("TRAINING_JOB_NAME")

    if not job_name:
        raise ValueError(
            "job_name must be provided or TRAINING_JOB_NAME/TRAINING_TASK_FAMILY must be set"
        )

    return get_orchestration_provider().run_job(job_name, overrides)


def get_job_execution(execution_id: str) -> JobExecution:
    """Get details about a job execution."""
    return get_orchestration_provider().get_execution(execution_id)


def list_job_executions(
    job_name: Optional[str] = None, limit: int = 100
) -> List[JobExecution]:
    """List recent executions for a job."""
    if job_name is None:
        provider_name = os.getenv("CLOUD_PROVIDER", "gcp").lower()
        if provider_name == "aws":
            job_name = os.getenv("TRAINING_TASK_FAMILY")
        else:
            job_name = os.getenv("TRAINING_JOB_NAME")

    if not job_name:
        raise ValueError(
            "job_name must be provided or TRAINING_JOB_NAME/TRAINING_TASK_FAMILY must be set"
        )

    return get_orchestration_provider().list_executions(job_name, limit)


def cancel_job_execution(execution_id: str) -> None:
    """Cancel a running execution."""
    get_orchestration_provider().cancel_execution(execution_id)
