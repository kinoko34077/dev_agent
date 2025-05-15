# core/__init__.py

#from .main import main
from .executor import Executor
from .functions_registry import FUNCTIONS, recursion_flag, trigger_recursion
from .recursion_manager import RecursionManager

__all__ = [
    "main",
    "Executor",
    "FUNCTIONS",
    "recursion_flag",
    "trigger_recursion",
    "RecursionManager"
]
