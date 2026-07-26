# Configuration Matrix

Configuration presence does not count as executable behavior.

| Configuration | Consumed by | Validation | Snapshot/digest | Classification | Gap |
|---|---|---|---|---|---|
| backend/config/returns/production.yaml | load_return_configuration; agents; services | Strict Pydantic | Persisted return_configuration_snapshots | SOURCE_IMPLEMENTED_RUNTIME_UNVERIFIED | Graph freshness and full heavy-equipment policy missing. |
| backend/config/dependency_simulation.yaml | DependencySimulationService | Strict Pydantic + required dependencies | SHA logged | PARTIAL | OMC SET_RETURN_METHOD absent; parcel milestones differ. |
| backend/config/ai_gateway.yaml | AIGatewayService/AIRoutePool | Strict task/limit config | SHA logged | SOURCE_IMPLEMENTED_RUNTIME_UNVERIFIED | Task/session/user limits not modeled. |
| backend/src/return_platform/configuration/settings.py | Application startup | Pydantic validators and production simulation guard | Indirect environment snapshot | SOURCE_IMPLEMENTED_RUNTIME_UNVERIFIED | Legacy single key/model fields coexist with list path. |
| compose.yaml | Compose topology | docker compose config | Image/source state only | PARTIAL | Single configurable SQL credential spans platform tables and potential generic write service. |
