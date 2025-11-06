provider "aws" {
  region = var.aws_region
}

##############################################################
# Locals for computed values
##############################################################
locals {
  web_service_url   = "https://${aws_lb.web_alb.dns_name}"
  auth_redirect_uri = "${local.web_service_url}/oauth2callback"
  common_tags = {
    Project     = "mmm-app"
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}

##############################################################
# VPC and Networking
##############################################################
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = merge(local.common_tags, {
    Name = "${var.service_name}-vpc"
  })
}

resource "aws_subnet" "public" {
  count                   = length(var.availability_zones)
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true

  tags = merge(local.common_tags, {
    Name = "${var.service_name}-public-subnet-${count.index + 1}"
  })
}

resource "aws_subnet" "private" {
  count             = length(var.availability_zones)
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 100)
  availability_zone = var.availability_zones[count.index]

  tags = merge(local.common_tags, {
    Name = "${var.service_name}-private-subnet-${count.index + 1}"
  })
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = merge(local.common_tags, {
    Name = "${var.service_name}-igw"
  })
}

resource "aws_eip" "nat" {
  count  = length(var.availability_zones)
  domain = "vpc"

  tags = merge(local.common_tags, {
    Name = "${var.service_name}-nat-eip-${count.index + 1}"
  })

  depends_on = [aws_internet_gateway.main]
}

resource "aws_nat_gateway" "main" {
  count         = length(var.availability_zones)
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = merge(local.common_tags, {
    Name = "${var.service_name}-nat-${count.index + 1}"
  })

  depends_on = [aws_internet_gateway.main]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = merge(local.common_tags, {
    Name = "${var.service_name}-public-rt"
  })
}

resource "aws_route_table" "private" {
  count  = length(var.availability_zones)
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[count.index].id
  }

  tags = merge(local.common_tags, {
    Name = "${var.service_name}-private-rt-${count.index + 1}"
  })
}

resource "aws_route_table_association" "public" {
  count          = length(var.availability_zones)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = length(var.availability_zones)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

##############################################################
# Security Groups
##############################################################
resource "aws_security_group" "web_alb" {
  name        = "${var.service_name}-web-alb-sg"
  description = "Security group for web application load balancer"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "${var.service_name}-web-alb-sg"
  })
}

resource "aws_security_group" "web_service" {
  name        = "${var.service_name}-web-service-sg"
  description = "Security group for web service tasks"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.web_alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "${var.service_name}-web-service-sg"
  })
}

resource "aws_security_group" "training_task" {
  name        = "${var.service_name}-training-task-sg"
  description = "Security group for training tasks"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name = "${var.service_name}-training-task-sg"
  })
}

##############################################################
# S3 Bucket for Application Data
##############################################################
resource "aws_s3_bucket" "app_data" {
  bucket = var.s3_bucket_name

  tags = merge(local.common_tags, {
    Name = var.s3_bucket_name
  })
}

resource "aws_s3_bucket_versioning" "app_data" {
  bucket = aws_s3_bucket.app_data.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "app_data" {
  bucket = aws_s3_bucket.app_data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

##############################################################
# ECR Repositories
##############################################################
resource "aws_ecr_repository" "web" {
  name                 = "${var.service_name}-web"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.common_tags
}

resource "aws_ecr_repository" "training" {
  name                 = "${var.service_name}-training"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.common_tags
}

resource "aws_ecr_repository" "training_base" {
  name                 = "${var.service_name}-training-base"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.common_tags
}

##############################################################
# Secrets Manager
##############################################################
resource "aws_secretsmanager_secret" "sf_private_key" {
  name        = "${var.service_name}-sf-private-key"
  description = "Snowflake private key for authentication"

  tags = local.common_tags
}

resource "aws_secretsmanager_secret_version" "sf_private_key" {
  secret_id     = aws_secretsmanager_secret.sf_private_key.id
  secret_string = var.sf_private_key
}

resource "aws_secretsmanager_secret" "sf_private_key_persistent" {
  name        = "${var.service_name}-sf-private-key-persistent"
  description = "Persistent Snowflake private key for user-uploaded keys"

  tags = local.common_tags
}

resource "aws_secretsmanager_secret" "auth_client_id" {
  name        = "${var.service_name}-auth-client-id"
  description = "Google OAuth client ID"

  tags = local.common_tags
}

resource "aws_secretsmanager_secret_version" "auth_client_id" {
  secret_id     = aws_secretsmanager_secret.auth_client_id.id
  secret_string = var.auth_client_id
}

resource "aws_secretsmanager_secret" "auth_client_secret" {
  name        = "${var.service_name}-auth-client-secret"
  description = "Google OAuth client secret"

  tags = local.common_tags
}

resource "aws_secretsmanager_secret_version" "auth_client_secret" {
  secret_id     = aws_secretsmanager_secret.auth_client_secret.id
  secret_string = var.auth_client_secret
}

resource "aws_secretsmanager_secret" "auth_cookie_secret" {
  name        = "${var.service_name}-auth-cookie-secret"
  description = "Cookie encryption secret"

  tags = local.common_tags
}

resource "aws_secretsmanager_secret_version" "auth_cookie_secret" {
  secret_id     = aws_secretsmanager_secret.auth_cookie_secret.id
  secret_string = var.auth_cookie_secret
}

##############################################################
# IAM Roles and Policies
##############################################################

# ECS Task Execution Role (for pulling images and accessing secrets)
resource "aws_iam_role" "ecs_task_execution_role" {
  name = "${var.service_name}-ecs-task-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution_role_policy" {
  role       = aws_iam_role.ecs_task_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_task_execution_secrets" {
  name = "${var.service_name}-ecs-task-execution-secrets"
  role = aws_iam_role.ecs_task_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          aws_secretsmanager_secret.sf_private_key.arn,
          aws_secretsmanager_secret.sf_private_key_persistent.arn,
          aws_secretsmanager_secret.auth_client_id.arn,
          aws_secretsmanager_secret.auth_client_secret.arn,
          aws_secretsmanager_secret.auth_cookie_secret.arn
        ]
      }
    ]
  })
}

