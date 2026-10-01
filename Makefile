CLUSTER := stateless-mcp-scale
CTX := kind-$(CLUSTER)
NS := stateless-mcp-scale
AGW_IMAGE := cr.agentgateway.dev/agentgateway:v1.5.0

.PHONY: up build load-images deploy demo rollout-demo fan-out port-check down validate-config

up: build
	kind create cluster --config kind-config.yaml
	$(MAKE) load-images
	kubectl --context $(CTX) apply -f manifests/00-namespace.yaml
	kubectl --context $(CTX) apply -f manifests/10-mcp-server.yaml
	kubectl --context $(CTX) -n $(NS) rollout status deployment/stateless-mcp-demo --timeout=90s
	kubectl --context $(CTX) apply -f agentgateway/deploy.yaml
	kubectl --context $(CTX) -n $(NS) rollout status deployment/agentgateway --timeout=90s
	@echo
	@echo "agentgateway is listening on http://127.0.0.1:38080/mcp"

build:
	docker build -t stateless-mcp-demo:dev server/

load-images:
	kind load docker-image stateless-mcp-demo:dev --name $(CLUSTER)
	-kind load docker-image $(AGW_IMAGE) --name $(CLUSTER)

validate-config:
	docker run --rm -v "$(PWD)/agentgateway:/config" $(AGW_IMAGE) --validate-only -f /config/agentgateway.yaml

demo:
	python3 scripts/demo.py

down:
	kind delete cluster --name $(CLUSTER)
