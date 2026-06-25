"""Минимальные проверки движка chip."""
from __future__ import annotations

from pathlib import Path

from .chip import ChipEngine, analyze_chip
from .registry import ENGINES

_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "examples" / "chip_fixtures"


def _synthetic_xnor_snapshot() -> str:
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


def _fixture_body(name: str) -> str:
    return (_FIXTURE_DIR / name).read_text(encoding="utf-8")


def test_case_a_fixture_real_xnor_match():
    body = _fixture_body("mlp_lie_detector_xnor_case_a.md")
    analysis = analyze_chip("mlp", body)
    assert analysis is not None, "fixture should parse as supported mlp snapshot"
    assert analysis.real_network.output_mask == (1, 0, 0, 1)
    assert analysis.output_role == "XNOR"
    assert analysis.target_role == "XNOR"
    assert analysis.real_network.solves_target is True
    assert analysis.result == "MATCH"
    assert analysis.cmos.functional.transistors == 16

    report = ChipEngine.render("mlp", body)
    assert "Target: XNOR" in report
    assert "Network: XNOR" in report
    assert "Result: MATCH" in report
    assert "CMOS COST: 16T" in report
    assert "Network: NAND" not in report


def test_synthetic_xnor_remains_supported():
    analysis = analyze_chip("mlp", _synthetic_xnor_snapshot())
    assert analysis is not None
    assert analysis.output_role == "XNOR"
    assert analysis.cmos.functional.transistors == 16


def test_unsupported_model_message():
    report = ChipEngine.render("bias", "nothing here")
    assert "2 → N → 1" in report


def test_hotkey_registry():
    assert ENGINES[0].info.key == "chip"
    hotkeys = [engine.info.hotkey for engine in ENGINES]
    assert len(hotkeys) == len(set(hotkeys))


def test_report_keeps_debug_below_main_sections():
    report = ChipEngine.render("mlp", _fixture_body("mlp_lie_detector_xnor_case_a.md"))
    first_40 = report.splitlines()[:40]
    assert "BOOLEAN CHIP SCHEME" in first_40
    assert any(line.startswith("CMOS COST: ") for line in first_40)
    assert "PROOF" in first_40

    debug_index = report.index("DEBUG / RAW NEURON VIEW")
    assert "h0:" not in report[:debug_index]
    assert "Raw hidden approximation: not used for CMOS cost." in report[debug_index:]


if __name__ == "__main__":
    test_case_a_fixture_real_xnor_match()
    print("✓ Case A fixture")
    test_synthetic_xnor_remains_supported()
    print("✓ synthetic XNOR")
    test_unsupported_model_message()
    print("✓ unsupported model message")
    test_hotkey_registry()
    print("✓ hotkey registry")
    test_report_keeps_debug_below_main_sections()
    print("✓ compact report layout")
    print("All chip engine tests passed.")
