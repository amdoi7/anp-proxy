"""
Agent Network Proxy (ANP) - HTTP over WebSocket tunneling.

A high-performance, framework-agnostic proxy solution for exposing private
network services through secure WebSocket tunnels.
"""

__version__ = "0.1.0"
__author__ = "ANP Team"

from .anp_proxy import ANPProxy

__all__ = ["ANPProxy"]
