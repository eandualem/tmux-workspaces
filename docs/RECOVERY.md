# Preserve and recover an arrangement

An invalid library stops launch before any display/demo server or ordinary shell
starts. The error identifies `layouts.db` and the invalid field or database issue.
No automatic repair, replacement layout or fallback to an older table is attempted.
An existing viewer also refuses to refresh/save over an invalid current record.
Its external sessions and ordinary shell processes remain separately owned by tmux.

## Keep the original

Do not delete `layouts.db` to dismiss the error. Keep its directory, including any
`layouts.db-wal` and `layouts.db-shm` files. Those files can contain committed data
that has not reached the main database yet.

Exit viewers using this library before making a filesystem backup, so another
window cannot write during the copy. Exiting a viewer preserves its ordinary
shells and external sessions. Copy the entire directory to a new location:

```sh
library='/absolute/path/to/your/library'
backup='/absolute/path/to/a/new/backup-directory'
test ! -e "$backup" && cp -R "$library" "$backup"
```

Use a new destination; keep the original untouched. If you cannot close every
writer, use SQLite's backup facilities with help from someone familiar with SQLite
instead of relying on a live filesystem copy. If SQLite cannot read the database,
preserve the original files for recovery rather than repeatedly modifying them.

## Continue separately

You can work with a different library while investigating:

```sh
./run --data-dir /absolute/path/to/a/new/library
```

This creates an independent arrangement and shell server. It does not import,
migrate, stop or delete the original library's processes or external sessions.

## Recover into a copy

A known-good backup can be restored into another directory and opened there with
`--data-dir`. Check its grouping, names, splits and attachments before choosing it
as your normal library. Restarting from a different library directory creates its
own ordinary shells; it does not move the old shell processes into the copy.

Earlier `layout` and `shared_layout` tables are preserved during valid migrations,
including their exact stored JSON text. They can provide recovery material for an
expert inspecting a **copy** with SQLite tools. A bad current `terminal_layout`
record never silently falls back to those tables, since their content may be old.
Choose an intact arrangement explicitly, repair only the copy, and validate by
launching against that copy. This version has no automatic repair/export command.

Unsupported future versions should be opened with the version of the application
that wrote them. Do not change the version number to bypass validation.

## Validation scope

Validation checks required workspace/tab/leaf fields, globally unique 12-character
hexadecimal identities, names, selected identities, exact session references and
absolute cwd/socket paths. A missing cwd or source socket remains supported for
older migrated leaves. Paths may contain spaces or newlines; NUL and paths that
cannot be represented by the filesystem are rejected. A socket reference requires
an attachment and does not prove that its session is currently online.

Splits require both children and `right`/`below` direction. An omitted ratio retains
the existing equal-split default; an explicit ratio must be finite and between
0.01 and 0.99. Tree depth is limited to 64, overall data depth to 128, and the library
to 10000 containers/identified objects, 100000 data values, and 16 MiB of JSON text.
The JSON size limit also applies before saving or migrating a record, so a write
cannot create an oversized record that the next read refuses. These limits keep
recursive model/render operations bounded. Duplicate JSON keys, non-finite numbers,
invalid JSON and malformed SQLite schemas are rejected without rewriting records.
Unknown extension fields are preserved when their data remains within those limits.
