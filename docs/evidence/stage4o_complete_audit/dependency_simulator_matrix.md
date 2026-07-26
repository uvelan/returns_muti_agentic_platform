# Dependency Simulator Matrix

Simulator source is isolated from production, but two required operation/state sets are incomplete.

| Dependency | Classification | Module | Mode selection | Deterministic state/IDs | Idempotency | Persistence/history | Temporal signalling | Production protection | Tests | UI | Gap |
|---|---|---|---|---|---|---|---|---|---|---|---|
| OMC | PARTIAL | backend/src/return_platform/dependency_simulation/service.py | PLATFORM_OMC_DEPENDENCY_MODE | Yes | Yes | dependency_simulation_operations | Yes | Yes | backend/tests/test_dependency_simulation.py | /system/dependency-simulator/omc | SET_RETURN_METHOD absent. |
| PARCEL | PARTIAL | backend/src/return_platform/dependency_simulation/service.py | PLATFORM_PARCEL_DEPENDENCY_MODE | Yes | Yes | dependency_simulation_operations | Yes | Yes | backend/tests/test_dependency_simulation.py | /system/dependency-simulator/parcel | PACKAGE_READY/CARRIER_ACCEPTED separation absent. |
| FREIGHT | SIMULATED | backend/src/return_platform/dependency_simulation/service.py | PLATFORM_FREIGHT_DEPENDENCY_MODE | Yes | Yes | dependency_simulation_operations | Yes | Yes | backend/tests/test_dependency_simulation.py | /system/dependency-simulator/freight | Live TMS integration absent by design. |
| LSI | SIMULATED | backend/src/return_platform/dependency_simulation/service.py | PLATFORM_LSI_DEPENDENCY_MODE | Yes | Yes | dependency_simulation_operations | Yes | Yes | backend/tests/test_dependency_simulation.py | /system/dependency-simulator/lsi | Live LSI/reconciliation absent by design. |
