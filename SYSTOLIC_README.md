# Calyx Systolic Array Generator

> **Based on the [Calyx compiler infrastructure](https://github.com/calyxir/calyx)** developed by the CAPRA research group at Cornell University. This repo contains the original Calyx compiler source unchanged — my own work is layered on top of it and is described below.

A research project built on top of the [Calyx](https://calyxir.org) hardware compiler infrastructure. Starting from a naive software-style matrix multiply, I progressively optimized it into a statically-scheduled, pipelined systolic array — all expressed in Calyx IR and verified with Verilator cycle-accurate simulation.

## My Contributions

The files I authored are:

| File | Description |
|------|-------------|
| `matmul.futil` | Naive 4×4 matrix multiplier written in Calyx IR (Phase 2 baseline) |
| `gen_systolic.py` | Python script that generates a pipelined 4×4 systolic array in Calyx IR |
| `systolic.futil` | Generated systolic array output (produced by `gen_systolic.py`) |
| `gen_data.py` | Generates simulation input data with pre-skewed memories |
| `data.json` | Input matrices for the matmul simulation |
| `data_systolic.json` | Pre-skewed input memories for the systolic simulation |
| `out.json` | Matmul simulation output (cycles: 804) |
| `out_systolic.json` | Systolic simulation output (cycles: 55) |
| `SYSTOLIC_README.md` | This file |

Everything else in the repo is the upstream Calyx compiler infrastructure, used as-is.

---

---

## Toolchain

| Tool | Version | Role |
|------|---------|------|
| `fud2` (Rust) | 0.0.2 | Build driver: routes `.futil → Verilog → Verilator sim → `.json` |
| Calyx compiler | `target/debug/calyx` | Frontend type-checker and IR lowering |
| Verilator | 5.044 | Cycle-accurate RTL simulation |
| Python 3 | — | Data generation (`gen_data.py`) and design generation (`gen_systolic.py`) |

**End-to-end simulation command:**
```bash
fud2 <design>.futil --through calyx-to-verilog -s sim.data=<data>.json -o out.json
```

---

## Phase 2 — Naive 4×4 Matrix Multiplier (`matmul.futil`)

### Overview

A direct translation of the triple-nested loop `C[i][j] += A[i][k] * B[k][j]` into Calyx IR. This establishes the correctness baseline and cycle-count reference point.

### Architecture

- **Memories:** `@external A, B, C = seq_mem_d2(32, 4, 4, 3, 3)` — 2-D sequential memories with 1-cycle read latency.
- **Loop counters:** Three 3-bit registers `i`, `j`, `k` each with their own `std_lt` comparator (`lt_i`, `lt_j`, `lt_k`) to avoid structural conflicts in nested `while` conditions.
- **Multiply:** `std_mult_pipe(32)` — 3-cycle pipelined multiplier. Its three `write_together(1)` ports (`go`, `left`, `right`) must all be guarded on the same condition, or the Calyx checker raises a constraint violation.
- **Accumulate:** Result is latched into `mac_reg`, then added to `C.read_data` (which is `@stable`) and written back via a separate group.

### Key Design Decisions

**Separate comparators per loop level.** A single shared `std_lt` cell would cause a *structural conflict* because the three `comb group` definitions for `cond_i`, `cond_j`, `cond_k` would all drive `lt.left` simultaneously in the compiler's static analysis scope. Three cells (`lt_i`, `lt_j`, `lt_k`) are declared instead; the Calyx cell-sharing pass merges them back to one hardware unit after proving mutual exclusivity.

**Split read-modify-write into two groups.** `seq_mem_d2` has a single `done` port shared by reads and writes. Doing both in one group creates a hazard. The solution is:
1. `read_c` — issues a read (`content_en=1`), exits on `C.done`. `C.read_data` is marked `@stable` so its value is retained.
2. `do_add` — issues a write (`content_en=1, write_en=1, write_data=add.out`), exits on `C.done`.

**`write_together` guard uniformity.** `std_mult_pipe`'s `go`, `left`, and `right` ports are in the same `write_together(1)` group. All three must be driven under the same guard expression; mixing a conditional `go` with unconditional `left`/`right` violates the constraint.

### Data Generation (`gen_data.py`)

```python
random.seed(42)
A = [[random.randint(1, 5) for _ in range(4)] for _ in range(4)]
B = [[random.randint(1, 5) for _ in range(4)] for _ in range(4)]
```

Matrices are stored as nested 2-D arrays to match `seq_mem_d2`'s flat row-major layout expected by `fud2`'s JSON harness.

### Result

```
A × B  (numpy golden):
[[23 33 20 16]
 [28 45 28 30]
 [33 54 36 22]
 [19 28 17 15]]
```

| Metric | Value |
|--------|-------|
| Correctness | PASS |
| Simulation cycles | **804** |

---

## Phase 3 — Static Systolic Array Generator (`gen_systolic.py`)

### Overview

A Python script that programmatically emits a 4×4 weight-stationary systolic array in Calyx IR. The design uses Calyx's *Piezo* static abstractions (`static<N>` components, `%N` cycle guards, `static seq`) to eliminate go/done handshake overhead and expose the array's regular, statically-known timing to the compiler.

### Architecture

#### `mac_pe` — Static Processing Element

```
static<1> component mac_pe(top: 32, left: 32)
    -> (bottom: 32, right: 32, result: 32)
```

| Cell | Type | Purpose |
|------|------|---------|
| `mult` | `std_unsyn_mult(32)` | Combinational multiply (non-synthesizable, used for simulation) |
| `acc` | `std_reg(32)` | Running accumulator |
| `r_fwd` | `std_reg(32)` | Registered left→right data forwarding |
| `d_fwd` | `std_reg(32)` | Registered top→bottom data forwarding |
| `add` | `std_add(32)` | Accumulator adder |

The single `static<1> group step` fires every cycle the PE is active:
- `mult.left = top; mult.right = left;` — combinational multiply
- `add.left = mult.out; add.right = acc.out;` — accumulate
- `acc.in = add.out; acc.write_en = %0 ? 1'd1;` — write result
- `r_fwd.in = left; r_fwd.write_en = %0 ? 1'd1;` — forward A value rightward
- `d_fwd.in = top; d_fwd.write_en = %0 ? 1'd1;` — forward B value downward

Continuous assignments outside the group:
```
bottom = d_fwd.out;
right  = r_fwd.out;
result = acc.out;
```

**Port naming note:** The output port is named `result` (not `acc_out`) to avoid a Verilog backend naming collision. Calyx's Verilog emitter auto-generates a local wire `logic [31:0] acc_out` for the `acc` register's `.out` port — if the component output port were also named `acc_out`, the emitted SystemVerilog would have a duplicate signal declaration that Verilator rejects.

#### `main` — 4×4 PE Grid

```
cells {
    @external A0..A3 = seq_mem_d1(32, 16, 4);  // pre-skewed A rows
    @external B0..B3 = seq_mem_d1(32, 16, 4);  // pre-skewed B columns
    @external C0..C3 = seq_mem_d1(32, 4, 2);   // result rows
    pe_00 .. pe_33   = mac_pe();
    steps            = std_reg(4);
    steps_add        = std_add(4);
    lt15             = std_lt(4);
}
```

**Memory sizing:** Input memories have depth 16 and 4-bit address (`IDX_BITS_IN = 4`) to match the 4-bit `steps` register exactly — a 3-bit address would produce a Calyx port-width mismatch error at compile time.

#### Pre-Skewed Input Data

A standard systolic array requires diagonal wave-front alignment: row `i` of A is delayed by `i` cycles before entering the array, and column `j` of B is delayed by `j` cycles. This is achieved by pre-padding each 1-D memory with leading zeros:

| Memory | Contents |
|--------|----------|
| `A0` | `[a00, a01, a02, a03, 0, 0, …]` |
| `A1` | `[0, a10, a11, a12, a13, 0, …]` |
| `A2` | `[0, 0, a20, a21, a22, a23, …]` |
| `A3` | `[0, 0, 0, a30, a31, a32, a33, …]` |
| `B0` | `[b00, b10, b20, b30, 0, 0, …]` (column 0) |
| `B1` | `[0, b01, b11, b21, b31, 0, …]` (column 1) |

With `seq_mem_d1`'s 1-cycle read latency plus `j` hops of `r_fwd` forwarding for A and `i` hops of `d_fwd` forwarding for B, element `A[i][k]` and `B[k][j]` both arrive at `PE[i][j]` at cycle `i + j + k + 1`. This is identical for all `k`, so all four partial products for each output cell are computed in four consecutive cycles without stalls.

#### `pulse_array` Static Group

```calyx
static<1> group pulse_array {
    A0.addr0 = steps.out; A0.content_en = 1'd1;
    ...
    pe_00.go   = %0 ? 1'd1;
    pe_00.left = A0.read_data; pe_00.top = B0.read_data;
    pe_01.go   = %0 ? 1'd1;
    pe_01.left = pe_00.right;  pe_01.top = B1.read_data;
    ...
}
```

**The `go` signal is mandatory.** The `mac_pe` static component guards all register writes on its `go` port (`acc.write_en = go ? 1'd1`). Driving only the data ports without asserting `go` results in the accumulator never writing — all outputs remain zero. Every PE must have `pe_{i}{j}.go = %0 ? 1'd1;` asserted inside `pulse_array`.

#### Control Flow

```calyx
control {
    seq {
        init_steps;
        while lt15.out with cond_steps {
            static seq { pulse_array; upd_steps; }
        }
        par { wb_c0j0; wb_c1j0; wb_c2j0; wb_c3j0; }  // column 0 write-back
        par { wb_c0j1; wb_c1j1; wb_c2j1; wb_c3j1; }  // column 1 write-back
        par { wb_c0j2; wb_c1j2; wb_c2j2; wb_c3j2; }  // column 2 write-back
        par { wb_c0j3; wb_c1j3; wb_c2j3; wb_c3j3; }  // column 3 write-back
    }
}
```

The while loop body is `static seq { pulse_array; upd_steps; }` — two static cycles. Writes within a single `C_i` memory are sequential (single-port); writes to different `C_i` memories are independent and use `par`.

### Result

| Metric | Value |
|--------|-------|
| Correctness | PASS |
| Simulation cycles | **52** |
| Speedup vs. naive | **15.5×** |

---

## Phase 4 — Pipelined PE (`gen_systolic.py`, updated)

### Motivation

In the original mac_pe, the combinational critical path within a single clock cycle was:

```
mult.out → add.left → add.out → acc.in
```

This chain (multiply result feeding directly into the adder and then the accumulator) limits the maximum synthesis clock frequency. Breaking it with a pipeline register shortens each stage's combinational depth.

### Change: `mult_reg` Pipeline Register

A new register `mult_reg = std_reg(32)` is inserted between the multiplier output and the adder input:

**Before (unpipelined):**
```calyx
mult.left  = top;          mult.right = left;
add.left   = mult.out;     add.right  = acc.out;
acc.in     = add.out;      acc.write_en = %0 ? 1'd1;
```

**After (pipelined):**
```calyx
// Stage 1: latch multiply result into pipeline register
mult.left         = top;           mult.right        = left;
mult_reg.in       = mult.out;      mult_reg.write_en = %0 ? 1'd1;
// Stage 2: accumulate from pipeline register (breaks critical path)
add.left          = mult_reg.out;  add.right         = acc.out;
acc.in            = add.out;       acc.write_en      = %0 ? 1'd1;
```

Both stages still execute within the same `static<1>` clock cycle, so the PE's external interface and the systolic array's scheduling are unchanged. The benefit is purely in synthesis: the two critical-path segments (`mult` and `add+acc`) are now separated by a register, allowing the synthesizer to target a higher clock frequency.

### Loop Bound Adjustment

The extra register in the datapath means the last multiply result is latched one cycle later. The while loop bound is increased from 14 to 15 (`STEPS_LIMIT = 15`) to give the final wave-front one additional cycle to flush through `mult_reg` and reach `acc`.

### Result

| Metric | Value |
|--------|-------|
| Correctness | PASS |
| Simulation cycles | **55** |
| Cycle overhead vs. unpipelined | +3 (2 extra loop iterations + scheduling) |
| Synthesis critical-path benefit | `mult` and `add+acc` now in separate pipeline stages |

---

## Summary

| Phase | Design | Cycles | Speedup |
|-------|--------|--------|---------|
| 2 | Naive triple-loop (`matmul.futil`) | 804 | 1× |
| 3 | Static systolic array (`systolic.futil`) | 52 | 15.5× |
| 4 | Pipelined PE (`systolic.futil`, updated) | 55 | 14.6× |

---

## File Reference

| File | Description |
|------|-------------|
| `matmul.futil` | Naive 4×4 matrix multiply in Calyx IR |
| `gen_systolic.py` | Python generator for the systolic array (Phase 3 + 4) |
| `systolic.futil` | Generated systolic array IR (do not edit directly) |
| `gen_data.py` | Generates `data.json` (matmul) and `data_systolic.json` (systolic) |
| `data.json` | Input matrices for matmul simulation |
| `data_systolic.json` | Pre-skewed input memories for systolic simulation |
| `out.json` | Matmul simulation output (`cycles: 804`) |
| `out_systolic.json` | Systolic simulation output (`cycles: 55`) |
| `primitives/` | Calyx primitive library (core, binary_operators, seq memories, unsynthesizable) |
