import importlib
import pkgutil
import sys
import time

from .tools import ToolPlugin, ToolRegistry, create_default_registry


def build_tool_registry(
    *,
    plugin_package: str = "plugins",
    extra_plugins: list[ToolPlugin] | None = None,
) -> tuple[ToolRegistry, list[str]]:
    registry = create_default_registry()
    errors: list[str] = []

    for plugin in extra_plugins or []:
        try:
            registry.register(plugin)
        except Exception as exc:
            errors.append(f"register extra plugin {plugin.name} failed: {exc}")

    try:
        importlib.invalidate_caches()
        package = importlib.import_module(plugin_package)
    except ModuleNotFoundError:
        return registry, errors
    except Exception as exc:
        errors.append(f"load plugin package {plugin_package} failed: {exc}")
        return registry, errors

    package_paths = getattr(package, "__path__", None)
    if package_paths is None:
        errors.append(f"plugin package {plugin_package} is not a package")
        return registry, errors

    for module_info in pkgutil.iter_modules(package_paths):
        module_name = f"{plugin_package}.{module_info.name}"
        try:
            if module_name in sys.modules:
                module = importlib.reload(sys.modules[module_name])
            else:
                module = importlib.import_module(module_name)
        except Exception as exc:
            errors.append(f"load plugin {module_name} failed: {exc}")
            continue

        register = getattr(module, "register", None)
        if not callable(register):
            continue

        try:
            register(registry)
        except Exception as exc:
            errors.append(f"register plugin {module_name} failed: {exc}")

    return registry, errors


class ReloadableToolRegistry:
    def __init__(
        self,
        *,
        plugin_package: str = "plugins",
        extra_plugins: list[ToolPlugin] | None = None,
        reload_interval: float = 0.0,
    ):
        self.plugin_package = plugin_package
        self.extra_plugins = list(extra_plugins or [])
        self.reload_interval = reload_interval
        self._registry = create_default_registry()
        self._last_reload = 0.0
        self._pending_errors: list[str] = []
        self.reload(force=True)

    def reload(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_reload) < self.reload_interval:
            return
        registry, errors = build_tool_registry(
            plugin_package=self.plugin_package,
            extra_plugins=self.extra_plugins,
        )
        self._registry = registry
        self._pending_errors.extend(errors)
        self._last_reload = now

    def drain_errors(self) -> list[str]:
        errors = self._pending_errors[:]
        self._pending_errors.clear()
        return errors

    def tool_specs(self) -> list[dict]:
        self.reload()
        return self._registry.tool_specs()

    def call(self, name: str, arguments: str) -> str:
        self.reload()
        return self._registry.call(name, arguments)

    def plugin_names(self) -> list[str]:
        self.reload()
        return self._registry.plugin_names()


def create_reloadable_tool_registry(
    *,
    plugin_package: str = "plugins",
    extra_plugins: list[ToolPlugin] | None = None,
    reload_interval: float = 0.0,
) -> ReloadableToolRegistry:
    return ReloadableToolRegistry(
        plugin_package=plugin_package,
        extra_plugins=extra_plugins,
        reload_interval=reload_interval,
    )
