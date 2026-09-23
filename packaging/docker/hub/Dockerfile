# Build the embedded frontend with the repository's locked pnpm dependency graph.
FROM node:22-alpine AS web-builder
WORKDIR /src/web
RUN npm install --global pnpm@10.6.5
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm run build

# Build the Hub with the frontend dist available for go:embed.
FROM golang:1.26-alpine AS builder
RUN apk add --no-cache git
WORKDIR /src

COPY go.mod go.sum ./
RUN go mod download

COPY . .
COPY --from=web-builder /src/web/dist ./web/dist
ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=linux GOARCH=${TARGETARCH} go build -ldflags="-s -w" -o /out/nodesteer-hub.unbundled ./cmd/hub \
    && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -ldflags="-s -w" -o /out/nodesteer-agent-amd64 ./cmd/agent \
    && CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -ldflags="-s -w" -o /out/nodesteer-agent-arm64 ./cmd/agent \
    && sh packaging/bundle-agent.sh /out/nodesteer-hub /out/nodesteer-hub.unbundled amd64 /out/nodesteer-agent-amd64 arm64 /out/nodesteer-agent-arm64

# Runtime
FROM alpine:3.20
RUN apk add --no-cache ca-certificates tzdata
COPY --from=builder /out/nodesteer-hub /usr/local/bin/nodesteer-hub
RUN mkdir -p /var/lib/nodesteer-hub /var/lib/nodesteer-hub/artifacts
EXPOSE 8080 8443
VOLUME ["/var/lib/nodesteer-hub"]
ENTRYPOINT ["/usr/local/bin/nodesteer-hub"]
