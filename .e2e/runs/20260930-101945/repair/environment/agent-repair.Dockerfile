ARG REPAIR_BASE_IMAGE
FROM ${REPAIR_BASE_IMAGE}

COPY nodesteer-agent /usr/local/bin/nodesteer-agent

ARG REPAIR_AGENT_SHA
ARG REPAIR_BUILD_LABEL
LABEL org.nodesteer.repair.agent-sha256=${REPAIR_AGENT_SHA}
LABEL org.nodesteer.repair.build-label=${REPAIR_BUILD_LABEL}

ENTRYPOINT ["/usr/local/bin/nodesteer-agent"]
