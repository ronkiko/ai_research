"""Валидация моделей: каноническое поведение, стабильность, ресет, переключение.

Прогоняет:
  модели: Bias, Logistic
    × игры: Ball, Kormushka, Drift, Dealer
    × режимы: supervised, rl, adaptive, play
  100k шагов каждый → отчёт + assert канонического поведения.

Запуск:
  python3 examples/validate_models.py
"""
from __future__ import annotations

import sys
import time
sys.path.insert(0, ".")

from collections import deque
from typing import Dict, List, Tuple

from modules.base import Observation, Outcome
from modules.core.mechanics.ball import BallMechanics
from modules.core.mechanics.kormushka import KormushkaMechanics
from modules.core.mechanics.drift import DriftBallMechanics
from modules.core.mechanics.dealer import DealerMechanics
from modules.game_api.models.bias import BiasModel
from modules.game_api.models.logistic import LogisticModel
from modules.game_api.models.base import Model
from modules.game_api.modes import SUPERVISED, RL, PLAY, ADAPTIVE

STEPS = 100_000
REPORT_EVERY = 20_000


def run_one(
    model: Model,
    game,
    mode: str,
    steps: int = STEPS,
    seed: int = 42,
) -> dict:
    """Прогнать модель × игру × режим, вернуть словарь с метриками."""
    game.sit()
    model.train_mode = mode
    model.reset()

    hits = deque(maxlen=1000)
    rewards = []
    params_hist: Dict[str, list] = {}

    for i in range(steps):
        obs = game.observe()
        action = model.act(obs)
        out = game.step(action)
        model.train(obs, out)

        hits.append(1 if out.reward > 0 else 0)
        rewards.append(out.reward)

        if i % 1000 == 0:
            for k, v in model.params().items():
                params_hist.setdefault(k, []).append(v)

    st = model.stats()

    acc_last = sum(list(hits)[-1000:]) / 1000
    acc_all = sum(rewards) / len(rewards)  # average reward, -1..+1

    result = {
        "model": model.KEY,
        "game": game.KEY,
        "mode": mode,
        "steps": steps,
        "acc_last_1000": acc_last,
        "avg_reward": acc_all,
        "params": st.params,
        "logit": st.logit,
        "prob": st.prob,
        "n_params": st.info.n_params,
        "n_neurons": st.n_neurons,
    }
    return result


