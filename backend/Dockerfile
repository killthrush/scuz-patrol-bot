ARG PYTHON_VERSION="3.11"
FROM public.ecr.aws/lambda/python:${PYTHON_VERSION}

ENV AWS_DEFAULT_REGION=us-east-1
ENV AWS_REGION=us-east-1
ENV AWS_ACCESS_KEY_ID=
ENV AWS_SECRET_ACCESS_KEY=
ENV AWS_SESSION_TOKEN=

COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install -r requirements.txt
COPY ./src ${LAMBDA_TASK_ROOT}/src

# Exec-form ENTRYPOINT (not baked-in shell form) so Lambda's per-function
# image_config.command can override the handler -- this is how the
# reconstruct Lambda points at a different handler while sharing this same
# image with the main bot. CMD is the default handler for local `docker run`
# and for any Lambda function that doesn't set image_config.command.
ENTRYPOINT ["/lambda-entrypoint.sh"]
CMD ["src.handler.lambda_handler"]
