"""Harness generation for bounded model checkers.

Generates self-contained C++ harness files with:
  - Minimal STL stubs (vector, pair, algorithms, math)
  - Nondet variable declarations with bounded constraints
  - Assume/assert blocks encoding specification implications

Supports both CBMC and ESBMC backends via configurable intrinsics.
"""

import re
from pathlib import Path
from .types import FunctionSig, Param, CompareInput

MAX_VEC_SIZE = 4

# Verifier-specific intrinsics
_INTRINSICS = {
    "cbmc": {"assume": "__CPROVER_assume", "assert": "__CPROVER_assert"},
    "esbmc": {"assume": "__VERIFIER_assume", "assert": "assert"},
}


def _type_category(ptype: str) -> str:
    """Classify a C++ type string into a harness type category."""
    t = re.sub(r'\bconst\b', '', ptype).strip().replace('&', '').strip()
    if 'vector<vector<int>>' in t:
        return 'vec_vec_int'
    if 'vector<int>' in t or 'vector<unsigned' in t:
        return 'vec_int'
    if 'vector<double>' in t or 'vector<float>' in t:
        return 'vec_double'
    if 'vector<bool>' in t:
        return 'vec_int'
    if 'vector<' in t:
        return 'vec_int'
    if 'pair<int' in t:
        return 'pair_int'
    if t in ('int', 'long', 'long int', 'long long'):
        return 'int'
    if t in ('unsigned int', 'unsigned', 'size_t', 'uint32_t'):
        return 'unsigned_int'
    if t in ('bool',):
        return 'bool'
    if t in ('double', 'float', 'long double'):
        return 'double'
    if t == 'char':
        return 'char'
    if t == 'void':
        return 'void'
    return 'int'


def _nondet_decl(name: str, cat: str, verifier: str = "cbmc") -> str:
    """Generate a nondet variable declaration with bounded constraints."""
    assume_fn = _INTRINSICS.get(verifier, _INTRINSICS["cbmc"])["assume"]
    if cat in ('vec_int', 'vec_vec_int'):
        return (
            f"  int {name}_data_arr[{MAX_VEC_SIZE + 1}];\n"
            f"  std::vector<int> {name};\n"
            f"  {name}._data = {name}_data_arr;\n"
            f"  {assume_fn}({name}._size <= {MAX_VEC_SIZE});\n"
        )
    if cat == 'vec_double':
        return (
            f"  double {name}_data_arr[{MAX_VEC_SIZE + 1}];\n"
            f"  std::vector<double> {name};\n"
            f"  {name}._data = {name}_data_arr;\n"
            f"  {assume_fn}({name}._size <= {MAX_VEC_SIZE});\n"
        )
    ctype_map = {
        'pair_int': 'std::pair<int,int>',
        'int': 'int',
        'unsigned_int': 'unsigned int',
        'bool': 'bool',
        'double': 'double',
        'char': 'char',
    }
    ctype = ctype_map.get(cat, 'int')
    return f"  {ctype} {name};\n"


def _load_stubs(verifier: str = "cbmc") -> str:
    """Load the STL stubs header for the given verifier."""
    stub_name = f"{verifier}_stl.hpp"
    stubs_path = Path(__file__).parent / "stubs" / stub_name
    if stubs_path.exists():
        return stubs_path.read_text()
    # Fall back to cbmc stubs
    fallback = Path(__file__).parent / "stubs" / "cbmc_stl.hpp"
    if fallback.exists():
        return fallback.read_text()
    raise FileNotFoundError(f"STL stubs not found at {stubs_path}")


def _preprocess_expr(expr: str) -> str | None:
    """Normalize a spec expression for CBMC compatibility."""
    e = expr
    e = re.sub(r'static_cast\s*<[^>]+>\s*\(', '(', e)
    e = re.sub(r'\b__result\b', '__ret', e)
    if 'boost::' in e:
        return None
    return e


def _map_result_alias(expr: str, params: list[Param]) -> str:
    """Replace 'result' with '__ret' when it's not a parameter name."""
    param_names = {p.name for p in params}
    e = expr
    if 'result' not in param_names:
        e = re.sub(r'(?<!\w)result(?!\w)', '__ret', e)
    return e


_LAMBDA_CTR = 0