def check_canonical(results: List[dict]) -> List[str]:
    """Проверить, что результаты соответствуют каноническому поведению."""
    errors = []

    checks = [
        # (model, game, mode, check_fn, description)
    ]

    def add(model, game, mode, fn, desc):
        checks.append((model, game, mode, fn, desc))

    for r in results:
        mo = r["model"]
        ga = r["game"]
        md = r["mode"]
        p = r["params"]
        acc = r["acc_last_1000"]

        # --- Bias × Ball × supervised: b ~ 0.85, acc ~ 70% ---
        if mo == "bias" and ga == "ball" and md == SUPERVISED:
            add(mo, ga, md, lambda r=r: abs(r["params"].get("b", 0)) < 20,
                "b не ушёл в бесконечность")
            add(mo, ga, md, lambda r=r: 0.6 <= r["acc_last_1000"] <= 0.75,
                "точность 60-75% (оптимум 70%)")

        # --- Bias × Ball × rl: b ~ 10-15 (L2 равновесие) ---
        elif mo == "bias" and ga == "ball" and md == RL:
            add(mo, ga, md, lambda r=r: abs(r["params"].get("b", 0)) < 50,
                "b не ушёл в бесконечность (RL+L2)")
            add(mo, ga, md, lambda r=r: 0.6 <= r["acc_last_1000"] <= 0.75,
                "RL точность 60-75%")
            add(mo, ga, md, lambda r=r: r["params"].get("b", 0) > 3,
                "b > 3 (RL накручивает bias)")

        # --- Bias × Ball × play: b=0, argmax(p=0.5)→всегда право → acc~70% ---
        elif mo == "bias" and ga == "ball" and md == PLAY:
            add(mo, ga, md, lambda r=r: abs(r["params"].get("b", 0)) < 0.01,
                "b ≈ 0 (play не учится)")

        # --- Bias × Ball × adaptive: same as RL ---
        elif mo == "bias" and ga == "ball" and md == ADAPTIVE:
            add(mo, ga, md, lambda r=r: abs(r["params"].get("b", 0)) < 50,
                "adaptive: b не ушёл в бесконечность")

        # --- Bias × Kormushka: не может выучить паттерн (нет входа) ---
        elif mo == "bias" and ga == "kormushka":
            add(mo, ga, md, lambda r=r: 0.40 <= r["acc_last_1000"] <= 0.62,
                f"{md}: bias на kormushka не лучше 62% (слепой)")

        # --- Logistic × Kormushka × supervised: w > 0, acc ~ 68% ---
        elif mo == "logistic" and ga == "kormushka" and md == SUPERVISED:
            add(mo, ga, md, lambda r=r: r["params"].get("w", 0) > 0.5,
                "w > 0 (мир повторяется)")
            add(mo, ga, md, lambda r=r: 0.62 <= r["acc_last_1000"] <= 0.75,
                "logistic на kormushka 62-75%")

        # --- Logistic × Kormushka × play: w=0, acc~50% ---
        elif mo == "logistic" and ga == "kormushka" and md == PLAY:
            add(mo, ga, md, lambda r=r: abs(r["params"].get("w", 0)) < 0.01,
                "play: w ≈ 0")
            add(mo, ga, md, lambda r=r: 0.45 <= r["acc_last_1000"] <= 0.55,
                "play: acc ≈ 50%")

        # --- Logistic × Kormushka × rl: w > 0, acc ~ 68% ---
        elif mo == "logistic" and ga == "kormushka" and md in (RL, ADAPTIVE):
            add(mo, ga, md, lambda r=r: r["params"].get("w", 0) > 0.3,
                f"{md}: w > 0")
            add(mo, ga, md, lambda r=r: 0.60 <= r["acc_last_1000"] <= 0.75,
                f"{md}: acc 60-75%")

        # --- Bias × Drift: oscillates, acc ~60-68% for supervised ---
        elif mo == "bias" and ga == "drift":
            if md == SUPERVISED:
                add(mo, ga, md, lambda r=r: r["acc_last_1000"] >= 0.55,
                    "supervised на drift ≥ 55%")
            elif md == RL:
                add(mo, ga, md, lambda r=r: abs(r["params"].get("b", 0)) < 20,
                    "rl на drift: b стабилен")

        # --- Logistic × Dealer × supervised: предсказывает карту → ПРОИГРЫВАЕТ ---
        elif mo == "logistic" and ga == "dealer" and md == SUPERVISED:
            add(mo, ga, md, lambda r=r: r["params"].get("w", 0) > 0.2,
                "supervised: w > 0 (учит карту)")
            add(mo, ga, md, lambda r=r: r["acc_last_1000"] < 0.5,
                "supervised: проигрывает (acc < 50%)")

        # --- Logistic × Dealer × rl: играет наоборот → ВЫИГРЫВАЕТ ---
        elif mo == "logistic" and ga == "dealer" and md in (RL, ADAPTIVE):
            add(mo, ga, md, lambda r=r: r["params"].get("w", 0) < -0.2,
                f"{md}: w < 0 (ставит против карты)")
            add(mo, ga, md, lambda r=r: r["acc_last_1000"] > 0.5,
                f"{md}: выигрывает (acc > 50%)")

        # --- Bias × Dealer × supervised: может только в одну сторону ---
        elif mo == "bias" and ga == "dealer" and md == SUPERVISED:
            add(mo, ga, md, lambda r=r: r["params"].get("b", 0) > 0,
                "bias на dealer: b > 0 (право — 70%)")
            add(mo, ga, md, lambda r=r: r["acc_last_1000"] < 0.5,
                "bias на dealer supervised: проигрывает")

        # --- Bias × Dealer × rl: учится ставить налево (против большинства) ---
        elif mo == "bias" and ga == "dealer" and md == RL:
            add(mo, ga, md, lambda r=r: r["params"].get("b", 0) < 0,
                "bias на dealer rl: b < 0 (налево = 70% выигрыш)")

    for model, game, mode, fn, desc in checks:
        try:
            assert fn(), f"{model}×{game}×{mode}: {desc}"
        except AssertionError as e:
            errors.append(str(e))

    return errors


def print_report(results: List[dict], errors: List[str]):
    """Табличный отчёт."""
    header = f"{'model':10s} {'game':10s} {'mode':12s} {'acc':6s} {'reward':7s} {'params':30s}"
    sep = "─" * len(header)
    print(sep)
    print(header)
    print(sep)
    for r in sorted(results, key=lambda x: (x["model"], x["game"], x["mode"])):
        params_str = " ".join(f"{k}={v:+.2f}" for k, v in r["params"].items())
        print(
            f"{r['model']:10s} {r['game']:10s} {r['mode']:12s} "
            f"{r['acc_last_1000']:.0%}  "
            f"{r['avg_reward']:+.2f}   "
            f"{params_str[:30]:30s}"
        )
    print(sep)
    print(f"\nПроверок: {len(results)}")
    if errors:
        print(f"\nОШИБКИ ({len(errors)}):")
        for e in errors:
            print(f"  ✗ {e}")
    else:
        print("\nВсе проверки пройдены ✓")


def test_reset():
    """Проверить, что reset() возвращает веса в ноль."""
    model = LogisticModel(seed=0)
    game = KormushkaMechanics(seed=1)
    game.sit()
    model.train_mode = SUPERVISED
    for _ in range(1000):
        obs = game.observe()
        action = model.act(obs)
        out = game.step(action)
        model.train(obs, out)
    model.reset()
    for k, v in model.params().items():
        assert abs(v) < 1e-10, f"reset не обнулил {k}={v}"
    assert model.logit(Observation(state=(0,))) == 0.0
    assert model._steps == 0
    assert model._adaptive_cooloff == 0
    assert len(model._adaptive_buffer) == 0
    return True


