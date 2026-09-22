"""Contract tests: the committed OpenAPI snapshot matches the code, and real
responses validate against the schemas it declares."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import signed_path, upload

SNAPSHOT = Path(__file__).resolve().parents[2] / "openapi" / "openapi.yaml"


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return yaml.safe_load(SNAPSHOT.read_text())  # type: ignore[no-any-return]


def validate(spec: dict[str, Any], schema_name: str, instance: Any) -> None:
    schema = {**spec["components"]["schemas"][schema_name], "components": spec["components"]}
    resolver_schema = {"$ref": f"#/components/schemas/{schema_name}", "components": spec["components"]}
    jsonschema.Draft202012Validator(resolver_schema).validate(instance)
    assert schema  # keep the direct lookup so a renamed schema fails loudly


def response_schema(spec: dict[str, Any], path: str, method: str, status: int) -> str:
    ref = spec["paths"][path][method]["responses"][str(status)]["content"]["application/json"]["schema"]["$ref"]
    return str(ref.rsplit("/", 1)[1])


def test_snapshot_matches_generated_spec(app: FastAPI, spec: dict[str, Any]) -> None:
    generated = app.openapi()
    assert generated == spec, "openapi/openapi.yaml is stale: run `make openapi` and commit the result"


def test_every_operation_documents_the_error_envelope(spec: dict[str, Any]) -> None:
    for path, operations in spec["paths"].items():
        if path.startswith("/health"):
            continue
        for method, op in operations.items():
            errors = [code for code in op["responses"] if code.startswith(("4", "5"))]
            assert errors, f"{method.upper()} {path} declares no error responses"
            for code in errors:
                ref = op["responses"][code]["content"]["application/json"]["schema"]["$ref"]
                assert ref.endswith("/ErrorResponse"), f"{method.upper()} {path} {code} does not use ErrorResponse"


def test_responses_validate_against_declared_schemas(
    client: TestClient, alice: dict[str, str], spec: dict[str, Any]
) -> None:
    created = upload(client, alice, b"contract", "c.txt")
    validate(spec, response_schema(spec, "/v1/files", "post", 201), created.json())
    file_id = created.json()["id"]

    validate(spec, response_schema(spec, "/v1/files", "get", 200), client.get("/v1/files", headers=alice).json())
    validate(
        spec,
        response_schema(spec, "/v1/files/{file_id}", "get", 200),
        client.get(f"/v1/files/{file_id}", headers=alice).json(),
    )

    link = client.post(f"/v1/files/{file_id}/links", headers=alice, json={"ttl_seconds": 60})
    validate(spec, response_schema(spec, "/v1/files/{file_id}/links", "post", 201), link.json())
    client.get(signed_path(link.json()["url"]))

    audit = client.get(f"/v1/files/{file_id}/audit", headers=alice)
    validate(spec, response_schema(spec, "/v1/files/{file_id}/audit", "get", 200), audit.json())

    validate(spec, "HealthResponse", client.get("/health/ready").json())
    validate(spec, "HealthResponse", client.get("/health/live").json())

    for response in (
        client.get("/v1/files"),
        client.get("/v1/files/nope", headers=alice),
        client.get("/v1/files?limit=0", headers=alice),
        client.post(f"/v1/files/{file_id}/links", headers=alice, json={"ttl_seconds": 999_999}),
        client.get(f"/v1/download/{file_id}?exp=1&lid=x&kid=k1&sig={'A' * 43}"),
    ):
        assert response.status_code >= 400
        validate(spec, "ErrorResponse", response.json())


def test_security_scheme_documented(spec: dict[str, Any]) -> None:
    upload_params = spec["paths"]["/v1/files"]["post"]["parameters"]
    names = {p["name"] for p in upload_params}
    assert {"X-API-Key", "Idempotency-Key"} <= names
    download_params = {p["name"] for p in spec["paths"]["/v1/download/{file_id}"]["get"]["parameters"]}
    assert download_params == {"file_id", "exp", "lid", "kid", "sig"}
