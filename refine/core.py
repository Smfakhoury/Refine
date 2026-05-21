"""Core comparison logic for refine.

Orchestrates harness generation, verifier invocation, and result
interpretation to compare two specification sets.
"""

import re
from pathlib import Path
from .types import (
    CompareInput, CompareResult, ImplicationResult, Verdict,
)
from .harness import (
    build_implication_harness, build_vacuity_harness,
    _preprocess_expr, _map_result_alias, _extract_lambdas,
)
from .backends import VerifierBackend

# Patterns that indicate unsupported constructs (conservative preflight).
_UNSUPPORTED_PATTERNS = [
    (r'\bstd::map\b', "std::map not supported by STL stubs"),
    (r'\bstd::unordered_map\b', "std::unordered_map not supported"),
    (r'\bstd::string\b', "std::string not supported by STL stubs"),
    (r'\bstd::views\b', "C++20 ranges/views not supported"),
    (r'\bstd::ranges\b', "C++20 ranges not supported"),
    (r'\bstd::optional\b', "std::optional not supported"),
    (r'\bstd::tuple\b', "std::tuple not supported"),
    (r'\bstd::array\b', "std::array not supported"),
]

# ESBMC/Clang error signatures that indicate unsupported constructs
# (post-hoc reclassification from ERROR → UNSUPPORTED).
_UNSUPPORTED_ERROR_SIGS = [
    "called object type",       # GT helper function not defined
    "is not a class, namespace, or enumeration",  # C++20 views etc.
    "indirection requires pointer operand",  # pointer deref on non-pointer
]


def _detect_unsupported(exprs: list[str], inp: "CompareInput | None" = None) -> str | None:
    """Check expressions and function signature for known unsupported constructs.

    Returns a diagnostic string if unsupported, or None if OK.
    """
    text = " ".join(exprs)
    # Also check parameter types
    if inp is not None:
        text += " " + " ".join(p.type for p in inp.function.params)
        text += " " + inp.function.return_type
    for pattern, reason in _UNSUPPORTED_PATTERNS:
        if re.search(pattern, text):
            return reason
    return None


def _reclassify_error(result: ImplicationResult) -> None:
    """Post-hoc: reclassify ERROR → UNSUPPORTED when diagnostics match
    known unsupported-construct signatures."""
    if result.verdict != Verdict.ERROR:
        return
    diag_text = " ".join(result.diagnostics)
    for sig in _UNSUPPORTED_ERROR_SIGS:
        if sig in diag_text:
            result.verdict = Verdict.UNSUPPORTED
            result.diagnostics.append(f"Reclassified: error matches unsupported signature '{sig}'")
            return


def _prep_exprs(
    exprs: list[str],
    params: list,
    task_tag: str,
    extract_lambdas: bool = True,
    lambda_counter: list[int] | None = None,
) -> tuple[list[str], list[str], bool]:
    """Preprocess a list of spec expressions.

    Returns (processed_exprs, helper_functions, any_lambdas_extracted).
    ESBMC supports lambdas natively, so lambda extraction can be skipped.
    """
    processed = []
    helpers = []
    any_extracted = False
    for raw in exprs:
        e = _preprocess_expr(raw)
        if e is None:
            continue
        e = _map_result_alias(e, params)
        if extract_lambdas:
            e, h = _extract_lambdas(e, task_tag, lambda_counter)
            if h:
                any_extracted = True
            helpers.extend(h)
        processed.append(e)
    return processed, helpers, any_extracted


