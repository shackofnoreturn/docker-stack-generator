#!/usr/bin/env python3
"""Convenience launcher: ./run.py"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from docker_stack_generator.app import main
main()
