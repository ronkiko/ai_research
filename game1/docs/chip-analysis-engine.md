# Движок `chip` — лабораторный разбор весов как булевой схемы / чипа

> **Важное уточнение.** Этот движок **не** утверждает, что обученная нейросеть
> физически является CMOS-чипом. Поведение сети дискретизируется на булевых
> входах `0/1`, после чего из него извлекается логическая схема и оценивается её
> **эквивалентная CMOS-стоимость** в стандартной вентильной библиотеке.

## 1. Назначение

- Это **первый движок лаборатории** (`engine #1`).
- Заменяет устаревший `default_report`.
- Строит полный «чиповый» разбор снапшота весов:

```text
weights snapshot
→ parse weights
→ hidden neurons as threshold gates
→ output combiner по hidden outputs
→ extracted Boolean circuit
→ extracted network CMOS cost
→ optimized final-function CMOS reference
→ human-readable chip report
```

Главное правило: **не смешивать** стоимость фактически извлечённой сети
и стоимость оптимизированной итоговой функции.

## 2. Вход

- Markdown-снапшот из `game1/research/weights/**/*.md`.
- `model_key` — ключ модели, сохранённый в мета-поле `Модель:`.
- Распарсенные веса: `W1`, `b1`, `W2`, `b2`.
- В текущей версии поддерживаются архитектуры `2 → N → 1`, где скрытый слой
  может использовать одно из трёх семейств активаций:
  - `ReLU` — модель `mlp`;
  - `Tanh` — модель `torch`;
  - sigmoid-like threshold — любая модель, бинаризуемая через `z >= 0`.

## 3. Ограничения

- Это **не физический синтез нейросети в кремний**.
- Это **дискретизация поведения** сети на булевых входах.
- Малый `margin` (`min |z|` на четырёх входах) помечается как нестабильный
  вентиль.
- `ReLU`, `Tanh` и sigmoid бинаризуются через **pre-activation / logit threshold**,
  а не через произвольное правило `activation(z) > 0.5`.
- Модели без скрытого слоя (`bias`, `logistic`, `duplet`, `context`) в этой
  версии не разбираются как схемы; вместо `None` выдаётся понятное сообщение.

## 4. Алгоритм

1. Распарсить веса из Markdown inline-формата (`0.weight[0] = 1.23`) или
   табличного формата.
2. Восстановить `W1`, `b1`, `W2`, `b2` для сети `2 → N → 1`.
3. Для каждого скрытого нейрона `k` посчитать `z` на четырёх входах
   `00, 01, 10, 11`:
   ```text
   z(x₀, x₁) = W1[0][k] * x₀ + W1[1][k] * x₁ + b1[k]
   ```
4. Получить маску из 4 бит по policy бинаризации (см. раздел 5).
5. Сопоставить маску с ролью:
   `ZERO`, `ONE`, `AND`, `OR`, `NOR`, `NAND`, `XOR`, `XNOR`, `PASS`, `NOT`,
   implication, `custom/DNF`.
6. Посчитать `margin = min |z|`. Если `margin` меньше порога — статус
   `unstable`.
7. Определить статус каждого скрытого нейрона:
   - `active` — используется в схеме;
   - `ignored_by_output` — вес на выходе близок к нулю;
   - `constant` — маска `0000` или `1111`;
   - `duplicate` — такая же маска уже встречалась раньше;
   - `unstable` — малый margin.
8. Посчитать выходную truth table по **бинаризованным** скрытым битам:
   ```text
   net(x₀, x₁)  = Σ W2[k] * hidden_bit[k] + b2
   output_bit   = 1 if net >= 0 else 0
   ```
   Маска строится для тех же четырёх входов `00, 01, 10, 11`.
9. Проанализировать **output combiner**: по active hidden gates, их маскам и
   весам на выход определить, какая двухвходовая операция (OR/AND/NAND/NOR)
   или threshold-функция реализована над hidden outputs.
10. Построить `extracted network expression`:
    - для стандартного XNOR-паттерна `NOR + AND → OR` —
      `OR(NOR(x₀,x₁), AND(x₀,x₁))`;
    - для известных ролей — каноническая формула;
    - иначе — threshold/DNF по hidden bits.
11. Построить `final Boolean function` напрямую от `x₀, x₁`.
12. Проверить совпадение truth table извлечённой сети с итоговой функцией.
13. Построить текстовую схему (active / ignored / constant / duplicate / unstable
    gates + output combiner).
