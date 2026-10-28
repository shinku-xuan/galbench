"""Official Harness protocol adapters; the process lifecycle lives in clients."""
from . import claude_kimi, opencode_deepseek, qoder_workbuddy

MODULES = (claude_kimi, opencode_deepseek, qoder_workbuddy)
SPECS = {key:value for module in MODULES for key,value in module.SPECS.items()}


def adapter(provider):
    return next(module for module in MODULES if provider in module.SPECS)
