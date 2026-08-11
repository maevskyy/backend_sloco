# Recommendation Service Docs

This folder contains implementation notes and task plans for the Python
recommendation service.

Start here:

```text
TASKS_1_embedding_recommender_endpoint.md
TASKS_2_location_recommender_v4.md
TASKS_3_user_reactions_contract.md
TASKS_4_onboarding_artifact_endpoint.md
TASKS_5_direct_image_similar.md
TASKS_6_v4_activation_coverage_guard.md
```

`TASKS_3` is part 1 of 3 of the cross-service user-reactions feature; the gateway
halves are `services/gateway/docs/tasks/TASKS_34` (storage) and `TASKS_35` (feed).

`TASKS_4`/`TASKS_5` are the rec-service halves of the onboarding feature; the
gateway halves are `services/gateway/docs/tasks/TASKS_38` (complete), `TASKS_39`
(tree, needs `TASKS_4`) and `TASKS_40` (similar, needs `TASKS_5`).

`TASKS_6` is the prod activation of the v4 engine (env flip + startup coverage
guard) — it resolves `../../../recommender-config-audit.md` P0-1/P0-2 and the iOS
`RECOMMENDER_STATUS` ask; part of the cross-service plan in
`../../../ios-asks-implementation-plan.md`.

