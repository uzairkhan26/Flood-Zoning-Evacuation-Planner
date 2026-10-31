#!/usr/bin/env python3
"""Run the test suite and exit hard (QGIS can crash while shutting down after tests)."""
import os
import sys

import pytest

code = pytest.main(sys.argv[1:] or ['-v', 'tests'])
sys.stdout.flush()
sys.stderr.flush()
os._exit(int(code))
