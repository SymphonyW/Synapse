ARG PYTHON_BASE=docker.io/library/python:3.12-slim

FROM ${PYTHON_BASE}

WORKDIR /app

COPY services/ai-engine-py/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY proto ./proto
COPY scripts/post_gen.py ./scripts/post_gen.py
COPY services/ai-engine-py/app ./app

RUN mkdir -p synapse \
    && python -m grpc_tools.protoc -I proto \
         --python_out=. \
         --grpc_python_out=. \
       proto/synapse/v1/agent.proto \
     && python scripts/post_gen.py synapse

ENV PYTHONUNBUFFERED=1
EXPOSE 50051

CMD ["python", "-m", "app.main"]
