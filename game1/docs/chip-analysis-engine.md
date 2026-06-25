# Движок `chip` — лабораторный разбор весов как булевой схемы / чипа

> **Важное уточнение.** Этот движок **не** утверждает, что обученная нейросеть
> физически является CMOS-чипом. Поведение сети дискретизируется на булевых
> входах `0/1`, после чего из него строится логическая схема и оценивается её
> **эквивалентная CMOS-стоимость** в стандартной вентильной библиотеке.

## 1. Назначение

- Это **первый движок лаборатории** (`engine #1`).
- Заменяет устаревший `default_report`.
- Строит «чиповый» разбор снапшота весов:

```text
weights snapshot
→ parse weights
→ real network forward pass на 00/01/10/11
→ real network behavior (truth table)
→ functional boolean chip
→ functional CMOS cost
→ human-readable chip report
→ raw neuron diagnostic (ниже первого экрана)
```

Главное правило: итоговая булева функция и её CMOS-стоимость строятся по
**real network behavior** — честному forward pass сети, а не по hidden-bit
approximation.

## 2. Терминология

- **real network behavior** — truth table, полученная реальным forward passом
  сети `2 → N → 1` на входах `00, 01, 10, 11` с настоящими активациями
  скрытого слоя.
- **functional boolean chip** — булева схема, эквивалентная `real network
  behavior`. Это то, что рисуется в ASCII и чему считается CMOS-стоимость.
- **functional CMOS cost** — стоимость `functional boolean chip` в стандартной
  вентильной библиотеке. Единственная CMOS-цена в отчёте.
- **raw neuron diagnostic** — компактная диагностика скрытых нейронов как
  threshold-gate (роль, статус, margin). Расположена ниже первого экрана и
  **не влияет** на `Network`, `Result`, `CMOS COST`.

> Старые термины `extracted network CMOS cost` и `optimized final-function
> reference` больше не используются. Единственная стоимость —
> `functional CMOS cost`.

## 3. Вход

- Markdown-снапшот из `game1/research/weights/**/*.md`.
- `model_key` — ключ модели, сохранённый в мета-поле `Модель:`.
- Распарсенные веса: `W1`, `b1`, `W2`, `b2`.
- Поддерживаются архитектуры `2 → N → 1`, где скрытый слой использует одно из
  семейств активаций:
  - `ReLU` — модель `mlp`;
  - `Tanh` — модель `torch`;
  - sigmoid-like threshold — любая модель, бинаризуемая через `z >= 0`.

## 4. Ограничения

- Это **не физический синтез нейросети в кремний**.
- Это **дискретизация поведения** сети на булевых входах.
- Малый `margin` (`min |z|` на четырёх входах скрытого нейрона) помечается как
  `unstable` в raw neuron diagnostic.
- `ReLU`, `Tanh` и sigmoid бинаризуются через **pre-activation / logit
  threshold**, а не через произвольное правило `activation(z) > 0.5`.
- Модели без скрытого слоя (`bias`, `logistic`, `duplet`, `context`) в этой
  версии не разбираются как схемы; вместо `None` выдаётся понятное сообщение.
- **Hidden-bit approximation не влияет** на `Network`, `Result`, `CMOS COST`:
  эти поля строятся только по `real network behavior`. Approximation остаётся
  лишь справочной диагностикой.

## 5. Алгоритм

1. Распарсить веса из Markdown inline-формата (`0.weight[0] = 1.23`) или
   табличного формата.
2. Восстановить `W1`, `b1`, `W2`, `b2` для сети `2 → N → 1`.
3. Для каждого из четырёх входов `00, 01, 10, 11` выполнить **реальный forward
   pass**:
   ```text
   z_k    = W1[0][k] * x₀ + W1[1][k] * x₁ + b1[k]
   h_k    = activation(z_k)        # ReLU/Tanh по model_key
   logit  = Σ W2[k] * h_k + b2
   output = 1 if logit >= 0 else 0
   ```
4. Собрать `output_mask` из четырёх `output`-битов → `real network behavior`.
5. Сопоставить `output_mask` с ролью:
   `ZERO`, `ONE`, `AND`, `OR`, `NOR`, `NAND`, `XOR`, `XNOR`, `PASS`, `NOT`,
   implication, `CUSTOM DNF`.
