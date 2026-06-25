from __future__ import annotations

from dataclasses import dataclass

from modules.base import AiHost, LoadResult, MechanicsHost, Module, Status
from modules.core import CoreModule
from modules.game_api import GameApiModule

MODULES: list[type[Module]] = [CoreModule, GameApiModule]


@dataclass(frozen=True)
class LoadedModules:
    modules: list[Module]
    mechanics: MechanicsHost | None
    ai: AiHost | None
    results: list[LoadResult]


def load_application_modules() -> LoadedModules:
    modules: list[Module] = []
    results: list[LoadResult] = []

    for cls in MODULES:
        module = cls()
        info = module.info()
        try:
            result = module.load()
        except Exception as exc:
            result = LoadResult(
                Status.FAIL,
                f"исключение при загрузке: {exc}",
                info.version,
            )
        modules.append(module)
        results.append(result)

    mechanics = next((m for m in modules if isinstance(m, MechanicsHost)), None)
    ai = next((m for m in modules if isinstance(m, AiHost)), None)
    return LoadedModules(modules=modules, mechanics=mechanics, ai=ai, results=results)