14. Посчитать **extracted network CMOS cost** (hidden gates + output combiner).
15. Показать **optimized final-function CMOS reference** отдельно, не прибавляя
    его к extracted cost.
16. Сформировать отчёт и вердикт.

## 5. Бинаризация

| Семейство активации | Policy |
|---|---|
| sigmoid-like, tanh | `bit = 1 if z >= 0 else 0` |
| ReLU | `bit = 1 if z > EPS else 0` (`EPS = 1e-6`) |

`EPS` — константа, не параметр. В отчёте явно пишутся `activation family` и
`threshold policy`.

## 6. CMOS-библиотека

| Вентиль | Стоимость |
|---|---|
| `INV` | 2T |
| `NAND2` | 4T |
| `NOR2` | 4T |
| `AND2` | NAND2 + INV = 6T |
| `OR2` | NOR2 + INV = 6T |

### 6.1 Extracted network CMOS cost

Стоимость фактически извлечённой сети:

```text
extracted cost = Σ active hidden gates + output combiner
```

Пример стандартного XNOR-паттерна:

```text
h0 = NOR(x₀, x₁)
h1 = AND(x₀, x₁)
output = OR(h0, h1)

cost = NOR2 + AND2 + OR2 = 4 + 6 + 6 = 16T
depth = 2
```

Выходной комбайнер анализируется по hidden outputs и весам выхода. Это **не**
всегда OR; для mixed-sign весов или более чем двух active hidden gates
используется threshold/DNF fallback с честным note об ограничении.

### 6.2 Optimized final-function reference

Справочная оценка итоговой булевой функции, реализованной напрямую по `x₀,x₁`.
**Не прибавляется** к extracted cost.

Для XNOR:

```text
Classic XNOR reference:
NOR2 + AND2 + OR2 = 16T

Alternative NAND-only:
4 × NAND2 = 16T

Optimized macro / pass-transistor:
may be cheaper, not counted as extracted network
```

## 7. Output combiner

Output combiner — функция выходного нейрона над hidden outputs.

- `active_hidden` — hidden gates со статусом `active`, `unstable` или `duplicate`,
  с ненулевым весом на выход и не являющиеся constant.
- `positive_hidden` / `negative_hidden` — по знаку веса.
- `ignored_hidden` — не влияют на выход.

Логика:

- 1 active hidden → `WIRE`, `INV` или threshold-обёртка.
- 2 active hidden, оба positive → семантический поиск `OR/AND/NAND/NOR`;
  `XOR/XNOR` показываются как DNF fallback.
- mixed-sign веса или более 2 active hidden → threshold / multi-input
  combiner с честным note, что точная CMOS-стоимость не вычислена.

## 8. Неподдерживаемые и direct-модели

Если `model_key` не `mlp` / `torch`, или в снапшоте нет весов скрытого слоя,
`chip` возвращает понятный markdown-отчёт вместо `None`:

```text
# CHIP ANALYSIS

chip engine supports 2 → N → 1 snapshots in this version.

Current model: bias/logistic/duplet/context
Reason: no hidden layer weights were found.

Use forensic / prune, or train/save an mlp / torch snapshot.
```

## 9. Формат отчёта

Отчёт состоит из пяти шагов:

1. **Hidden gates** — таблица нейронов, их роли, z-значения, margin, статус.
2. **Output combiner truth table** — таблица для `00,01,10,11`: hidden bits,
   `net`, `output_bit`, итоговая роль.
3. **Full chip scheme** — текстовая схема: входы → hidden gates → output,
   плюс секции ignored / constant / duplicate / unstable gates.
4. **CMOS estimate** — отдельно:
   - extracted network expression;
   - final Boolean function;
   - verification note;
   - extracted network CMOS cost;
   - optimized final-function reference.
5. **Verdict** — краткий вывод: схема совпала с target или нет, предупреждения.

## 10. Критерии готовности

- Старый `default_report` больше не является движком №1.
- Новый движок №1 называется `chip`.
- UI показывает его как `1 chip`.
- `forensic` и `prune` остаются доступными как отдельные подключаемые движки.
- `py_compile` проходит без ошибок.
- Добавлены unit-style проверки нового движка.
- CMOS-оценка разделена на `extracted network cost` и `optimized reference`.
- Output combiner анализируется по hidden outputs, а не только по `output_mask`.
