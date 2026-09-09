"""Conflict-checked atomic writes for the small TOML files the viewer owns.

Themes and keymaps are edited from inside a running viewer, so saving one has
to satisfy the same rules: never replace a file nobody read, never follow a
symlink or write a device, notice an edit made elsewhere since the file was
read, and leave the previous usable file in place when anything fails. Getting
that wrong costs a user their own configuration, so the logic lives here once
rather than being written a second time per file type.

The caller supplies the vocabulary. Every message names what the user keeps --
their colors, their shortcuts -- because a refusal is only actionable if it says
what survived it.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

LOCK_TIMEOUT_SECONDS = 2.0
LOCK_POLL_SECONDS = 0.02


class ConfigError(ValueError):
    """Actionable failure that never asks the caller to discard its working value."""


class ConfigConflict(ConfigError):
    """The file changed since it was read; the caller keeps its edits."""


@dataclass(frozen=True)
class Vocabulary:
    """The words one file type puts into its own refusals.

    ``noun`` is lower case for mid-sentence use and ``title`` capitalised for the
    start of one. ``unchanged`` states what the user still has, ``reopen`` names
    the action that shows them the file they did not expect, and ``option`` is
    the command-line flag that chooses a different path.
    """

    noun: str
    title: str
    unchanged: str
    reopen: str
    option: str
    temp_prefix: str


def read_file(path: Path, limit: int) -> bytes:
    """Read a regular file, refusing anything that could block viewer startup.

    Opening a FIFO for reading blocks until a writer appears, so a config path
    pointing at one would hang the viewer before it drew a frame. O_NONBLOCK
    makes the open return immediately and fstat then rejects anything that is
    not a regular file. Symlinks are followed deliberately: people symlink their
    dotfiles, and a link to a regular file is a regular file here.

    One byte past the limit is returned so the caller can tell "at the limit"
    from "over it" without a second read.
    """
    handle = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    try:
        if not stat.S_ISREG(os.fstat(handle).st_mode):
            raise OSError(errno.EINVAL, "not a regular file")
        chunks, total = [], 0
        while total <= limit:
            chunk = os.read(handle, 4096)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks)[: limit + 1]
    finally:
        os.close(handle)


class ConfigFile:
    """Reads and conflict-checked atomic writes for one configuration file.

    Subclasses add parsing and serialisation; everything that decides whether
    replacing the file is safe belongs here. ``error`` and ``conflict`` are the
    exception types raised, so a caller can keep catching its own.

    The concurrent-edit guarantee has a stated limit. Writers that take the
    sibling lock are fully serialised against each other. An external editor
    takes no lock, so it is caught by comparing the bytes on disk with the ones
    read -- a comparison made twice, the second time immediately before the
    replacement, which leaves a window of microseconds in which such an editor
    could write and be overwritten. POSIX offers no rename conditional on the
    target's contents, so that window cannot be closed here; it is narrowed as
    far as it goes and named rather than implied away.
    """

    def __init__(
        self,
        path,
        *,
        limit: int,
        words: Vocabulary,
        error: type[ConfigError] = ConfigError,
        conflict: type[ConfigConflict] = ConfigConflict,
    ):
        self.path = Path(path)
        self.limit = limit
        self.words = words
        self.error = error
        self.conflict = conflict
        self._digest: str | None = None
        self._seen = False
        # The bytes behind that digest. Anything wanting to inspect the file as
        # it was read must use these rather than reading again: a second read
        # would move the digest onto newer bytes and blind the conflict check.
        self._payload: bytes | None = None
        # A file that exists but could not be read is neither "never read" nor
        # "read these bytes": replacing it would destroy contents nobody saw.
        self._unreadable = False

    @staticmethod
    def _hash(payload: bytes) -> str:
        # The length is part of the digest: reads stop one byte past the cap, so
        # without it two different oversized files could share a prefix hash.
        return hashlib.sha256(f"{len(payload)}:".encode() + payload).hexdigest()

    def read_bytes(self) -> tuple[bytes | None, str | None]:
        """The file's bytes, or None when it is absent or unreadable.

        The digest bookkeeping happens here so that every caller records what it
        saw. A diagnostic accompanies an unreadable file; a missing one is not a
        problem, because saving creates it.
        """
        try:
            payload = read_file(self.path, self.limit)
        except FileNotFoundError:
            self._digest, self._seen, self._unreadable = None, True, False
            self._payload = None
            return None, None
        except OSError as error:
            self._digest, self._seen, self._unreadable = None, False, True
            self._payload = None
            return None, f"{error.strerror}: {self.path}"
        self._digest, self._seen, self._unreadable = self._hash(payload), True, False
        self._payload = payload
        return payload, None

    def rewrites_cleanly(self, payload: bytes) -> bool:
        """Whether writing `payload` would preserve everything the file holds.

        Generated output carries no comments and one fixed ordering, so a
        hand-written file is not round-tripped. This compares against the bytes
        already read rather than reading again, because a second read would
        advance the conflict digest onto a concurrent writer's changes while the
        caller still held the older content -- and the save would then replace
        that writer's file instead of refusing.

        A file nobody read cannot be judged, so it is reported as lossy: that
        errs towards warning the user rather than towards a silent rewrite.
        """
        if not self._seen:
            return False
        return self._payload is None or self._payload == payload

    def oversized(self, payload: bytes) -> bool:
        """Whether these bytes exceed what this file type accepts."""
        return len(payload) > self.limit

    def writable(self) -> bool:
        """Whether a Save can be offered; a race still surfaces as the error type."""
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            parent = self.path.parent
            while not parent.exists() and parent != parent.parent:
                parent = parent.parent
            return os.access(parent, os.W_OK | os.X_OK)
        except OSError:
            return False
        if not stat.S_ISREG(info.st_mode):
            return False
        return os.access(self.path, os.W_OK)

    def _check_target(self) -> None:
        words = self.words
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return
        except OSError as error:
            raise self.error(f"{words.title} {self.path}: {error.strerror}") from error
        if stat.S_ISLNK(info.st_mode):
            raise self.error(
                "Saving would replace a symbolic link, so nothing was written. Save to "
                f"a regular file instead of {self.path}."
            )
        if not stat.S_ISREG(info.st_mode):
            raise self.error(f"Not a regular file, so it was not replaced: {self.path}")
        if not os.access(self.path, os.W_OK):
            raise self.error(
                f"The {words.noun} file is read-only. {words.unchanged}; fix the "
                f"permissions of {self.path} or choose another path with {words.option}."
            )

    def _conflict(self) -> None:
        """Compare the bytes on disk with those last read. Call under the lock."""
        words = self.words
        if self._unreadable:
            # Permissions can change between the read and the save, so the rule
            # is stated here rather than left to whether the re-read happens to
            # fail again.
            raise self.error(
                f"The {words.noun} file could not be read, so it was not replaced. "
                f"{words.unchanged}; fix the permissions of {self.path}, then apply again."
            )
        try:
            payload = read_file(self.path, self.limit)
        except FileNotFoundError:
            current = None
        except OSError as error:
            raise self.error(f"{error.strerror}: {self.path}") from error
        else:
            if self.oversized(payload):
                raise self.error(
                    f"The {words.noun} file is larger than {self.limit} bytes, so a "
                    f"concurrent edit cannot be detected safely. {words.unchanged}; "
                    f"shrink or remove {self.path}, then apply again."
                )
            current = self._hash(payload)
        if not self._seen and current is not None:
            # The contract this class exists for: bytes nobody looked at are
            # never replaced. A first save to a path with no file is still fine.
            raise self.error(
                f"The {words.noun} file was not read before saving, so it was not "
                f"replaced. {words.unchanged}; {words.reopen}, then apply again. "
                f"({self.path})"
            )
        if self._seen and current != self._digest:
            raise self.conflict(
                f"The {words.noun} file changed on disk since it was read. "
                f"{words.unchanged}; {words.reopen}, then apply again. ({self.path})"
            )

    def write_bytes(self, payload: bytes) -> None:
        """Replace the file atomically, refusing unsafe targets and concurrent edits."""
        words = self.words
        if self.oversized(payload):
            raise self.error(f"{words.title} exceeds {self.limit} bytes; remove some values.")
        self._check_target()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as error:
            raise self.error(f"{error.strerror}: {self.path.parent}. {words.unchanged}.") from error
        lock = self._lock()
        try:
            # Everything that decides whether replacing is safe happens here,
            # while the lock is held, and the digest is checked once more with
            # the replacement already on disk.
            self._check_target()
            self._conflict()
            temporary = None
            try:
                with NamedTemporaryFile(
                    dir=self.path.parent, prefix=words.temp_prefix, suffix=".toml", delete=False
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                self._conflict()
                os.replace(temporary, self.path)
                temporary = None
            finally:
                if temporary is not None:
                    with contextlib.suppress(OSError):
                        temporary.unlink()
        except OSError as error:
            raise self.error(
                f"{error.strerror or error}: {self.path}. {words.unchanged}."
            ) from error
        finally:
            self._release(lock)
        self._digest, self._seen, self._unreadable = self._hash(payload), True, False
        self._payload = payload

    def _lock(self):
        """Serialize this application's writers on a stable sibling lock file.

        The lock cannot live on the file itself: write replaces that inode, so
        two writers would end up holding locks on different inodes and a first
        save to a missing file would take no lock at all. The sibling file is
        never replaced, so its inode is stable. flock is non-blocking and
        bounded, because a viewer that blocks here stops drawing. External
        editors do not take this lock, so the byte-level digest comparison, not
        the lock, is what actually detects a concurrent edit.
        """
        words = self.words
        try:
            import fcntl
        except ImportError:
            return None
        target = self.path.with_name("." + self.path.name + ".lock")
        try:
            handle = os.open(target, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        except OSError as error:
            raise self.error(
                f"{words.title} lock {target}: {error.strerror}. {words.unchanged}."
            ) from error
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return handle
            except OSError as error:
                if error.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                    os.close(handle)
                    raise self.error(
                        f"{words.title} lock {target}: {error.strerror}. {words.unchanged}."
                    ) from error
                if time.monotonic() >= deadline:
                    os.close(handle)
                    raise self.error(
                        f"The {words.noun} file is being saved by another window. "
                        f"{words.unchanged}; try again in a moment."
                    ) from error
                time.sleep(LOCK_POLL_SECONDS)

    @staticmethod
    def _release(handle) -> None:
        if handle is None:
            return
        with contextlib.suppress(OSError):
            os.close(handle)
