variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
  default     = "fujisan-viewshed"
}

variable "aws_region" {
  description = "AWS region for S3 bucket"
  type        = string
  default     = "ap-northeast-1"
}
