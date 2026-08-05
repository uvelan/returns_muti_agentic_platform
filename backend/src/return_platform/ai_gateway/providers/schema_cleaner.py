import copy

def clean_gemini_schema(schema: dict) -> dict:
    if not isinstance(schema, dict):
        return schema

    schema_copy = copy.deepcopy(schema)
    defs = schema_copy.pop("$defs", {})

    def resolve_refs(node):
        if isinstance(node, list):
            return [resolve_refs(item) for item in node]
        elif isinstance(node, dict):
            if "$ref" in node:
                ref_path = node["$ref"]
                # Assuming format "#/$defs/Name"
                ref_name = ref_path.split("/")[-1]
                if ref_name in defs:
                    resolved = copy.deepcopy(defs[ref_name])
                    # Merge any other keys if needed, but normally $ref replaces the node
                    return resolve_refs(resolved)
            
            if "anyOf" in node:
                any_of_list = node.pop("anyOf")
                for item in any_of_list:
                    if item.get("type") != "null":
                        # recursively resolve the item first, then update
                        resolved_item = resolve_refs(item)
                        node.update(resolved_item)
                        break

            # Clean unsupported keys
            for key in ["title", "default", "additionalProperties"]:
                node.pop(key, None)

            # Process remaining items
            return {k: resolve_refs(v) for k, v in node.items()}
        return node

    return resolve_refs(schema_copy)
