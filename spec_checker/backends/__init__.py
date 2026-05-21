"""Abstract verifier backend interface."""

from abc import ABC, abstractmethod
from pathlib import Path
from ..types import Verdict


class VerifierBackend(ABC):
    """Backend interface for bounded model checkers."""

    @abstractmethod
    def name(self) -> str:
        """Return the verifier name (e.g. 'cbmc', 'esbmc')."""
        ...

    @abstractmethod
    def check_harness(self, harness_path: Path, timeout: int = 60) -> tuple[Verdict, str | None]:
        """Run the verifier on a harness file.

        Returns:
            (verdict, witness_or_detail)
            - Verdict.PROVED if all assertions hold
            - Verdict.REFUTED if a counterexample was found
            - Verdict.UNKNOWN / ERROR / TIMEOUT otherwise
        """
        ...