6. Сравнить `output_mask` с canonical target игры → `Result` (`MATCH` / `FAIL`
   / `NETWORK ONLY`).
7. Построить `functional boolean chip` (ASCII-схема) по роли `output_mask`.
8. Посчитать **functional CMOS cost** по роли; для `CUSTOM` — DNF-оценка.
9. Сформировать proof table: `x₀ x₁ | target | network | ok`.
10. Дополнительно собрать **raw neuron diagnostic** по hidden-нейронам как
    threshold-gate (роль, статус, margin). Эта секция расположена ниже первого
    экрана и не участвует в стоимости.

## 6. Бинаризация

| Семейство активации | Policy |
|---|---|
| sigmoid-like, tanh | `bit = 1 if z >= 0 else 0` |
| ReLU | `bit = 1 if z > EPS else 0` (`EPS = 1e-6`) |

`EPS` — константа, не параметр. В raw neuron diagnostic явно пишутся `role` и
`margin`.

## 7. CMOS-библиотека

| Вентиль | Стоимость |
|---|---|
| `INV` | 2T |
| `NAND2` | 4T |
| `NOR2` | 4T |
| `AND2` | NAND2 + INV = 6T |
| `OR2` | NOR2 + INV = 6T |

### 7.1 Functional CMOS cost

Единственная стоимость в отчёте — стоимость `functional boolean chip`:

```text
functional cost = cost(role(output_mask))
```

Для известной роли берётся каноническая реализация. Пример XNOR:

```text
h0 = NOR(x₀, x₁)
h1 = AND(x₀, x₁)
output = OR(h0, h1)

functional cost = NOR2 + AND2 + OR2 = 4 + 6 + 6 = 16T
```

Для `CUSTOM` — DNF-оценка по реальной truth table (`AND2` на терм, `INV` на
отрицательные литералы, `OR2` на сборку). Точная стоимость смешанных
комбинаторов не вычисляется — тогда схема показывает `CUSTOM DNF` и отсылает к
proof table.

## 8. Target truth tables

Canonical target пока берётся из `game1/ui/lab_engines/targets.py` —
минимального локального словаря (`target_for_game`). Позже источник target
должен переехать к mechanics metadata игр, а `targets.py` станет тонким
читателем.

Текущая запись:

```text
lie_detector → XNOR, mask (1, 0, 0, 1) — two witnesses agree
```

## 9. Неподдерживаемые и direct-модели

Если `model_key` не `mlp` / `torch`, или в снапшоте нет весов скрытого слоя,
`chip` возвращает понятный markdown-отчёт вместо `None`:

```text
# CHIP

chip engine supports 2 → N → 1 snapshots in this version.

Current model: bias/logistic/duplet/context
Reason: no hidden layer weights were found.

Use forensic / prune, or train/save an mlp / torch snapshot.
```

## 10. Формат отчёта

Первый экран — короткая булева схема, а не лабораторный дамп:

```text
# CHIP

Game: lie_detector
Target: XNOR
Network: XNOR
Result: MATCH

BOOLEAN CHIP SCHEME

<ASCII-схема>

CMOS COST: 16T

PROOF

x₀ x₁ | target | network | ok
0  0  |   1    |    1    | ✓
0  1  |   0    |    0    | ✓
1  0  |   0    |    0    | ✓
1  1  |   1    |    1    | ✓

DEBUG: press 1/2/3 to switch engines; raw neuron diagnostic below
```

Ниже разделителя `--- RAW NEURON DIAGNOSTIC ---` идёт компактная диагностика
скрытых нейронов (`h0: role, status, margin`). Эта секция не влияет на
основной результат.

## 11. Критерии готовности

- Старый `default_report` больше не является движком №1.
- Новый движок №1 называется `chip`.
- UI показывает его как `1 chip`.
- `forensic` и `prune` остаются доступными как отдельные подключаемые движки.
- `py_compile` проходит без ошибок.
- Добавлены unit-style проверки нового движка.
- Итоговая функция и CMOS-стоимость строятся по `real network behavior`.
- Hidden-bit approximation не влияет на `Network`, `Result`, `CMOS COST`.
- Canonical target берётся из `ui/lab_engines/targets.py`.