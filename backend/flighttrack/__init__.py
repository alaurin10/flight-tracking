"""flighttrack: the collector and (from later milestones) the API behind Flight Watch."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("flighttrack")  # from pyproject.toml, the one place it is set
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"
