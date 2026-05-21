"""
spec_checker — Formal specification comparison via bounded model checking.

Compares two sets of pre/postconditions (left vs right) for the same function
signature using bounded model checkers (ESBMC or CBMC). Reports precondition
and postcondition implication in both directions, with soundness/completeness
and equivalence verdicts.
"""

__version__ = "0.2.0"
