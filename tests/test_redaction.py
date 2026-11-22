"""Demo 4: secret/PII patterns are refused at write time, storing nothing."""

import pytest

from contextvault import ContextVault, RedactionError


@pytest.fixture()
def vault(tmp_path):
    v = ContextVault(tmp_path / "test.db")
    yield v
    v.close()


@pytest.mark.parametrize("value,fired", [
    ({"aws_access_key_id": "AKIAIOSFODNN7EXAMPLE"}, "aws_access_key_id"),
    ("token is ghp_0123456789abcdefghijklmnopqrstuv", "github_token"),
    ("-----BEGIN PRIVATE KEY-----\nMIIB...\n-----END PRIVATE KEY-----",
     "private_key_block"),
    ("Authorization: Bearer abcdef0123456789abcdef", "bearer_token"),
    ("api_key = sk-live-99887766", "secret_assignment"),
    ({"patient_ssn": "123-45-6789"}, "ssn"),
    # Nested inside a list inside a dict: serialization-level screening.
    ({"notes": ["all normal", {"leak": "AKIAIOSFODNN7EXAMPLE"}]},
     "aws_access_key_id"),
])
def test_secret_writes_are_refused(vault, value, fired):
    with pytest.raises(RedactionError) as excinfo:
        vault.write("k", value, agent="deploy-agent", source_event="run-1")
    assert fired in excinfo.value.patterns
    # The refusal never echoes the matched secret text.
    assert "AKIA" not in str(excinfo.value)
    # Nothing was stored -- not even the key.
    assert vault.keys() == []
    with pytest.raises(KeyError):
        vault.read("k")


def test_refusal_does_not_block_later_clean_write(vault):
    with pytest.raises(RedactionError):
        vault.write("k", "password: hunter2-hunter2", agent="a",
                    source_event="e1")
    entry = vault.write("k", "clean value", agent="a", source_event="e2")
    assert entry.version == 1  # the refused write consumed no version
    assert vault.read("k").value == "clean value"


def test_ordinary_values_pass_screening(vault):
    vault.write("facts", {"pump": "P-114", "pressure_psi": 41,
                          "note": "key rotated on schedule"},
                agent="a", source_event="e1")
    assert vault.read("facts").value["pressure_psi"] == 41
