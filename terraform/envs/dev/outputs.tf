output "lambda_function" {
  description = "Lambda function name and other details"
  value = {
    name = aws_lambda_function.bot.function_name
    arn  = aws_lambda_function.bot.arn
  }
}

output "image_repo" {
  description = "ECR repository for Lambda container images"
  value = {
    url = aws_ecr_repository.bot.repository_url
    arn = aws_ecr_repository.bot.arn
  }
}

output "webhook_url" {
  description = "Discord webhook URL (use as Interactions Endpoint URL in bot settings)"
  value       = "${aws_apigatewayv2_stage.webhook.invoke_url}/webhook"
}

output "webhook_api_id" {
  description = "API Gateway API ID"
  value       = aws_apigatewayv2_api.webhook.id
}

output "log_group_name" {
  description = "CloudWatch Log Group name"
  value       = aws_cloudwatch_log_group.lambda_logs.name
}

output "manifest_bucket" {
  description = "S3 bucket storing the Suno scrape manifest"
  value       = aws_s3_bucket.manifest.bucket
}

output "facts_table" {
  description = "DynamoDB table storing atomic lore facts"
  value       = aws_dynamodb_table.facts.name
}

output "song_queue" {
  description = "SQS FIFO queue for per-song ingest processing"
  value       = aws_sqs_queue.song_ingest.url
}
