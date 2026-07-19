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
        aws_secretsmanager_secret.discord_public_key.arn,
        aws_secretsmanager_secret.discord_application_id.arn,
      ]
    }]
  })
}

# IAM policy allowing the Lambda to asynchronously invoke itself
# (used to process slash commands outside Discord's 3s response window)
resource "aws_iam_role_policy" "lambda_self_invoke" {
  name = "${var.function_name}-self-invoke-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = [aws_lambda_function.bot.arn]
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

resource "aws_secretsmanager_secret" "discord_public_key" {
  name                    = "${var.function_name}/discord-public-key"
  recovery_window_in_days = 7
  description             = "Discord public key for interaction signature verification"
}

resource "aws_secretsmanager_secret" "discord_application_id" {
  name                    = "${var.function_name}/discord-application-id"
  recovery_window_in_days = 7
  description             = "Discord application ID for follow-up webhook calls"
}

# S3 bucket for the Suno scrape manifest (clip_id -> cached comment ids etc).
# Lambda's /tmp isn't durable across invocations, so this is the only
# reliable shared state for detecting new songs/comments between refreshes.
resource "aws_s3_bucket" "manifest" {
  bucket_prefix = "${var.function_name}-manifest-"
  force_destroy = true
}

# IAM policy for the manifest bucket
resource "aws_iam_role_policy" "lambda_manifest_bucket" {
  name = "${var.function_name}-manifest-bucket-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = ["${aws_s3_bucket.manifest.arn}/*"]
      },
      {
        # GetObject on a nonexistent key returns AccessDenied instead of
        # NoSuchKey without this -- needed on first run before manifest.json exists.
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [aws_s3_bucket.manifest.arn]
      }
    ]
  })
}

# DynamoDB table: the durable ledger of atomic lore facts the canon doc gets
# periodically reconstructed from. Facts are append-only -- a retcon
# supersedes an old fact rather than overwriting it, so a bad doc rewrite
# can never destroy the underlying source of truth.
resource "aws_dynamodb_table" "facts" {
  name         = "${var.function_name}-facts"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "fact_id"

  attribute {
    name = "fact_id"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  attribute {
    name = "ingested_at"
    type = "S"
  }

  # Lets the rebuild job cheaply pull "all pending facts, oldest first"
  # without a full table scan.
  global_secondary_index {
    name            = "status-ingested_at-index"
    hash_key        = "status"
    range_key       = "ingested_at"
    projection_type = "ALL"
  }
}

# IAM policy for the facts table
resource "aws_iam_role_policy" "lambda_facts_table" {
  name = "${var.function_name}-facts-table-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "dynamodb:PutItem",
        "dynamodb:GetItem",
        "dynamodb:UpdateItem",
        "dynamodb:Query",
      ]
      Resource = [
        aws_dynamodb_table.facts.arn,
        "${aws_dynamodb_table.facts.arn}/index/*",
      ]
    }]
  })
}

# Dead-letter queue for songs that fail to process repeatedly (e.g. a
# persistently erroring Suno response) -- keeps a poison message from
# retrying forever and eating into the queue's processing capacity.
resource "aws_sqs_queue" "song_ingest_dlq" {
  name       = "${var.function_name}-song-ingest-dlq.fifo"
  fifo_queue = true
}

# FIFO queue with every message in a single group ("songs") so Lambda only
# ever processes one song at a time, in enqueue order -- deliberately gentle
# on Suno's API instead of the old in-process ThreadPoolExecutor + retry loop,
# since Suno appears to rate-limit/flag whole IP ranges rather than just
# high request volume from a single caller.
resource "aws_sqs_queue" "song_ingest" {
  name                       = "${var.function_name}-song-ingest.fifo"
  fifo_queue                 = true
  visibility_timeout_seconds = var.timeout

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.song_ingest_dlq.arn
    maxReceiveCount     = 3
  })
}

# Batch size 1 + a single message group already serializes processing, so no
# scaling_config is needed to additionally cap concurrency.
resource "aws_lambda_event_source_mapping" "song_ingest" {
  event_source_arn = aws_sqs_queue.song_ingest.arn
  function_name    = aws_lambda_function.bot.arn
  batch_size       = 1
}

# IAM policy for the song ingest queue
resource "aws_iam_role_policy" "lambda_song_queue" {
  name = "${var.function_name}-song-queue-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "sqs:SendMessage",
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes",
      ]
      Resource = [aws_sqs_queue.song_ingest.arn]
    }]
  })
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
  publish       = true

  image_uri    = "${aws_ecr_repository.bot.repository_url}:latest"
  package_type = "Image"

  environment {
    variables = {
      LOG_LEVEL       = "INFO"
      GOOGLE_DOC_ID   = var.google_doc_id
      MANIFEST_BUCKET = aws_s3_bucket.manifest.bucket
      FACTS_TABLE     = aws_dynamodb_table.facts.name
      SONG_QUEUE_URL  = aws_sqs_queue.song_ingest.url
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_logs
  ]
}

# Alias that Discord's webhook actually calls. Deploys move this alias to
# point at the newly published version so provisioned concurrency below
# stays warm across code updates (see task deploy:backend:dev).
resource "aws_lambda_alias" "live" {
  name             = "live"
  function_name    = aws_lambda_function.bot.function_name
  function_version = aws_lambda_function.bot.version

  lifecycle {
    ignore_changes = [function_version]
  }
}

# Keeps one instance warm so the initial Discord interaction response
# (required within 3s) doesn't get stuck behind a container cold start.
resource "aws_lambda_provisioned_concurrency_config" "live" {
  function_name                     = aws_lambda_function.bot.function_name
  qualifier                         = aws_lambda_alias.live.name
  provisioned_concurrent_executions = var.provisioned_concurrency
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

# API Gateway integration with Lambda (targets the "live" alias so
# provisioned concurrency applies to incoming webhook traffic)
resource "aws_apigatewayv2_integration" "lambda" {
  api_id           = aws_apigatewayv2_api.webhook.id
  integration_type = "AWS_PROXY"
  integration_method = "POST"
  payload_format_version = "2.0"
  integration_uri = aws_lambda_alias.live.invoke_arn
}

# API Gateway route
resource "aws_apigatewayv2_route" "webhook" {
  api_id    = aws_apigatewayv2_api.webhook.id
  route_key = "POST /webhook"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

# Lambda permission for API Gateway (granted on the alias, matching the integration above)
resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.bot.function_name
  qualifier     = aws_lambda_alias.live.name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.webhook.execution_arn}/*/*"
}

# CloudWatch Log Group (explicit for better control)
resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/${var.function_name}"
  retention_in_days = 7
}
