# RepOS™ — Multi-Panel REPL-Based Developer Command Environment
# Copyright (c) 2025
# TriFactoria (Andrew Blankfield)
#
# Licensed under the Business Source License 1.1 (BSL 1.1).
# You may use, modify, and redistribute this file under the terms of the BSL.
# On the Change Date (2029-01-01), this file will be licensed under
# the Apache License, Version 2.0.

"""
RepOS core package.

The implementation in this bootstrap is intentionally minimal. The test suite
in ``tests/`` defines the behavior that must be implemented.
"""
from .kernel import Kernel as Kernel  # noqa: F401 (re-export)
