# Kubernetes development environment

This directory defines the LAN-accessible AdaptSG development and test environment at
`https://sim-next.lab.packetcraft.dev`.

## Design

- `bootstrap-application.yaml` is the only manifest applied manually. It creates the tracked
  `adaptsg` AppProject and `adaptsg-dev` Application from `infra/kubernetes/argocd/`.
- Argo CD continuously reconciles `infra/kubernetes/dev/` into `adaptsg-dev`.
- One Streamlit replica is intentional. Streamlit isolates `st.session_state` per browser
  WebSocket while a single replica avoids cross-replica in-memory session loss.
- The source and virtual environment live on a 5 GiB Longhorn volume. The init container
  checks out `main`; a pod restart refreshes it to the latest remote commit.
- The app runs in deterministic `ADAPTSG_MODE=demo`. No provider credentials are stored in
  Git or the cluster manifests.
- Python `debugpy` listens on pod port 5678, but no Service or Ingress exposes that port.
- A diagnostics sidecar supplies `curl`, DNS, socket, route, packet, and process tools. The
  pod shares its process namespace so the sidecar can inspect the app process.
- Node is copied into the app container at startup, and the project is installed editable
  with its UI and development extras, so the complete `scripts/check.sh` gate runs against
  the checked-out source in one container.
- The pod receives no Kubernetes API token. The diagnostics sidecar has only the three
  capabilities required for packet, route, and cross-process inspection; it has no host
  network, host PID, privileged mode, or LAN route.

## Bootstrap and status

```sh
kubectl apply -f infra/kubernetes/bootstrap-application.yaml
kubectl wait --for=jsonpath='{.status.health.status}'=Healthy \
  application/adaptsg-dev -n argocd --timeout=10m
kubectl get application adaptsg-bootstrap adaptsg-dev -n argocd
kubectl get pods,service,pvc -n adaptsg-dev
```

The wildcard LAN DNS record and Traefik's default wildcard TLS certificate already cover
`sim-next.lab.packetcraft.dev`.

## Test and debug

Run the full repository gate inside the app container:

```sh
kubectl exec -n adaptsg-dev deploy/adaptsg-dev -c app -- \
  /bin/sh -lc 'cd /workspace/source && ./scripts/check.sh'
```

Use the diagnostics sidecar without exposing a toolbox to the LAN:

```sh
kubectl exec -it -n adaptsg-dev deploy/adaptsg-dev -c diagnostics -- /bin/sh
```

Attach a local debugger through a temporary, authenticated Kubernetes tunnel:

```sh
kubectl port-forward -n adaptsg-dev deploy/adaptsg-dev 5678:5678
```

Configure the IDE for Python attach at `127.0.0.1:5678`. The app does not pause waiting for
the debugger, so the shared LAN UI remains usable.

To update the working tree without recreating the pod:

```sh
kubectl exec -n adaptsg-dev deploy/adaptsg-dev -c app -- \
  /bin/sh -lc 'cd /workspace/source && git pull --ff-only origin main'
```

Streamlit watches the source tree and reruns when files change. A pod restart also performs
a checkout of the latest `main` revision while preserving the volume. The init container
refuses to overwrite uncommitted tracked edits; commit or copy those edits before restarting.