def test_model_switch():
    """Проверить, что переключение между моделями сохраняет веса."""
    from modules.game_api.adapter import AiAdapter
    from modules.game_api.models.bias import BiasModel
    from modules.game_api.models.logistic import LogisticModel

    adapter = AiAdapter({"bias": BiasModel, "logistic": LogisticModel})
    adapter.select("bias")
    adapter.set_mode(SUPERVISED)


    # Потренировать Bias на Ball
    table = BallMechanics(seed=1)
    table.sit()
    for _ in range(5000):
        obs = table.observe()
        action = adapter.act(obs)
        out = table.step(action)
        adapter.train(obs, out)

    bias_stats = adapter.stats("bias")
    bias_b = bias_stats.params.get("b", 0)
    assert bias_b != 0, "Bias не обучился"

    # Переключиться на Logistic, потренировать
    adapter.select("logistic")
    table2 = KormushkaMechanics(seed=2)
    table2.sit()
    for _ in range(5000):
        obs = table2.observe()
        action = adapter.act(obs)
        out = table2.step(action)
        adapter.train(obs, out)

    logist_stats = adapter.stats("logistic")
    assert logist_stats.params.get("w", 0) != 0, "Logistic не обучился"

    # Вернуться на Bias — веса должны сохраниться
    adapter.select("bias")
    bias_stats2 = adapter.stats("bias")
    assert abs(bias_stats2.params.get("b", 0) - bias_b) < 0.01, \
        f"Bias веса не сохранились: было {bias_b}, стало {bias_stats2.params.get('b', 0)}"

    return True


def test_canonical_formulas():
    """Проверить, что logit/features соответствуют документации."""
    # BiasModel: logit = b, features = {b: 1.0}
    bm = BiasModel(seed=0)
    obs = Observation(state=(42,))  # должен игнорировать вход
    assert bm.logit(obs) == 0.0, "Bias logit должен быть 0 (b=0)"
    bm._apply({"b": 3.0})
    assert bm.logit(obs) == 3.0, f"Bias logit должен быть 3, получил {bm.logit(obs)}"
    assert bm.features(obs) == {"b": 1.0}, \
        f"Bias features всегда 1, получил {bm.features(obs)}"
    # Проверка: Bias игнорирует вход
    obs2 = Observation(state=(99,))
    assert bm.logit(obs2) == 3.0, "Bias должен игнорировать state"

    # LogisticModel: logit = w*x + b, features = {w: x, b: 1.0}
    lm = LogisticModel(seed=0)
    obs0 = Observation(state=(0,))
    obs1 = Observation(state=(1,))
    assert lm.logit(obs0) == 0.0
    assert lm.logit(obs1) == 0.0

    lm._apply({"w": 2.0, "b": -1.0})
    assert lm.logit(obs0) == -1.0, f"logit(0) = w*0 + b = -1, получил {lm.logit(obs0)}"
    assert lm.logit(obs1) == 1.0, f"logit(1) = w*1 + b = 1, получил {lm.logit(obs1)}"

    feats0 = lm.features(obs0)
    feats1 = lm.features(obs1)
    assert feats0 == {"w": 0.0, "b": 1.0}, f"features(0) = {{w:0, b:1}}, получил {feats0}"
    assert feats1 == {"w": 1.0, "b": 1.0}, f"features(1) = {{w:1, b:1}}, получил {feats1}"

    return True


def main():
    models = [BiasModel, LogisticModel]
    games = [BallMechanics, KormushkaMechanics, DriftBallMechanics, DealerMechanics]
    modes = [SUPERVISED, RL, ADAPTIVE, PLAY]
    seed = 42

    print("=== Валидация моделей ===")
    print(f"Шагов на комбинацию: {STEPS}")
    print(f"{len(models)} моделей × {len(games)} игр × {len(modes)} режимов = {len(models)*len(games)*len(modes)} комбинаций")
    print()

    # 1. Базовые формулы
    print("1. Канонические формулы logit/features...", end=" ")
    test_canonical_formulas()
    print("✓")

    # 2. Прогон
    print(f"2. Прогон {STEPS} шагов каждой комбинации...")
    results = []
    t0 = time.time()
    for ClsModel in models:
        for ClsGame in games:
            for mode in modes:
                model = ClsModel(seed=seed)
                game = ClsGame(seed=seed + 1)
                r = run_one(model, game, mode, seed=seed)
                results.append(r)
                elapsed = time.time() - t0
                print(f"  [{elapsed:5.0f}s] {model.KEY:8s} × {game.KEY:10s} × {mode:11s}"
                      f"  → acc={r['acc_last_1000']:.0%}  "
                      f"params={' '.join(f'{k}={v:.2f}' for k,v in r['params'].items())}")
    elapsed = time.time() - t0
    print(f"  Всего: {elapsed:.0f}s")

    # 3. Отчёт
    print("\n3. Проверка канонического поведения...")
    errors = check_canonical(results)
    print_report(results, errors)

    # 4. Reset
    print("\n4. Reset()...", end=" ")
    test_reset()
    print("✓")

    # 5. Переключение моделей
    print("5. Переключение моделей (сохранение весов)...", end=" ")
    test_model_switch()
    print("✓")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
