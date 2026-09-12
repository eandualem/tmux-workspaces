"""JSON editing drafts over the existing validated configuration files."""

from __future__ import annotations

import json
import tomllib

from .keymap import DEFAULT_KEYMAP, Keymap, KeymapFile
from .theme import DEFAULT_THEME, Theme, ThemeFile


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError(f"{value} is not a JSON value")


class SettingsDraft:
    """Keep disk conflict detection independent of editable text and validation."""

    def __init__(self, kind, path):
        self.kind = kind
        self.file = KeymapFile(path) if kind == "shortcuts" else ThemeFile(path)
        self.schema = Keymap if kind == "shortcuts" else Theme
        default = DEFAULT_KEYMAP if kind == "shortcuts" else DEFAULT_THEME
        payload, diagnostic = self.file.read_bytes()
        self.message = diagnostic or ""
        self.confirmed = None
        self.saved = False
        self.original = payload
        self.repair = False
        data = tomllib.loads(default.to_toml())
        if kind == "colors" and payload is None:
            data = {"panel": default.panel, "surface": default.surface} | {
                role: {
                    "foreground": list(value.foreground),
                    **(
                        {}
                        if role in default._panel_roles | default._surface_roles
                        else {"background": list(value.background)}
                    ),
                    "attributes": list(value.attributes),
                }
                for role, value in default.roles.items()
            }
        if payload is not None:
            try:
                data = tomllib.loads(payload.decode("utf-8"))
                self.schema.from_dict(data)
            except (ValueError, UnicodeError, RecursionError) as error:
                self.message = (
                    f"Existing file is invalid: {error}. Defaults shown; Cancel keeps it."
                )
                self.repair = True
                data = tomllib.loads(default.to_toml())
        self.initial = json.dumps(data, ensure_ascii=True, indent=2) + "\n"
        self.lossy = not self.file.rewrites_cleanly(self.schema.from_dict(data).to_toml().encode())

    def validate(self, text):
        if len(text.encode("utf-8")) > self.file.limit:
            raise ValueError(f"Configuration exceeds {self.file.limit} bytes")
        data = json.loads(text, object_pairs_hook=object_pairs, parse_constant=invalid_constant)
        if not isinstance(data, dict):
            raise ValueError("Configuration must be a JSON object enclosed in { }")
        return self.schema.from_dict(data)

    def save(self, text):
        """Validate before writing; a refused save leaves the text and file intact."""
        if text == self.initial and self.original is not None and not self.repair:
            self.message = "No changes to save."
            return False
        try:
            value = self.validate(text)
            payload = value.to_toml().encode("utf-8")
            if (self.lossy or self.repair) and self.confirmed != text:
                self.confirmed = text
                self.message = (
                    "Save again to replace the TOML file; comments/formatting are not kept."
                )
                return False
            self.file.write_bytes(payload)
        except json.JSONDecodeError as error:
            self.message = f"JSON line {error.lineno}, column {error.colno}: {error.msg}"
            return False
        except (ValueError, OSError, RecursionError, UnicodeError) as error:
            self.message = str(error) or "Configuration is too deeply nested"
            return False
        self.saved = True
        return True
