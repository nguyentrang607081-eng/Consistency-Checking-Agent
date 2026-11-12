"""Load, merge and validate architecture rules."""
from pathlib import Path
import yaml
from .schema import ArchitectureRules

def load_rules(path: str | Path | None = None, override: dict | ArchitectureRules | None = None) -> ArchitectureRules:
    data = {}
    if path:
        p = Path(path)
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if isinstance(override, ArchitectureRules):
        override = override.model_dump()
    if override:
        data.update(override)
    rules = ArchitectureRules.model_validate(data)
    if len(rules.layers) != len(set(rules.layers)) or any(not x.strip() for x in rules.layers):
        raise ValueError("rules.layers must contain unique, non-empty layer names")
    return rules

def allowed_edge_set(rules: ArchitectureRules) -> set[tuple[str, str]]:
    return {(x["source"], x["target"]) for x in rules.allowed_edges
            if x.get("source") and x.get("target")}

def layer_of(node, rules: ArchitectureRules) -> str | None:
    if node.id in rules.node_layers:
        return rules.node_layers[node.id]
    explicit = node.properties.get("layer")
    if explicit:
        return str(explicit)
    prefix = node.id.split(".", 1)[0] if "." in node.id else None
    return prefix if prefix in rules.layers else None

def module_of(node_id: str, rules: ArchitectureRules) -> str | None:
    if node_id in rules.node_modules:
        return rules.node_modules[node_id]
    for item in rules.modules:
        if node_id in item.get("members", []):
            return str(item.get("name"))
    return None
