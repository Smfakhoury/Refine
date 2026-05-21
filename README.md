# Refine: Bounded Refinement Checking for Formal Specifications

`refine` takes two sets of pre/postconditions for the same function and checks whether they are logically equivalent, or which direction of implication holds. It uses bounded model checkers (ESBMC or CBMC) to verify the implications.

## When to use this

- Comparing inferred specifications against ground-truth specifications
- Validating that a refactored contract is equivalent to the original
- Checking if one spec refines another (weaker precondition, stronger postcondition)

## Installation

Requires Python ≥ 3.10 and one of:

- **ESBMC** (default, recommended): Docker with an `esbmc:latest` image
- **CBMC**: Native install via Homebrew (`brew install cbmc`) or [diffblue/cbmc](https://github.com/diffblue/cbmc)

No Python dependencies beyond the standard library.

## Quick start

```bash
# Compare two specs inline
python -m refine quick \
  --signature "int gcd(int a, int b)" \
  --left-pre "a > 0" "b > 0" \
  --left-post "__ret >= 1" "__ret <= a" "__ret <= b" \
  --right-post "__ret >= 1"

# Compare from a JSON file
python -m refine compare specs.json

# Batch compare a directory of JSON files
python -m refine batch specs_dir/ --out results.json
```

## Input format

The JSON input schema (v1.0):

```json
{
  "schema_version": "1.0",
  "function": {
    "name": "KthElement",
    "return_type": "int",
    "params": [
      {"name": "arr", "type": "const std::vector<int>&"},
      {"name": "k", "type": "size_t"}
    ],
    "local_vars": [
      {"name": "result", "type": "int"}
    ]
  },
  "left": {
    "label": "ground_truth",
    "preconditions": ["k >= 1 && k <= arr.size()"],
    "postconditions": ["result == arr[k - 1]"]
  },
  "right": {
    "label": "inferred",
    "preconditions": ["k >= 1 && k <= arr.size()"],
    "postconditions": ["result == arr[k - 1]"]
  },
  "helpers": [],
  "options": {}
}
```

- **`left` / `right`**: Neutral names. Use `--reference left` (default) to designate the left side as ground truth.
- **`helpers`**: Optional list of C++ helper function definitions needed by the specs.
- Spec expressions use C++ syntax. `result` is automatically mapped to the return value.

## What it checks

### Postconditions (under the intersection of both preconditions)

| Check | Encoding | Meaning |
|---|---|---|
| `left_implies_right` | `pre_L ∧ pre_R ∧ post_L ⇒ post_R` | Left postcondition implies right |
| `right_implies_left` | `pre_L ∧ pre_R ∧ post_R ⇒ post_L` | Right postcondition implies left |

### Preconditions

| Check | Encoding | Meaning |
|---|---|---|
| `pre_left_implies_right` | `pre_L ⇒ pre_R` | Left precondition implies right |
| `pre_right_implies_left` | `pre_R ⇒ pre_L` | Right precondition implies left |

### Domain aliases (when `--reference left`)

| Alias | Maps to | Interpretation |
|---|---|---|
| `post_soundness` | `left_implies_right` | Inferred postcondition doesn't overclaim |
| `post_completeness` | `right_implies_left` | Inferred postcondition captures everything |
| `pre_soundness` | `pre_right_implies_left` | Inferred precondition doesn't accept invalid inputs |
| `pre_completeness` | `pre_left_implies_right` | Inferred precondition covers the full valid domain |

**Equivalent** = all four checks proved (mutual refinement).

### Verdicts

| Verdict | Meaning |
|---|---|
| `proved` | Implication holds (within bounds) |
| `refuted` | Counterexample found |
| `vacuous` | Assumptions are contradictory — implication holds trivially |
| `unknown` | Verifier could not determine |
| `error` | Parse/conversion error in harness |
| `timeout` | Verifier timed out |

### Vacuity detection

Every `proved` result is automatically checked for vacuity: if the assumptions are contradictory (unsatisfiable), the result is reported as `vacuous` instead of `proved`.

## CLI reference

```
python -m refine compare INPUT.json [options]
python -m refine quick --signature SIG [options]
python -m refine batch INPUT_DIR/ [options]

Options:
  --backend {esbmc,cbmc}     Verifier backend (default: esbmc)
  --reference {left,right}   Which side is ground truth (default: left)
  --timeout SECONDS          Per-query timeout (default: 60)
  --unwind N                 Loop unwinding bound (default: 10)
  --emit-harness             Keep generated .cpp harness files
  --work-dir DIR             Directory for harness files
  --out FILE                 Write JSON results to file
  --docker-path PATH         Path to docker binary (ESBMC only)
  --esbmc-image IMAGE        ESBMC Docker image (default: esbmc:latest)
```

## Architecture

```
refine/
├── types.py          # Data classes: CompareInput, CompareResult, Verdict, ...
├── harness.py        # Harness generation (STL stubs, nondet vars, assume/assert)
├── core.py           # Orchestration: prep specs → build harness → run verifier
├── cli.py            # CLI entry point (compare, quick, batch)
├── backends/
│   ├── __init__.py   # VerifierBackend ABC
│   ├── cbmc.py       # CBMC backend (native)
│   └── esbmc.py      # ESBMC backend (via Docker)
└── stubs/
    ├── cbmc_stl.hpp  # Minimal STL stubs for CBMC
    └── esbmc_stl.hpp # Minimal STL stubs for ESBMC
```

### How it works

1. **Parse input** — Load function signature + two spec sets from JSON or CLI args
2. **Preprocess** — Normalize expressions, map `result` → `__ret`, extract lambdas (CBMC only)
3. **Generate harnesses** — For each implication direction, emit a self-contained C++ file with STL stubs, nondet variable declarations, `assume()` for antecedent, `assert()` for consequent
4. **Run verifier** — Invoke ESBMC (Docker) or CBMC (native) on each harness
5. **Interpret results** — Map verifier output to verdicts, check for vacuity
6. **Report** — Emit structured JSON with directional results + domain aliases

### Pre-state snapshots

Postconditions referencing `old_*` variables (e.g., `old_size`) are automatically bound to pre-state values before assumptions are applied. For example, `old_size` is bound to the first vector parameter's `.size()`.

## Adapters

The `adapters/` directory contains tool-specific converters that produce `refine` JSON input:

- **`deeptest_formalspec.py`** — Extracts specs from a DeepTest specifications database and FormalSpecCpp ground-truth files, producing comparison JSON files.

```bash
# Single task
python adapters/deeptest_formalspec.py single \
  --db .deeptest/analysis/specifications.db \
  --gt-dir FormalSpecCpp-Dataset/FormalSpecCPP \
  --nospec-dir FormalSpecCpp-Dataset/FormalSpecCPP-NoSpec \
  --task task_id_101

# Batch (all tasks)
python adapters/deeptest_formalspec.py batch \
  --db .deeptest/analysis/specifications.db \
  --gt-dir FormalSpecCpp-Dataset/FormalSpecCPP \
  --nospec-dir FormalSpecCpp-Dataset/FormalSpecCPP-NoSpec \
  --out-dir batch_inputs/
```

## Limitations

- **Bounded verification**: Results are sound within the configured unwind/vector bounds, not universally. A `proved` verdict means "no counterexample within bounds."
- **STL stubs**: Only `vector`, `pair`, and common algorithms are stubbed. Programs using `map`, `set`, `string`, etc. will get errors.
- **Lambda support**: ESBMC handles lambdas natively. CBMC requires lambda extraction to named functions (automatic but imperfect).
- **No execution model**: Specs are compared as pure logical predicates. The checker does not model actual function execution — it checks implication between spec expressions.

## License

MIT
