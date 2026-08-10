"""Neo Operator package.

Phase 13 keeps this package import-light so Control Center can use the neutral
Operator contracts without importing the execution service (and Memory Engine)
at package import time.
"""

__all__ = [
    "operator_status_payload",
    "plan_operator_actions",
    "run_operator_actions",
    "plan_operator_command",
    "run_operator_command",
]


def __getattr__(name):
    if name in __all__:
        from . import service
        return getattr(service, name)
    raise AttributeError(name)
