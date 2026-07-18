# IAM role for Lambda
resource "aws_iam_role" "lambda_role" {
  name = "${var.function_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
}

# IAM policy for CloudWatch logs
resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# IAM policy for Secrets Manager access
resource "aws_iam_role_policy" "lambda_secrets" {
  name = "${var.function_name}-secrets-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "secretsmanager:GetSecretValue"
      ]
      Resource = [
        aws_secretsmanager_secret.discord_token.arn,
        aws_secretsmanager_secret.anthropic_api_key.arn,
        aws_secretsmanager_secret.google_service_account.arn,
      ]
    }]
  })
}

# Secrets Manager secrets (populated via: task set-secrets:dev)
resource "aws_secretsmanager_secret" "discord_token" {
  name                    = "${var.function_name}/discord-token"
  recovery_window_in_days = 7
  description             = "Discord bot token for Scuz Patrol"
}

resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name                    = "${var.function_name}/anthropic-api-key"
  recovery_window_in_days = 7
  description             = "Anthropic API key for Claude"
}

resource "aws_secretsmanager_secret" "google_service_account" {
  name                    = "${var.function_name}/google-service-account"
  recovery_window_in_days = 7
  description             = "Google service account key for Docs API"
}

# ECR repository for Lambda container images
resource "aws_ecr_repository" "bot" {
  name                 = "${var.function_name}-repo"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Lambda function with container image
resource "aws_lambda_function" "bot" {
  function_name = var.function_name
  role          = aws_iam_role.lambda_role.arn
  timeout       = var.timeout
  memory_size   = var.memory_size
  architectures = ["x86_64"]

  image_uri = "${aws_ecr_repository.bot.repository_url}:latest"
  package_type = "Image"

  environment {
    variables = {
      LOG_LEVEL = "INFO"
      # Secrets stored in Lambda environment variables
      # (In production, use Secrets Manager or Parameter Store)
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_logs
  ]
}

# API Gateway to receive Discord webhooks
resource "aws_apigatewayv2_api" "webhook" {
  name          = "${var.function_name}-webhook"
  protocol_type = "HTTP"
}

# API Gateway stage
resource "aws_apigatewayv2_stage" "webhook" {
  api_id      = aws_apigatewayv2_api.webhook.id
  name        = var.environment
  auto_deploy = true
}

# API Gateway integration with Lambda
resource "aws_apigatewayv2_integration" "lambda" {
  api_id           = aws_apigatewayv2_api.webhook.id
  integration_type = "AWS_PROXY"
  integration_method = "POST"
  payload_format_version = "2.0"
  target = aws_lambda_function.bot.arn
}

# API Gateway route
resource "aws_apigatewayv2_route" "webhook" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "POST /webhook"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

# Lambda permission for API Gateway
resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.bot.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.webhook.execution_arn}/*/*"
}

# CloudWatch Log Group (explicit for better control)
resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/${var.function_name}"
  retention_in_days = 7
}
