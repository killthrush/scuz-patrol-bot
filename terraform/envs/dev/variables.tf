variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "dev"
}

variable "function_name" {
  description = "Lambda function name"
  type        = string
  default     = "scuz-patrol-bot-dev"
}

variable "timeout" {
  description = "Lambda timeout in seconds"
  type        = number
  default     = 300
}

variable "memory_size" {
  description = "Lambda memory in MB"
  type        = number
  default     = 256
}

variable "provisioned_concurrency" {
  description = "Number of provisioned concurrent executions to keep warm (avoids Discord's 3s timeout on cold starts)"
  type        = number
  default     = 1
}

variable "google_doc_id" {
  description = "Google Doc ID for the Scuz Patrol canon compendium"
  type        = string
  default     = "1gJuZ9CBbNz5vQ1xDEDDQRZLI5TyBFGGa4YGvWp1gwgE"
}
