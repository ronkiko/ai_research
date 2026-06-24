"""Show logit -> p -> decision for BiasModel on Ball."""


def sigmoid(x: float) -> float:
    e = 2.718281828459045
    if x >= 0:
        return 1.0 / (1.0 + e ** (-x))
    ex = e ** x
    return ex / (1.0 + ex)


def main() -> None:
    print("=== BiasModel на Ball, обученная (b=0.85) ===")
    print()

    for b in range(-10, 11):
        b_val = b / 10
        p = sigmoid(b_val)
        decision = "право" if p >= 0.5 else "лево"
        confidence = max(p, 1 - p)
        print(
            f"b={b_val:+.1f}  →  logit={b_val:+.1f}  →  "
            f"p={p:.4f}  ({p*100:.0f}% право)  →  "
            f"{decision}  (уверенность {confidence:.0%})"
        )

    print()
    print("=== Ключевые точки ===")
    print("b=0.00:  logit=0.00  →  σ(0)=0.5000  →  50%  →  50/50, монетка")
    print("b=0.85:  logit=0.85  →  σ(0.85)=0.700  →  70%  →  ставит право в 70%")
    print("b=-0.85: logit=-0.85 →  σ(-0.85)=0.300 →  30%  →  чаще лево")


if __name__ == "__main__":
    main()
