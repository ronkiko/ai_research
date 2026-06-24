"""Конфиг настроек движка (выбранная игра, модель, режим обучения).

Движок при старте ищет файл конфига (CONFIG_PATH):
  • есть и валиден — загружает настройки;
  • есть, но сломан — отклоняет его (с причиной) и генерирует пресет по умолчанию;
  • нет — генерирует пресет по умолчанию (первые игра/модель/режим).
Генерация пресета — только когда конфига нет или он не прошёл проверку; дальше
загрузка идёт уже из конфига. Настройки, изменённые в интерфейсе, движок
сохраняет в конфиг при выходе.

Конфиг — движковая инфраструктура (не автономный модуль манифеста): движок
ищет файл и подключает его сам. Значения проверяются по каноническим хостам
(MechanicsHost / AiHost), поэтому конфиг не может выбрать несуществующую игру
или модель.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from modules.base import MechanicsHost, AiHost, Status

# Файл конфига лежит рядом с движком.
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "game1.conf.json")


@dataclass
class Settings:
    """Выбранные настройки: игра, модель, режим обучения."""
    game: str
    model: str
    train_mode: str


def default_settings(host: MechanicsHost | None, ai: AiHost | None) -> Settings:
    """Пресет по умолчанию: первые доступные игра, модель и режим.

    Вызывается один раз — когда конфига нет или он сломан. Дальше стартует
    уже из сохранённого конфига.
    """
    games = host.list_mechanics() if host is not None else []
    models = ai.list_models() if ai is not None else []
    modes = ai.list_train_modes() if ai is not None else []
    return Settings(
        game=games[0].key if games else "",
        model=models[0].key if models else "",
        train_mode=modes[0].key if modes else "",
    )


def _is_valid(s: Settings, host: MechanicsHost | None, ai: AiHost | None) -> bool:
    """Все три значения известны соответствующим хостам."""
    if host is None or ai is None:
        return False
    if not s.game or not any(m.key == s.game for m in host.list_mechanics()):
        return False
    if not s.model or not any(m.key == s.model for m in ai.list_models()):
        return False
    if not s.train_mode or not any(m.key == s.train_mode for m in ai.list_train_modes()):
        return False
    return True


def load_config(host: MechanicsHost | None,
                ai: AiHost | None) -> tuple[Settings | None, Status, str]:
    """Найти и проверить конфиг.

    Возвращает (settings, status, message):
      • settings, OK, «конфиг загружен» — файл валиден, настройки готовы;
      • None, FAIL, причина — файла нет либо он сломан/отклонён (звать
        default_settings и применять пресет).
    """
    if not os.path.exists(CONFIG_PATH):
        return None, Status.FAIL, "файл конфига не найден — генерирую пресет"
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        s = Settings(game=str(data["game"]), model=str(data["model"]),
                     train_mode=str(data["train_mode"]))
    except (OSError, ValueError, KeyError) as exc:
        return None, Status.FAIL, f"конфиг сломан и отклонён: {exc}"
    if not _is_valid(s, host, ai):
        return None, Status.FAIL, (
            "конфиг отклонён: неизвестные значения "
            f"({s.game}/{s.model}/{s.train_mode}) — генерирую пресет"
        )
    return s, Status.OK, "конфиг загружен"


def save_config(s: Settings) -> tuple[Status, str]:
    """Сохранить текущие настройки в конфиг (при выходе)."""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(asdict(s), f, indent=2, ensure_ascii=False)
    except OSError as exc:
        return Status.FAIL, f"не удалось сохранить конфиг: {exc}"
    return Status.OK, "настройки сохранены в конфиг"