"""Kinea data collector — a vintage-aware, idempotent collector for ECB SDMX series.

The package is intentionally dependency-free (Python standard library only) so the
collector can be read and run without reconstructing an environment. The Streamlit
dashboard (Part B) lives outside this package and has its own requirements.
"""

__version__ = "2.2.0"