def _check_implication(
    inp: CompareInput,
    assume_pre: list[str],
    assume_post: list[str],
    assert_post: list[str],
    helper_funcs: list[str],
    label: str,
    work_dir: Path,
    backend: VerifierBackend,
    timeout: int,
) -> ImplicationResult:
    """Run a single implication check and optional vacuity test."""
    result = ImplicationResult()

    if not assert_post:
        result.verdict = Verdict.PROVED
        result.diagnostics.append("No assertions to check — trivially true")
        return result

    if not assume_post and not assume_pre:
        # No assumptions — just check the assertions
        pass

    code = build_implication_harness(
        inp, assume_pre, assume_post, assert_post, helper_funcs, label,
        verifier=backend.name(),
    )
    path = work_dir / f"{label}.cpp"
    path.write_text(code)

    verdict, detail = backend.check_harness(path, timeout)

    if verdict == Verdict.PROVED:
        # Check vacuity: are assumptions contradictory?
        vac_code = build_vacuity_harness(
            inp, assume_pre, assume_post, helper_funcs,
            verifier=backend.name(),
        )
        vac_path = work_dir / f"{label}_vacuity.cpp"
        vac_path.write_text(vac_code)
        vac_verdict, _ = backend.check_harness(vac_path, timeout)

        if vac_verdict == Verdict.PROVED:
            result.verdict = Verdict.VACUOUS
            result.vacuous = True
            result.diagnostics.append("Assumptions are contradictory (vacuously true)")
        else:
            result.verdict = Verdict.PROVED
    elif verdict == Verdict.REFUTED:
        result.verdict = Verdict.REFUTED
        result.witness = detail
    elif verdict == Verdict.TIMEOUT:
        result.verdict = Verdict.TIMEOUT
        result.diagnostics.append("Verifier timed out")
    elif verdict == Verdict.ERROR:
        result.verdict = Verdict.ERROR
        result.diagnostics.append(detail or "Unknown verifier error")
    else:
        result.verdict = Verdict.UNKNOWN
        if detail:
            result.diagnostics.append(detail)

    return result


