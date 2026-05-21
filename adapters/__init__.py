"""Adapters for converting tool-specific spec formats into spec_checker JSON.

Each adapter produces CompareInput objects (or their JSON dict form) that
can be fed to spec_checker's compare() or batch CLI.
"""
