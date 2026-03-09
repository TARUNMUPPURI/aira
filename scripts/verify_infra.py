import yaml, os, sys

files = {
    "single": ["docker-compose.yml", "k8s/configmap.yaml"],
    "multi":  ["k8s/deployment.yaml", "k8s/service.yaml"],
}

all_ok = True
for f in files["single"]:
    try:
        doc = yaml.safe_load(open(f, encoding="utf-8").read())
        assert doc is not None
        print(f"YAML OK (single-doc): {f}")
    except Exception as e:
        print(f"FAIL: {f} — {e}")
        all_ok = False

for f in files["multi"]:
    try:
        docs = list(yaml.safe_load_all(open(f, encoding="utf-8").read()))
        assert all(d is not None for d in docs), "null document"
        print(f"YAML OK (multi-doc, {len(docs)} docs): {f}")
    except Exception as e:
        print(f"FAIL: {f} — {e}")
        all_ok = False

# Dockerfile is not YAML — just check it exists and has key instructions
with open("Dockerfile", encoding="utf-8") as fh:
    content = fh.read()
for keyword in ["FROM python:3.11-slim", "COPY requirements.txt", "EXPOSE 8000 50051 8501", "CMD"]:
    assert keyword in content, f"Missing in Dockerfile: {keyword}"
print("Dockerfile OK: all required instructions present")

# docker-compose service names
dc = yaml.safe_load(open("docker-compose.yml", encoding="utf-8").read())
services = set(dc["services"].keys())
required = {"zookeeper", "kafka", "aria-api", "aria-grpc", "aria-dashboard"}
assert required == services, f"Service mismatch: got {services}"
print(f"docker-compose services OK: {sorted(services)}")

# ports
assert "9092:9092" in str(dc["services"]["kafka"]["ports"])
assert "8000:8000" in str(dc["services"]["aria-api"]["ports"])
assert "50051:50051" in str(dc["services"]["aria-grpc"]["ports"])
assert "8501:8501" in str(dc["services"]["aria-dashboard"]["ports"])
print("docker-compose ports OK: 2181/9092/8000/50051/8501")

# healthcheck on aria-api
assert "healthcheck" in dc["services"]["aria-api"]
print("docker-compose healthcheck OK: aria-api has healthcheck")

# configmap keys
cm = yaml.safe_load(open("k8s/configmap.yaml", encoding="utf-8").read())
cm_keys = set(cm["data"].keys())
required_keys = {"KAFKA_BOOTSTRAP_SERVERS", "CHROMA_PERSIST_DIR",
                 "RISK_LOW_THRESHOLD", "RISK_HIGH_THRESHOLD", "LOG_LEVEL"}
assert required_keys == cm_keys, f"ConfigMap key mismatch: {cm_keys}"
print(f"ConfigMap keys OK: {sorted(cm_keys)}")

# k8s deployments
deploys = list(yaml.safe_load_all(open("k8s/deployment.yaml", encoding="utf-8").read()))
deploy_names = [d["metadata"]["name"] for d in deploys if d.get("kind") == "Deployment"]
assert "aria-api"       in deploy_names, f"aria-api Deployment missing: {deploy_names}"
assert "aria-dashboard" in deploy_names, f"aria-dashboard Deployment missing: {deploy_names}"
api_dep = next(d for d in deploys if d.get("metadata", {}).get("name") == "aria-api")
dash_dep = next(d for d in deploys if d.get("metadata", {}).get("name") == "aria-dashboard")
assert api_dep["spec"]["replicas"] == 2
assert dash_dep["spec"]["replicas"] == 2
print(f"k8s Deployments OK: {deploy_names} (both 2 replicas)")

# k8s services
svcs = list(yaml.safe_load_all(open("k8s/service.yaml", encoding="utf-8").read()))
svc_names = [s["metadata"]["name"] for s in svcs if s.get("kind") == "Service"]
assert "aria-api-service"       in svc_names
assert "aria-dashboard-service" in svc_names
for svc in svcs:
    assert svc["spec"]["type"] == "LoadBalancer"
    port = svc["spec"]["ports"][0]
    assert port["port"] == 80
print(f"k8s Services OK: {svc_names} (both LoadBalancer port 80)")

print("\nALL CHECKS PASSED")
