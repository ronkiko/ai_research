from __future__ import annotations

from config import Settings, default_settings, load_config, save_config

from .dto import ActionResultDto


class ConfigStore:
    def load_or_default(self, host, ai) -> tuple[Settings, list[ActionResultDto]]:
        settings, status, message = load_config(host, ai)
        results = [ActionResultDto(ok=status.value == "ok", status=status.value, message=message)]
        if settings is not None:
            return settings, results

        fallback = default_settings(host, ai)
        results.append(
            ActionResultDto(
                ok=True,
                status="ok",
                message="сгенерирован пресет по умолчанию",
            )
        )
        return fallback, results

    def save(self, settings: Settings) -> ActionResultDto:
        status, message = save_config(settings)
        return ActionResultDto(ok=status.value == "ok", status=status.value, message=message)