# Web Service Task Role (for accessing AWS services at runtime)
resource "aws_iam_role" "web_service_task_role" {
  name = "${var.service_name}-web-service-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "web_service_s3_access" {
  name = "${var.service_name}-web-service-s3-access"
  role = aws_iam_role.web_service_task_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.app_data.arn,
          "${aws_s3_bucket.app_data.arn}/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy" "web_service_secrets_access" {
  name = "${var.service_name}-web-service-secrets-access"
  role = aws_iam_role.web_service_task_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:PutSecretValue",
          "secretsmanager:CreateSecret",
          "secretsmanager:DeleteSecret",
          "secretsmanager:UpdateSecret"
        ]
        Resource = [
          aws_secretsmanager_secret.sf_private_key.arn,
          aws_secretsmanager_secret.sf_private_key_persistent.arn,
          aws_secretsmanager_secret.auth_client_id.arn,
          aws_secretsmanager_secret.auth_client_secret.arn,
          aws_secretsmanager_secret.auth_cookie_secret.arn
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy" "web_service_ecs_access" {
  name = "${var.service_name}-web-service-ecs-access"
  role = aws_iam_role.web_service_task_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecs:RunTask",
          "ecs:DescribeTasks",
          "ecs:ListTasks",
          "ecs:StopTask"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "iam:PassRole"
        ]
        Resource = [
          aws_iam_role.training_task_role.arn,
          aws_iam_role.ecs_task_execution_role.arn
        ]
      }
    ]
  })
}

# Training Task Role (for accessing AWS services at runtime)
resource "aws_iam_role" "training_task_role" {
  name = "${var.service_name}-training-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "training_task_s3_access" {
  name = "${var.service_name}-training-task-s3-access"
  role = aws_iam_role.training_task_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.app_data.arn,
          "${aws_s3_bucket.app_data.arn}/*"
        ]
      }
    ]
  })
}

##############################################################
# Application Load Balancer
##############################################################
resource "aws_lb" "web_alb" {
  name               = "${var.service_name}-web-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.web_alb.id]
  subnets            = aws_subnet.public[*].id

  tags = merge(local.common_tags, {
    Name = "${var.service_name}-web-alb"
  })
}

resource "aws_lb_target_group" "web" {
  name        = "${var.service_name}-web-tg"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    enabled             = true
    healthy_threshold   = 2
    interval            = 30
    matcher             = "200"
    path                = "/"
    port                = "traffic-port"
    protocol            = "HTTP"
    timeout             = 5
    unhealthy_threshold = 3
  }

  tags = merge(local.common_tags, {
    Name = "${var.service_name}-web-tg"
  })
}

resource "aws_lb_listener" "web_http" {
  load_balancer_arn = aws_lb.web_alb.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

##############################################################
# ECS Cluster
##############################################################
resource "aws_ecs_cluster" "main" {
  name = "${var.service_name}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = local.common_tags
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name = aws_ecs_cluster.main.name

  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    base              = 1
    weight            = 100
    capacity_provider = "FARGATE"
  }
}

