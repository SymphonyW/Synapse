PROTO_FILE=proto/synapse/v1/agent.proto
GO_OUT=services/gateway-go/internal/gen
PY_OUT=services/ai-engine-py

.PHONY: proto proto-go proto-py gateway ai web up down

proto: proto-go proto-py

proto-go:
	protoc -I proto --go_out=$(GO_OUT) --go_opt=paths=source_relative --go-grpc_out=$(GO_OUT) --go-grpc_opt=paths=source_relative $(PROTO_FILE)

proto-py:
	python -m grpc_tools.protoc -I proto --python_out=$(PY_OUT) --grpc_python_out=$(PY_OUT) $(PROTO_FILE)
	python scripts/post_gen.py $(PY_OUT)/synapse

gateway:
	cd services/gateway-go && go run ./cmd/server

ai:
	cd services/ai-engine-py && python -m app.main

web:
	cd apps/web && npm run dev

up:
	docker compose up --build

down:
	docker compose down
