"""The exported JSON Schema is usable as structured output and accepts the examples."""
import json

import jsonschema
import pytest

from conftest import NAMES, example
from hephaestus.spec import json_schema, schema_depth


def walk(node):
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


def test_no_refs_and_shallow():
    schema = json_schema()
    keys = {k for n in walk(schema) if isinstance(n, dict) for k in n}
    assert "$ref" not in keys and "$defs" not in keys
    assert "oneOf" not in keys and "discriminator" not in keys
    assert schema_depth(schema) <= 10
    json.dumps(schema)


@pytest.mark.parametrize("name", NAMES)
def test_examples_validate_against_the_exported_schema(name):
    jsonschema.validate(example(name), json_schema())


def test_schema_rejects_a_raw_selector_string():
    spec = example("block")
    spec["features"][4]["edges"] = "|Z"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(spec, json_schema())
