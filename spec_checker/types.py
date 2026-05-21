"""Core data types for spec_checker."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Verdict(Enum):
    PROVED = "proved"
    REFUTED = "refuted"
    UNKNOWN = "unknown"
    VACUOUS = "vacuous"
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass
class Param:
    name: str
    type: str


@dataclass
class LocalVar:
    name: str
    type: str


@dataclass
class FunctionSig:
    name: str
    return_type: str
    params: list[Param] = field(default_factory=list)
    local_vars: list[LocalVar] = field(default_factory=list)


@dataclass
class SpecSet:
    label: str = ""
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)


@dataclass
class ImplicationResult:
    verdict: Verdict = Verdict.UNKNOWN
    vacuous: bool = False
    witness: Optional[str] = None
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class CompareResult:
    # Postcondition implication results (directional, tool-agnostic)
    left_implies_right: ImplicationResult = field(default_factory=ImplicationResult)
    right_implies_left: ImplicationResult = field(default_factory=ImplicationResult)

    # Precondition implication results
    # pre_left_implies_right: left_pre ⇒ right_pre
    #   "Does the left precondition imply the right precondition?"
    # pre_right_implies_left: right_pre ⇒ left_pre
    #   "Does the right precondition imply the left precondition?"
    pre_left_implies_right: ImplicationResult = field(default_factory=ImplicationResult)
    pre_right_implies_left: ImplicationResult = field(default_factory=ImplicationResult)

    equivalent: bool = False

    # Specification-domain aliases (set when reference/candidate roles are known).
    #
    # For postconditions (when left=reference, right=candidate):
    #   post_soundness = left_implies_right
    #     "GT_pre ∧ GT_post ⇒ DT_post" — inferred post doesn't overclaim
    #   post_completeness = right_implies_left
    #     "GT_pre ∧ DT_post ⇒ GT_post" — inferred post captures everything
    #
    # For preconditions (when left=reference, right=candidate):
    #   pre_soundness = pre_right_implies_left
    #     "DT_pre ⇒ GT_pre" — inferred pre doesn't accept invalid inputs
    #   pre_completeness = pre_left_implies_right
    #     "GT_pre ⇒ DT_pre" — inferred pre covers the full valid domain
    post_soundness: Optional[ImplicationResult] = None
    post_completeness: Optional[ImplicationResult] = None
    pre_soundness: Optional[ImplicationResult] = None
    pre_completeness: Optional[ImplicationResult] = None

    verifier: str = ""
    bounds: dict = field(default_factory=dict)

    def apply_roles(self, reference: str = "left"):
        """Set soundness/completeness based on which side is the reference.

        Args:
            reference: "left" or "right" — which spec set is the ground truth.

        Postcondition semantics (left=reference):
          - soundness: GT implies inferred (inferred doesn't overclaim)
          - completeness: inferred implies GT (inferred captures everything)

        Precondition semantics (left=reference):
          - soundness: DT_pre ⇒ GT_pre (inferred pre doesn't accept invalid inputs)
          - completeness: GT_pre ⇒ DT_pre (inferred pre covers full valid domain)
        """
        if reference == "left":
            self.post_completeness = self.right_implies_left
            self.post_soundness = self.left_implies_right
            self.pre_soundness = self.pre_right_implies_left
            self.pre_completeness = self.pre_left_implies_right
        else:
            self.post_completeness = self.left_implies_right
            self.post_soundness = self.right_implies_left
            self.pre_soundness = self.pre_left_implies_right
            self.pre_completeness = self.pre_right_implies_left


@dataclass
class CompareInput:
    schema_version: str = "1.0"
    function: FunctionSig = field(default_factory=FunctionSig)
    left: SpecSet = field(default_factory=SpecSet)
    right: SpecSet = field(default_factory=SpecSet)
    helpers: list[str] = field(default_factory=list)
    options: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "CompareInput":
        func_d = d.get("function", {})
        func = FunctionSig(
            name=func_d.get("name", ""),
            return_type=func_d.get("return_type", ""),
            params=[Param(**p) for p in func_d.get("params", [])],
            local_vars=[LocalVar(**v) for v in func_d.get("local_vars", [])],
        )

        def parse_spec(sd: dict) -> SpecSet:
            return SpecSet(
                label=sd.get("label", ""),
                preconditions=sd.get("preconditions", []),
                postconditions=sd.get("postconditions", []),
            )

        return cls(
            schema_version=d.get("schema_version", "1.0"),
            function=func,
            left=parse_spec(d.get("left", {})),
            right=parse_spec(d.get("right", {})),
            helpers=d.get("helpers", []),
            options=d.get("options", {}),
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "function": {
                "name": self.function.name,
                "return_type": self.function.return_type,
                "params": [{"name": p.name, "type": p.type}
                           for p in self.function.params],
                "local_vars": [{"name": v.name, "type": v.type}
                               for v in self.function.local_vars],
            },
            "left": {
                "label": self.left.label,
                "preconditions": self.left.preconditions,
                "postconditions": self.left.postconditions,
            },
            "right": {
                "label": self.right.label,
                "preconditions": self.right.preconditions,
                "postconditions": self.right.postconditions,
            },
            "helpers": self.helpers,
            "options": self.options,
        }
