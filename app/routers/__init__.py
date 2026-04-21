"""
Router modules for Russian tax declaration system.
"""

from . import projects
from . import import_data
from . import operations
from . import tax
from . import export
from . import audit
from . import wizard

__all__ = [
    "projects",
    "import_data",
    "operations",
    "tax",
    "export",
    "audit",
    "wizard",
]
