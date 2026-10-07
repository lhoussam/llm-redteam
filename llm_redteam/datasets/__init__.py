"""Attack prompt datasets, shipped inside the package as YAML files."""
from functools import cache
from pathlib import Path

import yaml

_DATASETS_DIR = Path(__file__).parent


@cache
def load_dataset(name: str) -> dict:
    """Load `<name>.yaml` from this package (read once, then cached)."""
    with open(_DATASETS_DIR / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)
