import json
import random
import numpy as np

random.seed(42)

A = [[random.randint(1, 5) for _ in range(4)] for _ in range(4)]
B = [[random.randint(1, 5) for _ in range(4)] for _ in range(4)]
C = [[0] * 4 for _ in range(4)]

mem_format = {"numeric_type": "bitnum", "is_signed": False, "width": 32}

data = {
    "A": {"data": A, "format": mem_format},
    "B": {"data": B, "format": mem_format},
    "C": {"data": C, "format": mem_format},
}

with open("data.json", "w") as f:
    json.dump(data, f, indent=2)

print("A =", A)
print("B =", B)
print("Expected C = A @ B:")
A_np = np.array(A)
B_np = np.array(B)
print(A_np @ B_np)


# ── Systolic array data (pre-skewed 1-D seq_mem_d1 memories) ──────────────
# Depth 16 matches MEM_DEPTH in gen_systolic.py so address widths align.
MEM_DEPTH = 16

def skew_row(row, skew, depth=MEM_DEPTH):
    """Insert `skew` leading zeros, append row data, zero-pad to `depth`."""
    padded = [0] * skew + list(row)
    padded += [0] * (depth - len(padded))
    return padded[:depth]

def skew_col(matrix, col_idx, skew, depth=MEM_DEPTH):
    """Extract column col_idx and pre-skew it."""
    col = [matrix[r][col_idx] for r in range(len(matrix))]
    padded = [0] * skew + col
    padded += [0] * (depth - len(padded))
    return padded[:depth]

systolic_data = {}
for i in range(4):
    systolic_data[f"A{i}"] = {"data": skew_row(A[i], skew=i), "format": mem_format}
for j in range(4):
    systolic_data[f"B{j}"] = {"data": skew_col(B, j, skew=j), "format": mem_format}
for i in range(4):
    systolic_data[f"C{i}"] = {"data": [0] * 4,
                               "format": {"numeric_type": "bitnum", "is_signed": False, "width": 32}}

with open("data_systolic.json", "w") as f:
    json.dump(systolic_data, f, indent=2)

print("\nGenerated data_systolic.json")
for i in range(4):
    print(f"  A{i} = {systolic_data[f'A{i}']['data']}")
for j in range(4):
    print(f"  B{j} = {systolic_data[f'B{j}']['data']}")
