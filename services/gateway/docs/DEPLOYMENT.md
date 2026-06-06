# Gateway Deployment

Gateway deployment is owned by the backend monorepo, not by this service folder.

Use the root deployment doc:

```text
../../../docs/DEPLOYMENT.md
```

The production compose service is still named:

```text
backend
```

The code lives in:

```text
services/gateway/
```

The production Docker image is:

```text
ghcr.io/maevskyy/backend_sloco:<tag>
```

Do not add a second service-local deploy flow here. Service-local docs should
cover Gateway code behavior, API contracts, migrations, scripts, and local
development only.
