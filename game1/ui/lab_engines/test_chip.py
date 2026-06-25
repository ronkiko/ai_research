"""Минимальные unit-style проверки движка chip."""
from __future__ import annotations

from .chip import CmosBreakdown, CmosCost, ChipAnalysis, OutputCombiner, estimate_cmos, synthesize_expression
from .common import classify_mask
from .registry import ENGINES


def _synthetic_xnor_snapshot() -> str:
    """mlp snapshot: h0=NOR, h1=AND, output=OR -> XNOR."""
    return """## Synthetic XNOR

- **Модель:** mlp
- **Игра:** lie_detector
- **Режим:** supervised

### Веса

  `0.weight[0]` = -1.0
  `0.weight[1]` = -1.0
  `0.weight[2]` = 1.0
  `0.weight[3]` = 1.0
  `0.bias[0]` = 0.5
  `0.bias[1]` = -1.5
  `2.weight[0]` = 1.0
  `2.weight[1]` = 1.0
  `2.bias[0]` = -0.5
"""


def test_nor_mask():
    role, _ = classify_mask((1, 0, 0, 0))
    assert role == "NOR", f"expected NOR, got {role}"


def test_and_mask():
    role, _ = classify_mask((0, 0, 0, 1))
    assert role == "AND", f"expected AND, got {role}"


def test_xnor_optimized_reference_16t():
    dummy_combiner = OutputCombiner(
        active_hidden=[],
        positive_hidden=[],
        negative_hidden=[],
        ignored_hidden=[],
        bias=0.0,
        kind="THRESHOLD",
        expression="",
        cost=CmosCost([], 0, 0, []),
        notes=[],
    )
    analysis = ChipAnalysis(
        model_key="dummy",
        hidden=[],
        output_mask=(1, 0, 0, 1),
        output_role="XNOR",
        extracted_expression="",
        final_function="",
        truth_match=True,
        output_combiner=dummy_combiner,
        cmos=CmosBreakdown(
            extracted=CmosCost([], 0, 0, []),
            optimized_reference=None,
        ),
        warnings=[],
    )
    cmos = estimate_cmos(analysis)
    assert cmos.optimized_reference is not None
    assert cmos.optimized_reference.transistors == 16, (
        f"expected 16T optimized reference, got {cmos.optimized_reference.transistors}T"
    )
    assert "NOR2" in cmos.optimized_reference.gates
    assert "AND2" in cmos.optimized_reference.gates
    assert "OR2" in cmos.optimized_reference.gates


def test_xnor_extracted_network_cost_16t():
    """Стандартный XNOR-паттерн NOR+AND->OR должен давать extracted cost 16T, не 26T."""
    from .chip import analyze_chip

    analysis = analyze_chip("mlp", _synthetic_xnor_snapshot())
    assert analysis is not None, "failed to analyze synthetic XNOR snapshot"
    assert analysis.output_role == "XNOR", f"expected XNOR, got {analysis.output_role}"
    assert analysis.output_combiner.kind == "OR", (
        f"expected OR output combiner, got {analysis.output_combiner.kind}"
    )
    extracted = analysis.cmos.extracted
    assert extracted.transistors == 16, (
        f"expected extracted cost 16T, got {extracted.transistors}T"
    )
    assert extracted.gates.count("NOR2") == 1
    assert extracted.gates.count("AND2") == 1
    assert extracted.gates.count("OR2") == 1
    assert analysis.truth_match is True, "extracted network should match final XNOR function"


def test_xnor_expression():
    expr = synthesize_expression((1, 0, 0, 1))
    assert "NOR" in expr and "AND" in expr, f"unexpected expression: {expr}"


def test_hotkey_registry():
    assert ENGINES[0].info.key == "chip", f"expected chip first, got {ENGINES[0].info.key}"
    hotkeys = [e.info.hotkey for e in ENGINES]
    assert len(hotkeys) == len(set(hotkeys)), f"duplicate hotkeys: {hotkeys}"


def test_unsupported_model_message():
    from .chip import ChipEngine

    report = ChipEngine.render("bias", "nothing here")
    assert report is not None, "unsupported model should return a message, not None"
    assert "2 → N → 1" in report, "message should explain supported architecture"


if __name__ == "__main__":
    test_nor_mask()
    print("✓ NOR mask")
    test_and_mask()
    print("✓ AND mask")
    test_xnor_optimized_reference_16t()
    print("✓ XNOR optimized reference = 16T")
    test_xnor_extracted_network_cost_16t()
    print("✓ XNOR extracted network cost = 16T")
    test_xnor_expression()
    print("✓ XNOR expression")
    test_hotkey_registry()
    print("✓ hotkey registry")
    test_unsupported_model_message()
    print("✓ unsupported model message")
    print("All chip engine tests passed.")
