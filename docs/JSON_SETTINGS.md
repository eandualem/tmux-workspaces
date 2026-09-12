# Edit settings as JSON

Open **Configure… → Edit colors JSON…** or **Edit shortcuts JSON…**. The built-in
editor opens over the workspace, using most of the terminal. It needs no external
editor or extra Python package. Existing shells and attached sessions keep running.

Use the visible **Save**, **Cancel** and **Help** buttons with the mouse, or:

- **F2 / Ctrl-S** saves. Syntax and setting errors keep the editor open.
- **F10 / Escape / Ctrl-C** cancels. A changed draft asks for a second Cancel
  before discarding it; editing again clears that confirmation.
- **F1** toggles help; **F4** formats valid JSON.
- **Ctrl-A** selects all; **Ctrl-Z / Ctrl-Y** undo and redo.
- Arrow keys, **Home / End**, **PageUp / PageDown**, mouse clicks and the mouse
  wheel move through the text. Enter starts an indented line; Tab inserts spaces.
- Terminal paste inserts text, not viewer actions. A bracketed paste is one undo
  step. Unsupported terminal shortcut sequences are ignored by the editor.

If a paste is interrupted for two seconds, its incomplete text is discarded and
the draft stays unchanged. Delayed paste input remains isolated; paste again or
press Escape to cancel. Normal editing resumes when the paste end marker arrives.

Wait until the editor appears before typing. During opening, viewer actions are
suppressed to keep early input out of workspace shells. The editor retains its
draft when resized; below 40 columns or 12 rows it asks for more room and disables
Save until its controls are visible again.

## Saving and applying

The editor shows JSON, but saves through the existing TOML configuration system.
The destination is shown above the text: the file selected by the launch options
or environment, otherwise the default `keymap.toml` or `theme.toml`. Existing
files remain compatible with older versions; no JSON file lookup or automatic
migration is introduced. A missing file is created only when Save succeeds.
Starting with `--no-keymap` still disables shortcut-file editing.

Saving a hand-formatted file may remove its comments and rewrite its layout.
When that would happen, the first Save explains it; Save again confirms that
specific draft. Cancel preserves the original. Unreadable files, unsafe write
targets and concurrent changes are refused with a message. A draft with invalid
JSON, duplicate properties, conflicting keys or unsupported colors cannot replace
the working file. Limits are 64 KiB for shortcut text and 16 KiB for color text.

Saved colors apply to this viewer when the editor closes. Other viewers retain
their current colors until they reload them. Saving shortcuts opens the existing
refresh flow: you may refresh now or close it and keep using the current bindings.
Dedicated Ghostty profiles require reopening a fresh instance to change their
native keys; the refresh screen provides the command. See [refresh](REFRESH.md).

## Examples

Shortcut JSON uses the same actions and key spellings as [user keymaps](SHORTCUTS.md):

```json
{
  "prefix": "C-g",
  "bindings": {
    "new-tab": ["u"]
  },
  "direct": {
    "new-tab": ["super+alt+t"]
  }
}
```

An empty array disables that binding. Omitted actions use their defaults.
Moving a key from another action requires removing it from that action too;
the validator names conflicts instead of silently changing other bindings.

Color JSON can start with a preset and override individual colors:

```json
{
  "preset": "paper",
  "panel": "#f0f0f0",
  "accent": {
    "foreground": ["#005fb8", 25, "blue"]
  }
}
```

Explicit values override the preset. The default new-file view includes the
default color fields so they can be edited directly. See [color roles and
fallbacks](THEMES.md). The older per-row controls remain available under
**Colors…** and **Shortcuts → Edit shortcuts…**.

## Verification scope

`make check` covers bounded text editing, validation, repair, write refusal,
conflicts and discard confirmation. `python3 -m tests.integration.smoke_json_settings`
uses disposable PTYs to exercise mouse Save, undo/redo, resize, the actual popup,
live color application, cancellation without shell input, and shortcut refresh
with the original shell process preserved. These checks do not establish native
Ghostty mouse or clipboard behavior; that remains a real-terminal visual check.
