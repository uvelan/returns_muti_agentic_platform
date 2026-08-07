import pytest
from pathlib import Path
from return_platform.configuration.application.loader import ConfigurationLoader
from return_platform.configuration.application.validator import ConfigurationValidator, ConfigurationValidationError
from return_platform.configuration.application.precedence import ConfigurationPrecedenceEvaluator
from return_platform.configuration.application.compatibility import LegacyCompatibilityAdapter, build_snapshot_from_legacy_configs
from return_platform.configuration.domain.release import RuntimeSnapshot

def test_loader_and_compatibility():
    config_dir = Path(__file__).resolve().parents[2] / "config"
    adapter = LegacyCompatibilityAdapter(config_dir)
    snapshot = adapter.build_canonical_snapshot()
    
    assert snapshot is not None
    assert isinstance(snapshot, RuntimeSnapshot)
    assert snapshot.modules is not None
    assert snapshot.ai is not None
    assert snapshot.platform is not None
    assert snapshot.graph is not None

def test_validator():
    validator = ConfigurationValidator()
    config_dir = Path(__file__).resolve().parents[2] / "config"
    snapshot = build_snapshot_from_legacy_configs(config_dir)
    
    validator.validate_snapshot(snapshot)

def test_precedence_evaluator():
    evaluator = ConfigurationPrecedenceEvaluator()
    base = {"a": 1, "nested": {"x": 10}}
    override = {"nested": {"y": 20}, "b": 2}
    result = evaluator.apply_overrides(base, override)
    
    assert result == {"a": 1, "nested": {"x": 10, "y": 20}, "b": 2}