def _extract_lambdas(expr: str, task_tag: str) -> tuple[str, list[str]]:
    """Extract C++ lambdas into standalone helper functions for CBMC.

    CBMC's parser cannot handle lambda syntax. This converts:
      [&](int x) { return x > 0; }  →  bool __pred_N(int x) { return x > 0; }
    and replaces the lambda in the expression with the function name.

    Captured variables ([&]) are declared as globals before the helpers.
    """
    global _LAMBDA_CTR
    helpers = []
    result = expr

    # Immediately-invoked lambdas: [&]() { ... }()
    while True:
        m = re.search(r'\[&?\]\s*\(\s*\)\s*\{', result)
        if not m:
            break
        brace_start = result.index('{', m.start())
        body = _balanced_extract(result, brace_start)
        if body is None:
            break
        after_brace = brace_start + len(body) + 2
        rest = result[after_brace:].lstrip()
        invoked = rest.startswith('()')

        _LAMBDA_CTR += 1
        name = f"__lambda_{task_tag}_{_LAMBDA_CTR}"
        helpers.append(f"bool {name}() {{\n{body}\n}}\n")
        end_pos = after_brace + (rest.index('()') + 2 if invoked else 0)
        result = result[:m.start()] + f"{name}()" + result[end_pos:]

    # Predicate lambdas: [&](int x) { ... }
    while True:
        m = re.search(r'\[&?\]\s*\(([^)]*)\)\s*\{', result)
        if not m:
            break
        params_str = m.group(1).strip()
        brace_start = result.index('{', m.start())
        body = _balanced_extract(result, brace_start)
        if body is None:
            break

        _LAMBDA_CTR += 1
        name = f"__pred_{task_tag}_{_LAMBDA_CTR}"
        helpers.append(f"bool {name}({params_str}) {{\n{body}\n}}\n")
        end_pos = brace_start + len(body) + 2
        result = result[:m.start()] + name + result[end_pos:]

    return result, helpers


def _balanced_extract(text: str, start: int) -> str | None:
    """Extract content between balanced delimiters at position start."""
    opener = text[start]
    closer = ')' if opener == '(' else '}'
    depth = 0
    i = start
    while i < len(text):
        if text[i] == opener:
            depth += 1
        elif text[i] == closer:
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
        i += 1
    return None


def _auto_declare_locals(
    spec_text: str,
    declared: set[str],
    helper_funcs: list[str],
) -> list[str]:
    """Auto-declare identifiers referenced in specs but not yet declared."""
    idents = set(re.findall(r'\b([a-zA-Z_]\w*)\b', spec_text))
    keywords = {
        'int', 'unsigned', 'bool', 'double', 'float', 'char', 'void',
        'const', 'static', 'return', 'if', 'else', 'for', 'while',
        'true', 'false', 'size', 'empty', 'front', 'back', 'begin', 'end',
        'std', 'abs', 'is_sorted', 'all_of', 'any_of', 'none_of',
        'equal', 'find', 'count', 'count_if', 'accumulate', 'sort',
        'min_element', 'max_element', 'adjacent_find', 'not_equal_to',
        '__CPROVER_assume', '__CPROVER_assert',
        '__VERIFIER_assume', 'assert',
        'push_back', 'reserve', 'size_t', 'main', 'EPSILON', 'M_PI',
        'numeric_limits', 'vector', 'pair', 'min', 'max', 'sqrt', 'pow',
        'data', 'fabs', 'floor', 'ceil', 'round', 'log', 'log2',
        'auto', 'long', 'short', 'signed', 'sizeof', 'result',
        'ok', 'v', 'x', 'idx', 'nullptr', 'decltype', 'find_if',
    }
    for h in helper_funcs:
        m = re.match(r'\w+\s+(\w+)\s*\(', h)
        if m:
            keywords.add(m.group(1))

    # Skip old_* identifiers — they are handled by _generate_old_bindings
    lines = []
    for name in sorted(idents - declared - keywords):
        if name.startswith('_'):
            continue
        if name.startswith('old_'):
            continue
        if (re.search(rf'\b{re.escape(name)}\.(size|begin|end|empty|front|back|push_back)\b',
                       spec_text)
                or re.search(rf'\b{re.escape(name)}\[', spec_text)):
            lines.append(f"  std::vector<int> {name}; // auto-declared (vector)")
        elif re.search(rf'\b{re.escape(name)}\.(first|second)\b', spec_text):
            lines.append(f"  std::pair<int,int> {name}; // auto-declared (pair)")
        else:
            lines.append(f"  int {name}; // auto-declared")
    return lines


def _generate_old_bindings(
    spec_text: str,
    params: list[Param],
) -> list[str]:
    """Generate pre-state snapshot bindings for old_* variables.

    Recognizes patterns like:
      old_size → bound to the first vector param's .size()
      old_X → bound to param X if X is a param name

    These must be emitted BEFORE any assumes so that postconditions
    referencing old_* values get the correct pre-state semantics.
    """
    old_refs = set(re.findall(r'\b(old_\w+)\b', spec_text))
    if not old_refs:
        return []

    param_names = {p.name for p in params}
    vec_params = [p.name for p in params if 'vector' in p.type]
    lines = []

    for old_var in sorted(old_refs):
        suffix = old_var[4:]  # strip 'old_'

        if suffix == 'size' and vec_params:
            # Bind to first vector param's size
            lines.append(f"  unsigned int {old_var} = {vec_params[0]}.size();")
        elif suffix in param_names:
            # Bind to a copy of the parameter
            lines.append(f"  auto {old_var} = {suffix};")
        else:
            # Can't determine binding — leave as declared nondet
            # (already declared by common_locals or auto_declare)
            pass

    return lines


