"""
conftest.py — pytest configuration
====================================
Inserts the project root onto sys.path so that `from src.detect.handler`
and `from src.presign.handler` resolve without installing anything.
"""
import sys
import pathlib

# Project root = directory containing this file
ROOT = pathlib.Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
