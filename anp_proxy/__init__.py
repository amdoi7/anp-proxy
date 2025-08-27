"""
Agent Network Proxy (ANP) - HTTP over WebSocket tunneling.

A high-performance, framework-agnostic proxy solution for exposing private
network services through secure WebSocket tunnels.

NOTE: Receiver components have been migrated to the independent octopus project.
For Receiver functionality, please refer to the octopus project documentation.
"""

__version__ = "0.1.0"
__author__ = "ANP Team"

# Lazily expose `main` to avoid importing submodules at package import time
import importlib


def __getattr__(name):
    if name == "main":
        return importlib.import_module(".anp_proxy", __name__).main
    raise AttributeError(f"module {__name__} has no attribute {name}")


__all__ = ["main"]
