"""Минимальные unit-style проверки движка chip."""
from __future__ import annotations

from .chip import CmosCost, ChipAnalysis, estimate_cmos, synthesize_expression
from .common import classify_mask


def test_nor_mask():
    role, _ = classify_mask((1, 0, 0, 0))
    assert role == "NOR", f"expected NOR, got {role}"


def test_and_mask():
    role, _ = classify_mask((0, 0, 0, 1))
    assert role == "AND", f"expected AND, got {role}"


def test_xnor_cmos_cost():
    analysis = ChipAnalysis(
        model_key="dummy",
        hidden=[],
        output_mask=(1, 0, 0, 1),
        output_role="XNOR",
        expression=synthesize_expression((1, 0, 0, 1)),
        cmos=CmosCost([], 0, 0, []),
        warnings=[],
    )
    cmos = estimate_cmos(analysis)
    assert cmos.transistors == 16, f"expected 16T, got {cmos.transistors}T"
    assert "NOR2" in cmos.gates
    assert "AND2" in cmos.gates
    assert "OR2" in cmos.gates


def test_xnor_expression():
    expr = synthesize_expression((1, 0, 0, 1))
    assert "NOR" in expr and "AND" in expr, f"unexpected expression: {expr}"


if __name__ == "__main__":
    test_nor_mask()
    print("✓ NOR mask")
    test_and_mask()
    print("✓ AND mask")
    test_xnor_cmos_cost()
    print("✓ XNOR CMOS cost = 16T")
    test_xnor_expression()
    print("✓ XNOR expression")
    print("All chip engine tests passed.")
