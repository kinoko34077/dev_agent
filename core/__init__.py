# core/__init__.py

#from .main import main
from .executor import Executor
from .functions_registry import (
    REGISTERED_FUNCTIONS,
    start_internal_dialogue,
    continue_internal_dialogue
)
from .internal_dialogue import InternalDialogue

__all__ = [
    "Executor",
    "REGISTERED_FUNCTIONS",
    "start_internal_dialogue",
    "continue_internal_dialogue",
    "InternalDialogue"
]
