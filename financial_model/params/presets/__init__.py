"""预设模板包 — 开箱即用的抽蓄项目参数模板

用法::

    from financial_model.params.presets import list_presets, load_preset

    for name in list_presets():
        config = load_preset(name)
        results = config.to_orchestrator().run()
"""

from financial_model.params.presets.loader import (
    list_presets,
    load_preset,
    load_preset_metadata,
)

__all__ = ["list_presets", "load_preset", "load_preset_metadata"]