def compare(
    inp: CompareInput,
    backend: VerifierBackend,
    work_dir: Path | None = None,
    timeout: int = 60,
    reference: str = "left",
    emit_harness: bool = False,
) -> CompareResult:
    """Compare two spec sets using a bounded model checker.

    Args:
        inp: The comparison input (function sig, left specs, right specs).
        backend: The verifier backend to use.
        work_dir: Directory for generated harness files. Created if needed.
        timeout: Per-query timeout in seconds.
        reference: Which side is the reference ("left" or "right").
                   Used to set completeness/soundness aliases.
        emit_harness: If True, keep harness files; otherwise clean up.

    Returns:
        CompareResult with directional verdicts and optional
        completeness/soundness aliases.
    """
    if work_dir is None:
        work_dir = Path("/tmp/refine_harnesses") / inp.function.name
    work_dir.mkdir(parents=True, exist_ok=True)

    tag = re.sub(r'\W', '_', inp.function.name)

    # ESBMC supports lambdas natively — skip extraction
    do_extract = backend.name() != "esbmc"

    # Deterministic lambda counter — reset per comparison
    lambda_ctr = [0]

    # Preprocess all spec expressions
    left_pre, lp_helpers, lp_lam = _prep_exprs(
        inp.left.preconditions, inp.function.params, tag, do_extract, lambda_ctr)
    left_post, lpost_helpers, lpost_lam = _prep_exprs(
        inp.left.postconditions, inp.function.params, tag, do_extract, lambda_ctr)
    right_pre, rp_helpers, rp_lam = _prep_exprs(
        inp.right.preconditions, inp.function.params, tag, do_extract, lambda_ctr)
    right_post, rpost_helpers, rpost_lam = _prep_exprs(
        inp.right.postconditions, inp.function.params, tag, do_extract, lambda_ctr)

    any_lambdas_extracted = lp_lam or lpost_lam or rp_lam or rpost_lam
    all_helpers = list(inp.helpers) + lp_helpers + lpost_helpers + rp_helpers + rpost_helpers

    result = CompareResult(
        verifier=backend.name(),
        bounds={"unwind": getattr(backend, '_unwind', 20), "vec_max_size": 4},
    )

    # ---- Preflight unsupported check ----
    all_raw = (inp.left.preconditions + inp.left.postconditions
               + inp.right.preconditions + inp.right.postconditions)
    unsup = _detect_unsupported(all_raw, inp)
    if unsup:
        unsup_result = ImplicationResult(
            verdict=Verdict.UNSUPPORTED,
            diagnostics=[unsup],
        )
        result.left_implies_right = unsup_result
        result.right_implies_left = unsup_result
        result.pre_left_implies_right = unsup_result
        result.pre_right_implies_left = unsup_result
        result.apply_roles(reference)
        return result

    # ---- Precondition checks ----
    # pre_left_implies_right: left_pre ⇒ right_pre
    if right_pre:
        result.pre_left_implies_right = _check_implication(
            inp,
            assume_pre=left_pre,
            assume_post=[],
            assert_post=right_pre,
            helper_funcs=all_helpers,
            label="pre_left_implies_right",
            work_dir=work_dir,
            backend=backend,
            timeout=timeout,
        )
    else:
        result.pre_left_implies_right = ImplicationResult(
            verdict=Verdict.PROVED,
            diagnostics=["No right preconditions — trivially implied"],
        )

    # pre_right_implies_left: right_pre ⇒ left_pre
    if left_pre:
        result.pre_right_implies_left = _check_implication(
            inp,
            assume_pre=right_pre,
            assume_post=[],
            assert_post=left_pre,
            helper_funcs=all_helpers,
            label="pre_right_implies_left",
            work_dir=work_dir,
            backend=backend,
            timeout=timeout,
        )
    else:
        result.pre_right_implies_left = ImplicationResult(
            verdict=Verdict.PROVED,
            diagnostics=["No left preconditions — trivially implied"],
        )

    # ---- Postcondition checks (combined precondition domain) ----
    combined_pre = left_pre + right_pre

    result.left_implies_right = _check_implication(
        inp,
        assume_pre=combined_pre,
        assume_post=left_post,
        assert_post=right_post,
        helper_funcs=all_helpers,
        label="left_implies_right",
        work_dir=work_dir,
        backend=backend,
        timeout=timeout,
    )

    result.right_implies_left = _check_implication(
        inp,
        assume_pre=combined_pre,
        assume_post=right_post,
        assert_post=left_post,
        helper_funcs=all_helpers,
        label="right_implies_left",
        work_dir=work_dir,
        backend=backend,
        timeout=timeout,
    )

    # ---- Diagnostic: postconditions under GT precondition only ----
    # Disentangles postcondition quality from precondition quality.
    gt_pre = left_pre if reference == "left" else right_pre

    post_lr_gt = _check_implication(
        inp,
        assume_pre=gt_pre,
        assume_post=left_post,
        assert_post=right_post,
        helper_funcs=all_helpers,
        label="post_lr_gt_pre",
        work_dir=work_dir,
        backend=backend,
        timeout=timeout,
    )

    post_rl_gt = _check_implication(
        inp,
        assume_pre=gt_pre,
        assume_post=right_post,
        assert_post=left_post,
        helper_funcs=all_helpers,
        label="post_rl_gt_pre",
        work_dir=work_dir,
        backend=backend,
        timeout=timeout,
    )

    if reference == "left":
        result.post_sound_under_gt_pre = post_lr_gt
        result.post_complete_under_gt_pre = post_rl_gt
    else:
        result.post_sound_under_gt_pre = post_rl_gt
        result.post_complete_under_gt_pre = post_lr_gt

    # ---- Post-hoc reclassification: ERROR → UNSUPPORTED ----
    for check in [result.left_implies_right, result.right_implies_left,
                  result.pre_left_implies_right, result.pre_right_implies_left,
                  result.post_sound_under_gt_pre, result.post_complete_under_gt_pre]:
        if check is not None:
            _reclassify_error(check)

    # ---- Lambda extraction warning (CBMC only) ----
    if any_lambdas_extracted:
        for check in [result.left_implies_right, result.right_implies_left]:
            check.lambda_extracted = True
            check.diagnostics.append(
                "Lambda expressions were extracted to helper functions; "
                "semantics may differ from the original"
            )

    # Equivalence: all 4 formal checks proved (vacuous does NOT count)
    result.equivalent = (
        result.left_implies_right.verdict == Verdict.PROVED
        and result.right_implies_left.verdict == Verdict.PROVED
        and result.pre_left_implies_right.verdict == Verdict.PROVED
        and result.pre_right_implies_left.verdict == Verdict.PROVED
    )

    # Set completeness/soundness aliases
    result.apply_roles(reference)

    if not emit_harness:
        for f in work_dir.glob("*.cpp"):
            f.unlink()
        try:
            work_dir.rmdir()
        except OSError:
            pass

    return result
