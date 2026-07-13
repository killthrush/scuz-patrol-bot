output "lambda_function_name" {
  description = "Lambda function name"
  value       = aws_lambda_function.bot.function_name
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
