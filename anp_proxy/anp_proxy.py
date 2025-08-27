"""
Deprecated compatibility shim. CLI moved to `anp_proxy.cli`.
This file remains temporarily to avoid breaking existing imports and docs
that refer to `anp_proxy.anp_proxy:main` or `python -m anp_proxy.anp_proxy`.
"""

import importlib
from warnings import warn


def __getattr__(name: str):
    if name == "main":
        warn(
            "anp_proxy.anp_proxy is deprecated; use `python -m anp_proxy` or the"
            " installed console script `anp-proxy`. For direct imports, use"
            " `anp_proxy.cli:main`.",
            DeprecationWarning,
            stacklevel=2,
        )
        module = importlib.import_module(".cli", __package__)
        return getattr(module, "main")  # type: ignore[attr-defined]
    raise AttributeError(f"module {__name__} has no attribute {name}")


if __name__ == "__main__":
    warn(
        "Running as `python -m anp_proxy.anp_proxy` is deprecated; use"
        " `python -m anp_proxy` or the console script `anp-proxy`.",
        DeprecationWarning,
        stacklevel=2,
    )
    module = importlib.import_module(".cli", __package__)
    getattr(module, "main")()  # type: ignore[attr-defined]
