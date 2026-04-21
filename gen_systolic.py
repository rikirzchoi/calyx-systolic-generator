"""
gen_systolic.py  –  Emit a 4×4 statically-timed systolic array in Calyx.

Architecture:
  • static<1> mac_pe component: combinational multiply (std_unsyn_mult),
    registered forwarding (r_fwd / d_fwd), and an accumulator (acc).
  • main: 16 PE instances, 8 pre-skewed 1-D input memories (A0-A3, B0-B3),
    4 output memories (C0-C3, one row of C each), a 4-bit step counter.
  • static<1> group pulse_array fires the whole array each cycle; reads
    memories with steps.out as address and wires PE grid connections.
  • While loop runs 15 iterations (14 for fill/drain + 1 to flush mult_reg
    pipeline stage).  Body is static seq { pulse_array; upd_steps; } = 2 cycles.
  • After the loop, 16 sequential write-back groups push PE accumulator
    values into C memories.
"""

N = 4           # matrix / array dimension
MEM_DEPTH = 16  # must hold STEPS_LIMIT addresses; steps is 4-bit → depth 16
C_DEPTH = 4     # output memory depth (one row of C)
STEPS_LIMIT = 15
STEPS_BITS = 4  # 2^4=16 > 14
IDX_BITS_IN = 4  # ceil(log2(16)) — matches STEPS_BITS so no width mismatch
IDX_BITS_OUT = 2  # ceil(log2(4))


def indent(n, s):
    return "  " * n + s


def emit_mac_pe():
    lines = []

    def E(s=""):
        lines.append(s)

    E("// ─────────────────────────────────────────────────────────────────────")
    E("// MAC Processing Element (pipelined, static latency = 1 cycle)")
    E("//   • std_unsyn_mult: combinational multiply (non-synthesisable)")
    E("//   • mult_reg:       pipeline register breaks mult→add critical path")
    E("//   • r_fwd / d_fwd:  registered forwarding (left→right, top→bottom)")
    E("//   • acc:            running accumulator")
    E("//   • result:         continuous output so main can read final value")
    E("// ─────────────────────────────────────────────────────────────────────")
    E("static<1> component mac_pe(top: 32, left: 32)")
    E("    -> (bottom: 32, right: 32, result: 32) {")
    E("  cells {")
    E("    acc      = std_reg(32);")
    E("    mult_reg = std_reg(32);")
    E("    r_fwd    = std_reg(32);")
    E("    d_fwd    = std_reg(32);")
    E("    add      = std_add(32);")
    E("    mult     = std_unsyn_mult(32);")
    E("  }")
    E("  wires {")
    E("    static<1> group step {")
    E("      // Stage 1: latch multiply result into pipeline register")
    E("      mult.left          = top;")
    E("      mult.right         = left;")
    E("      mult_reg.in        = mult.out;")
    E("      mult_reg.write_en  = %0 ? 1'd1;")
    E("      // Stage 2: accumulate from pipeline register (breaks critical path)")
    E("      add.left           = mult_reg.out;")
    E("      add.right          = acc.out;")
    E("      acc.in             = add.out;")
    E("      acc.write_en       = %0 ? 1'd1;")
    E("      // Register forwarding: propagate inputs to outputs next cycle")
    E("      r_fwd.in         = left;")
    E("      r_fwd.write_en   = %0 ? 1'd1;")
    E("      d_fwd.in         = top;")
    E("      d_fwd.write_en   = %0 ? 1'd1;")
    E("    }")
    E("    // Continuous: output registered values to neighbours")
    E("    bottom = d_fwd.out;")
    E("    right  = r_fwd.out;")
    E("    result = acc.out;")
    E("  }")
    E("  control {")
    E("    static seq { step; }")
    E("  }")
    E("}")
    return "\n".join(lines)