def build_implication_harness(
    inp: CompareInput,
    assume_pre: list[str],
    assume_post: list[str],
    assert_post: list[str],
    helper_funcs: list[str],
    harness_label: str,
    verifier: str = "cbmc",
) -> str:
    """Build a harness for checking an implication.

    The harness encodes:
        assume(assume_pre) ∧ assume(assume_post) → assert(assert_post)

    If the verifier reports VERIFICATION SUCCESSFUL, the implication holds
    (under the given bounds).
    """
    func = inp.function
    intrinsics = _INTRINSICS.get(verifier, _INTRINSICS["cbmc"])
    assume_fn = intrinsics["assume"]
    assert_fn = intrinsics["assert"]
    stubs = _load_stubs(verifier)
    lines = [f"// refine harness: {func.name} [{harness_label}]"]
    lines.append(stubs)

    # Globals for captured variables (CBMC lambda helpers reference these)
    # ESBMC handles lambdas natively, so globals are not needed.
    if verifier != "esbmc":
        for p in func.params:
            cat = _type_category(p.type)
            if cat in ('vec_int', 'vec_vec_int'):
                lines.append(f"int _g_{p.name}_data[{MAX_VEC_SIZE + 1}];")
                lines.append(f"std::vector<int> _g_{p.name};")
            elif cat == 'vec_double':
                lines.append(f"double _g_{p.name}_data[{MAX_VEC_SIZE + 1}];")
                lines.append(f"std::vector<double> _g_{p.name};")
            elif cat in ('int', 'unsigned_int', 'bool', 'double', 'char'):
                ctype = {'int': 'int', 'unsigned_int': 'unsigned int',
                         'bool': 'bool', 'double': 'double', 'char': 'char'}.get(cat, 'int')
                lines.append(f"{ctype} _g_{p.name};")

    ret_cat = _type_category(func.return_type)
    if verifier != "esbmc" and ret_cat != 'void':
        if ret_cat in ('vec_int', 'vec_vec_int'):
            lines.append(f"int _g___ret_data[{MAX_VEC_SIZE + 1}];")
            lines.append(f"std::vector<int> _g___ret;")
        elif ret_cat in ('int', 'unsigned_int', 'bool', 'double'):
            ctype = {'int': 'int', 'unsigned_int': 'unsigned int',
                     'bool': 'bool', 'double': 'double'}.get(ret_cat, 'int')
            lines.append(f"{ctype} _g___ret;")

    lines.append("")
    for h in helper_funcs:
        lines.append(h)

    lines.append("")
    lines.append("int main() {")

    # Nondet parameters
    for p in func.params:
        lines.append(_nondet_decl(p.name, _type_category(p.type), verifier))

    # Nondet return value
    if ret_cat != 'void':
        lines.append(_nondet_decl('__ret', ret_cat, verifier))

    # Local variable declarations (skip 'result' — it's aliased to __ret)
    for lv in func.local_vars:
        if lv.name == 'result':
            continue
        lines.append(f"  {lv.type} {lv.name};")

    # Common loop vars
    declared = set(p.name for p in func.params) | {'__ret', 'result', 'true', 'false'}
    declared |= {v.name for v in func.local_vars}
    common_locals = [("i", "int"), ("j", "int"), ("k", "int"),
                     ("old_size", "unsigned int")]
    for vn, vt in common_locals:
        if vn not in declared:
            lines.append(f"  {vt} {vn};")
            declared.add(vn)

    for pn in [p.name for p in func.params]:
        declared.add(f'{pn}_data_arr')
    declared.add('__ret_data_arr')

    all_spec_text = ' '.join(assume_pre + assume_post + assert_post)
    auto_lines = _auto_declare_locals(all_spec_text, declared, helper_funcs)
    lines.extend(auto_lines)

    # Bind old_* snapshot variables to pre-state values before assumes.
    # e.g., old_size = arr.size() so postconditions like
    # "arr.size() == old_size" express frame conditions correctly.
    old_bindings = _generate_old_bindings(all_spec_text, func.params)
    if old_bindings:
        lines.append("")
        lines.append("  // Pre-state snapshots")
        lines.extend(old_bindings)

    lines.append("")
    for expr in assume_pre:
        lines.append(f"  {assume_fn}({expr}); // pre")
    for expr in assume_post:
        lines.append(f"  {assume_fn}({expr}); // assumed post")
    lines.append("")
    if assert_fn == "assert":
        for expr in assert_post:
            lines.append(f'  assert({expr}); // asserted post')
    else:
        for expr in assert_post:
            lines.append(f'  {assert_fn}({expr}, "check"); // asserted post')
    lines.append("")
    lines.append("  return 0;")
    lines.append("}")

    return "\n".join(lines)


def build_vacuity_harness(
    inp: CompareInput,
    assume_pre: list[str],
    assume_post: list[str],
    helper_funcs: list[str],
    verifier: str = "cbmc",
) -> str:
    """Build a vacuity harness: assume(pre) ∧ assume(post) → assert(false).

    If the verifier reports VERIFICATION SUCCESSFUL, the assumptions are
    contradictory (vacuously true). Should FAIL if assumptions are satisfiable.
    """
    return build_implication_harness(
        inp, assume_pre, assume_post, ["false"], helper_funcs, "vacuity",
        verifier=verifier,
    )
