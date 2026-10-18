import pytest
from pydantic import ValidationError

from schema.prompts import Mem0Prompts


@pytest.mark.parametrize(
    "value",
    [
        "Missing status",
        "{other}",
        "{status.__class__}",
        "{status!r}",
        "{status:>10}",
        "{status} {status}",
        "{status",
    ],
)
def test_invalid_status_template_is_rejected(mem0_prompts, value: str) -> None:
    with pytest.raises(ValidationError):
        Mem0Prompts.model_validate(mem0_prompts.model_dump() | {"memory_status_template": value})


@pytest.mark.parametrize("field", list(Mem0Prompts.model_fields))
@pytest.mark.parametrize("value", ["", " \n ", None, 42])
def test_prompts_must_be_nonempty_strings(mem0_prompts, field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Mem0Prompts.model_validate(mem0_prompts.model_dump() | {field: value})
