from typing import Any, Dict

class ConfigurationPrecedenceEvaluator:
    def apply_overrides(self, base_config: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(base_config)
        for key, value in overrides.items():
            if isinstance(value, dict) and key in result and isinstance(result[key], dict):
                result[key] = self.apply_overrides(result[key], value)
            else:
                result[key] = value
        return result
