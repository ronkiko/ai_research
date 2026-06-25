"""Минимальные проверки движка chip."""
from __future__ import annotations

from pathlib import Path

from .chip import ChipEngine, analyze_chip
from .registry import ENGINES
from .targets import target_for_game

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


def test_cmos_breakdown_has_no_legacy_aliases():
    body = _fixture_body("mlp_lie_detector_xnor_case_a.md")
    analysis = analyze_chip("mlp", body)
    assert analysis is not None
    assert hasattr(analysis.cmos, "extracted") is False
    assert hasattr(analysis.cmos, "optimized_reference") is False


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


def test_report_first_screen_clean():
    report = ChipEngine.render("mlp", _fixture_body("mlp_lie_detector_xnor_case_a.md"))
    lines = report.splitlines()
    first_35 = lines[:35]

    assert "BOOLEAN CHIP SCHEME" in first_35
    assert any(line.startswith("CMOS COST: 16T") for line in first_35)
    assert "PROOF" in first_35
    assert "RAW NEURON" not in first_35

    below = "\n".join(lines[35:])
    assert "RAW NEURON" in below


def test_target_module_lie_detector_xnor():
    target = target_for_game("lie_detector")
    assert target is not None
    assert target.role == "XNOR"
    assert target.mask == (1, 0, 0, 1)
    assert target_for_game("unknown_game") is None


def test_audit_script_importable():
    """Smoke: модуль audit_chip_snapshots импортируется без запуска прогона."""
    import importlib.util

    path = _FIXTURE_DIR.parent / "audit_chip_snapshots.py"
    spec = importlib.util.spec_from_file_location("audit_chip_snapshots", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "main")
    # build_summary работает на пустом вводе и не падает.
    summary = module.build_summary([])
    assert summary["total"] == 0
    assert summary["matched"] == 0
    assert summary["failed"] == 0


if __name__ == "__main__":
    test_case_a_fixture_real_xnor_match()
    print("✓ Case A fixture")
    test_cmos_breakdown_has_no_legacy_aliases()
    print("✓ no legacy aliases")
    test_synthetic_xnor_remains_supported()
    print("✓ synthetic XNOR")
    test_unsupported_model_message()
    print("✓ unsupported model message")
    test_hotkey_registry()
    print("✓ hotkey registry")
    test_report_first_screen_clean()
    print("✓ first screen clean")
    test_target_module_lie_detector_xnor()
    print("✓ target module")
    test_audit_script_importable()
    print("✓ audit script importable")
    print("All chip engine tests passed.")