from typing import Optional

from PySide6.QtWidgets import QWidget


class BaseModule:
    name: str = "Unnamed Module"
    description: str = "No description provided."
    icon: str = "?"
    icon_name: Optional[str] = None

    key: str = ""
    version: str = "0.0.0"
    spec = None

    def on_load(self, api) -> None:
        pass

    def on_unload(self, api) -> None:
        pass

    def on_shutdown(self) -> None:
        pass

    def create_widget(self) -> QWidget:
        raise NotImplementedError("Module must implement create_widget()")

    def retranslate(self) -> None:
        pass