def emit_main():
    lines = []

    def E(s=""):
        lines.append(s)

    E("component main() -> () {")
    E("  cells {")

    # ── Input memories ────────────────────────────────────────────────────────
    E("    // Pre-skewed A rows (size 8 = 4 data + 3 skew zeros + 1 pad)")
    for i in range(N):
        E(f"    @external A{i} = seq_mem_d1(32, {MEM_DEPTH}, {IDX_BITS_IN});")
    E("    // Pre-skewed B columns")
    for j in range(N):
        E(f"    @external B{j} = seq_mem_d1(32, {MEM_DEPTH}, {IDX_BITS_IN});")
    E("    // Output memories: C_i stores row i of the result")
    for i in range(N):
        E(f"    @external C{i} = seq_mem_d1(32, {C_DEPTH}, {IDX_BITS_OUT});")

    # ── PE grid ───────────────────────────────────────────────────────────────
    E("    // 4×4 PE grid")
    for i in range(N):
        for j in range(N):
            E(f"    pe_{i}{j} = mac_pe();")

    # ── Step counter ──────────────────────────────────────────────────────────
    E(f"    steps     = std_reg({STEPS_BITS});")
    E(f"    steps_add = std_add({STEPS_BITS});")
    E(f"    lt15      = std_lt({STEPS_BITS});")

    E("  }")
    E("  wires {")

    # ── Loop condition ────────────────────────────────────────────────────────
    E("    // Condition: steps < 15  (extra cycle flushes the mult_reg pipeline stage)")
    E("    comb group cond_steps {")
    E("      lt15.left  = steps.out;")
    E(f"      lt15.right = {STEPS_BITS}'d{STEPS_LIMIT};")
    E("    }")

    # ── Init steps ────────────────────────────────────────────────────────────
    E("    group init_steps {")
    E(f"      steps.in       = {STEPS_BITS}'d0;")
    E("      steps.write_en = 1'd1;")
    E("      init_steps[done] = steps.done;")
    E("    }")

    # ── Increment steps (static so it fits inside static seq body) ────────────
    E("    static<1> group upd_steps {")
    E("      steps_add.left  = steps.out;")
    E(f"      steps_add.right = {STEPS_BITS}'d1;")
    E("      steps.in        = steps_add.out;")
    E("      steps.write_en  = %0 ? 1'd1;")
    E("    }")

    # ── One-cycle array pulse ─────────────────────────────────────────────────
    E("    // pulse_array: drive all memory reads and wire the PE datapath.")
    E("    // Because seq_mem has 1-cycle latency, the PEs consume data from")
    E("    // the PREVIOUS iteration's address, giving correct skew alignment.")
    E("    static<1> group pulse_array {")

    # Memory reads
    E("      // ── Memory reads (addr = steps.out) ──────────────────────────")
    for i in range(N):
        E(f"      A{i}.addr0 = steps.out; A{i}.content_en = 1'd1;")
    for j in range(N):
        E(f"      B{j}.addr0 = steps.out; B{j}.content_en = 1'd1;")

    # PE wiring – row by row
    for i in range(N):
        E(f"      // ── Row {i} ──────────────────────────────────────────────────────")
        for j in range(N):
            # left input
            if j == 0:
                left_src = f"A{i}.read_data"
            else:
                left_src = f"pe_{i}{j-1}.right"
            # top input
            if i == 0:
                top_src = f"B{j}.read_data"
            else:
                top_src = f"pe_{i-1}{j}.bottom"
            E(f"      pe_{i}{j}.go   = %0 ? 1'd1;")
            E(f"      pe_{i}{j}.left = {left_src}; pe_{i}{j}.top = {top_src};")

    E("    }")

    # ── Write-back groups ─────────────────────────────────────────────────────
    E("    // Write-back: after computation, copy each PE's acc_out to C_i[j].")
    for i in range(N):
        E(f"    // Row {i} → C{i}")
        for j in range(N):
            grp = f"wb_c{i}j{j}"
            E(f"    group {grp} {{")
            E(f"      C{i}.addr0      = {IDX_BITS_OUT}'d{j};")
            E(f"      C{i}.content_en = 1'd1;")
            E(f"      C{i}.write_en   = 1'd1;")
            E(f"      C{i}.write_data = pe_{i}{j}.result;")
            E(f"      {grp}[done]     = C{i}.done;")
            E(f"    }}")

    E("  }")

    # ── Control ───────────────────────────────────────────────────────────────
    E("  control {")
    E("    seq {")
    E("      init_steps;")
    E(f"      while lt15.out with cond_steps {{")
    E("        static seq { pulse_array; upd_steps; }")
    E("      }")
    E("      // Write all 16 accumulator values into C memories.")
    E("      // Writes within each C_i must be sequential (single-port memory).")
    E("      // Writes to different C memories are independent → use par.")
    for j in range(N):
        grps = " ".join(f"wb_c{i}j{j};" for i in range(N))
        E(f"      par {{ {grps} }}")
    E("    }")
    E("  }")
    E("}")

    return "\n".join(lines)


def gen_systolic_futil():
    header = [
        'import "primitives/core.futil";',
        'import "primitives/binary_operators.futil";',
        'import "primitives/memories/seq.futil";',
        'import "primitives/unsynthesizable.futil";',
        "",
    ]
    return "\n".join(header) + "\n" + emit_mac_pe() + "\n\n" + emit_main() + "\n"


if __name__ == "__main__":
    src = gen_systolic_futil()
    with open("systolic.futil", "w") as f:
        f.write(src)
    print("Generated systolic.futil")
    print(f"  • mac_pe: pipelined static<1> component (mult_reg breaks critical path)")
    print(f"  • main:   {N}×{N} PE grid, {N*2} input memories, {N} output memories")
    print(f"  • while loop: {STEPS_LIMIT} iterations × 2 cycles = {STEPS_LIMIT*2} total cycles")
