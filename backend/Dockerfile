ARG PYTHON_VERSION="3.11"
FROM public.ecr.aws/lambda/python:${PYTHON_VERSION}

ENV ENTRY_POINT="src.handler.lambda_handler"
ENV AWS_DEFAULT_REGION=us-east-1
ENV AWS_REGION=us-east-1
ENV AWS_ACCESS_KEY_ID=
ENV AWS_SECRET_ACCESS_KEY=
ENV AWS_SESSION_TOKEN=

COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install -r requirements.txt
COPY ./src ${LAMBDA_TASK_ROOT}/src
ENTRYPOINT /lambda-entrypoint.sh ${ENTRY_POINT}
