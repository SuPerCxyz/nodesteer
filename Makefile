.PHONY: build build-hub build-agent web web-build test lint docker docker-hub docker-agent clean

# 构建二进制
build: build-agent build-hub

build-hub: build-agent
	go build -buildvcs=false -o bin/nodesteer-hub.unbundled ./cmd/hub
	sh packaging/bundle-agent.sh bin/nodesteer-hub bin/nodesteer-hub.unbundled amd64 bin/nodesteer-agent

build-agent:
	go build -buildvcs=false -o bin/nodesteer-agent ./cmd/agent

# Web 前端
web:
	cd web && npm install

web-build:
	cd web && npm run build

# 测试
test:
	go test -buildvcs=false ./...

test-verbose:
	go test -buildvcs=false -v ./...

lint:
	go vet -buildvcs=false ./...

# Docker
docker: docker-hub docker-agent

docker-hub:
	docker build -t nodesteer/hub:latest -f packaging/docker/hub/Dockerfile .

docker-agent:
	docker build -t nodesteer/agent:latest -f packaging/docker/agent/Dockerfile .

clean:
	rm -rf bin web/dist