##############################################################
# CloudWatch Log Groups
##############################################################
resource "aws_cloudwatch_log_group" "web_service" {
  name              = "/ecs/${var.service_name}-web"
  retention_in_days = 7

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "training_task" {
  name              = "/ecs/${var.service_name}-training"
  retention_in_days = 7

  tags = local.common_tags
}

##############################################################
# ECS Task Definition - Web Service
##############################################################
resource "aws_ecs_task_definition" "web_service" {
  family                   = "${var.service_name}-web"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.web_cpu
  memory                   = var.web_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.web_service_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "web"
      image     = var.web_image
      essential = true

      portMappings = [
        {
          containerPort = 8080
          hostPort      = 8080
          protocol      = "tcp"
        }
      ]

      environment = [
        {
          name  = "AWS_REGION"
          value = var.aws_region
        },
        {
          name  = "CLOUD_PROVIDER"
          value = "aws"
        },
        {
          name  = "S3_BUCKET"
          value = var.s3_bucket_name
        },
        {
          name  = "TRAINING_TASK_FAMILY"
          value = "${var.service_name}-training"
        },
        {
          name  = "ECS_CLUSTER"
          value = aws_ecs_cluster.main.name
        },
        {
          name  = "DEFAULT_QUEUE_NAME"
          value = var.queue_name
        },
        {
          name  = "QUEUE_ROOT"
          value = "robyn-queues"
        },
        {
          name  = "SAFE_LAG_SECONDS_AFTER_RUNNING"
          value = "5"
        },
        {
          name  = "SF_USER"
          value = var.sf_user
        },
        {
          name  = "SF_ACCOUNT"
          value = var.sf_account
        },
        {
          name  = "SF_WAREHOUSE"
          value = var.sf_warehouse
        },
        {
          name  = "SF_DATABASE"
          value = var.sf_database
        },
        {
          name  = "SF_SCHEMA"
          value = var.sf_schema
        },
        {
          name  = "SF_ROLE"
          value = var.sf_role
        },
        {
          name  = "SF_PRIVATE_KEY_SECRET"
          value = aws_secretsmanager_secret.sf_private_key.name
        },
        {
          name  = "SF_PERSISTENT_KEY_SECRET"
          value = aws_secretsmanager_secret.sf_private_key_persistent.name
        },
        {
          name  = "AUTH_CLIENT_ID_SECRET"
          value = aws_secretsmanager_secret.auth_client_id.name
        },
        {
          name  = "AUTH_CLIENT_SECRET_SECRET"
          value = aws_secretsmanager_secret.auth_client_secret.name
        },
        {
          name  = "AUTH_COOKIE_SECRET_SECRET"
          value = aws_secretsmanager_secret.auth_cookie_secret.name
        },
        {
          name  = "AUTH_REDIRECT_URI"
          value = local.auth_redirect_uri
        },
        {
          name  = "ALLOWED_DOMAINS"
          value = var.allowed_domains
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.web_service.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "web"
        }
      }
    }
  ])

  tags = local.common_tags
}

##############################################################
# ECS Service - Web Service
##############################################################
resource "aws_ecs_service" "web_service" {
  name            = "${var.service_name}-web"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.web_service.arn
  desired_count   = var.min_instances
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.web_service.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 8080
  }

  depends_on = [
    aws_lb_listener.web_http
  ]

  tags = local.common_tags
}

##############################################################
# Auto Scaling for Web Service
##############################################################
resource "aws_appautoscaling_target" "web_service" {
  max_capacity       = var.max_instances
  min_capacity       = var.min_instances
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.web_service.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "web_service_cpu" {
  name               = "${var.service_name}-web-cpu-scaling"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.web_service.resource_id
  scalable_dimension = aws_appautoscaling_target.web_service.scalable_dimension
  service_namespace  = aws_appautoscaling_target.web_service.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value = 70.0
  }
}

##############################################################
# ECS Task Definition - Training Task
##############################################################
resource "aws_ecs_task_definition" "training_task" {
  family                   = "${var.service_name}-training"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.training_cpu
  memory                   = var.training_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.training_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "training"
      image     = var.training_image
      essential = true

      environment = [
        {
          name  = "AWS_REGION"
          value = var.aws_region
        },
        {
          name  = "CLOUD_PROVIDER"
          value = "aws"
        },
        {
          name  = "S3_BUCKET"
          value = var.s3_bucket_name
        },
        {
          name  = "JOB_CONFIG_S3_PATH"
          value = "s3://${var.s3_bucket_name}/training-configs/latest/job_config.json"
        },
        {
          name  = "R_MAX_CORES"
          value = "8"
        },
        {
          name  = "OMP_NUM_THREADS"
          value = "8"
        },
        {
          name  = "OPENBLAS_NUM_THREADS"
          value = "8"
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.training_task.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "training"
        }
      }
    }
  ])

  tags = local.common_tags
}

##############################################################
# EventBridge Scheduler for Queue Ticks
##############################################################
resource "aws_iam_role" "scheduler_role" {
  name = "${var.service_name}-scheduler-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "scheduler.amazonaws.com"
        }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "scheduler_invoke_service" {
  name = "${var.service_name}-scheduler-invoke-service"
  role = aws_iam_role.scheduler_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecs:RunTask"
        ]
        Resource = aws_ecs_task_definition.web_service.arn
        Condition = {
          StringLike = {
            "ecs:cluster" = aws_ecs_cluster.main.arn
          }
        }
      }
    ]
  })
}

# Note: EventBridge Scheduler to trigger queue ticks via HTTP would require
# API Gateway or direct ECS task invocation. For simplicity, this can be
# implemented as a CloudWatch Event Rule that invokes a Lambda function
# which calls the web service endpoint, or use ECS Scheduled Tasks.
# This is a placeholder for the scheduler implementation.
