# Build stage
FROM golang:1.26-alpine AS builder
RUN apk add --no-cache git
WORKDIR /src

COPY go.mod go.sum ./
RUN go mod download

COPY . .
ARG TARGETARCH
RUN CGO_ENABLED=0 GOOS=linux GOARCH=${TARGETARCH} go build -ldflags="-s -w" -o /out/cadentra-agent ./cmd/agent

# Runtime
FROM alpine:3.20
RUN apk add --no-cache ca-certificates tzdata bash coreutils procps iproute2 util-linux
COPY --from=builder /out/cadentra-agent /usr/local/bin/cadentra-agent
RUN mkdir -p /var/lib/cadentra
VOLUME ["/var/lib/cadentra"]
ENTRYPOINT ["/usr/local/bin/cadentra-agent"]
