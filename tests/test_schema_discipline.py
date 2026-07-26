"""Package-wide locks for the portable Pydantic schema discipline."""

import importlib
import inspect
import pkgutil
from collections.abc import Iterator

from pydantic import BaseModel

import vibrantine


def _package_models() -> Iterator[type[BaseModel]]:
    seen: set[type[BaseModel]] = set()
    for module_info in pkgutil.walk_packages(
        vibrantine.__path__,
        prefix=f"{vibrantine.__name__}.",
    ):
        parts = module_info.name.split(".")
        if "tests" in parts or parts[-1] == "__main__":
            continue
        module = importlib.import_module(module_info.name)
        for value in vars(module).values():
            if (
                inspect.isclass(value)
                and issubclass(value, BaseModel)
                and value.__module__.startswith("vibrantine.")
                and value not in seen
            ):
                seen.add(value)
                yield value


def test_package_models_describe_every_field_and_stay_within_field_cap() -> None:
    missing_descriptions: list[str] = []
    oversized: list[str] = []

    for model in _package_models():
        if len(model.model_fields) > 20:
            oversized.append(f"{model.__module__}.{model.__name__}")
        for field_name, field in model.model_fields.items():
            if not field.description or not field.description.strip():
                missing_descriptions.append(f"{model.__module__}.{model.__name__}.{field_name}")

    assert missing_descriptions == []
    assert oversized == []
