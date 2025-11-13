output "web_service_url" {
  description = "URL of the web service load balancer"
  value       = "http://${aws_lb.web_alb.dns_name}"
}

output "web_service_alb_dns" {
  description = "DNS name of the Application Load Balancer"
  value       = aws_lb.web_alb.dns_name
}

output "ecs_cluster_name" {
  description = "Name of the ECS cluster"
  value       = aws_ecs_cluster.main.name
}

output "web_service_name" {
  description = "Name of the web ECS service"
  value       = aws_ecs_service.web_service.name
}

output "training_task_family" {
  description = "Family name of the training task definition"
  value       = aws_ecs_task_definition.training_task.family
}

output "s3_bucket_name" {
  description = "Name of the S3 bucket for application data"
  value       = aws_s3_bucket.app_data.id
}

output "ecr_web_repository_url" {
  description = "URL of the web ECR repository"
  value       = aws_ecr_repository.web.repository_url
}

output "ecr_training_repository_url" {
  description = "URL of the training ECR repository"
  value       = aws_ecr_repository.training.repository_url
}

output "ecr_training_base_repository_url" {
  description = "URL of the training base ECR repository"
  value       = aws_ecr_repository.training_base.repository_url
}

output "vpc_id" {
  description = "ID of the VPC"
  value       = aws_vpc.main.id
}

output "private_subnet_ids" {
  description = "IDs of private subnets"
  value       = aws_subnet.private[*].id
}

output "public_subnet_ids" {
  description = "IDs of public subnets"
  value       = aws_subnet.public[*].id
}
