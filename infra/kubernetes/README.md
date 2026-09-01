# Kubernetes development environment

The cluster deployment is intentionally maintained in the dedicated GitOps repository rather
than duplicated here:

- Repository: `BarneyLaw/homelab-cicd-config`
- Application manifests: `apps/adaptsg-dev/`
- Argo CD bootstrap: `argocd/adaptsg-dev.yaml`
- LAN route: `https://sim-next.lab.packetcraft.dev`

The homelab configuration creates a dedicated `adaptsg` AppProject and `adaptsg-dev`
Application. It provides Python 3.12, the full editable development dependencies, Node 22,
localhost-only debugpy, an unexposed diagnostics sidecar, and a prune-protected Longhorn
workspace.

Run the full repository gate inside the deployed app container:

```sh
kubectl exec -n adaptsg-dev deploy/adaptsg-dev -c app -- ./scripts/check.sh
```

Use network and process diagnostics:

```sh
kubectl exec -it -n adaptsg-dev deploy/adaptsg-dev -c diagnostics -- /bin/sh
```

Attach the localhost-only Python debugger through Kubernetes:

```sh
kubectl port-forward -n adaptsg-dev deploy/adaptsg-dev 5678:5678
```

Streamlit provides independent state per browser WebSocket session. The pod has one shared
filesystem for Kubernetes users with `pods/exec`; it is not a per-developer operating-system
isolation boundary.
