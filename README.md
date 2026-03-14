# noip
kubectl apply -f configmap.yaml
kubectl apply -f secret.yaml
kubectl rollout restart deployment/noip-ddns-updater
kubectl logs -f deploy/noip-ddns-updater
