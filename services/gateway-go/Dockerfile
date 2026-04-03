ARG GOLANG_BASE=docker.io/library/golang:1.25
ARG RUNTIME_BASE=gcr.io/distroless/static-debian12:latest
ARG GOPROXY=https://goproxy.cn,direct
ARG GOSUMDB=sum.golang.google.cn

FROM ${GOLANG_BASE} AS builder

ARG GOPROXY
ARG GOSUMDB
ENV GOPROXY=${GOPROXY}
ENV GOSUMDB=${GOSUMDB}

WORKDIR /src

RUN apt-get update \
    && apt-get install -y --no-install-recommends protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

RUN go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.11 \
    && go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.5.1

COPY proto ./proto
COPY services/gateway-go ./services/gateway-go

RUN mkdir -p services/gateway-go/internal/gen
RUN protoc -I proto \
    --go_out=services/gateway-go/internal/gen --go_opt=paths=source_relative \
    --go-grpc_out=services/gateway-go/internal/gen --go-grpc_opt=paths=source_relative \
    proto/synapse/v1/agent.proto

WORKDIR /src/services/gateway-go
RUN go mod download
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o /bin/gateway ./cmd/server

FROM ${RUNTIME_BASE}
COPY --from=builder /bin/gateway /gateway
EXPOSE 8080
ENTRYPOINT ["/gateway"]
