"""
phantom.tools.files — read, write, list, search, and walk files.

Wraps the legacy tools/file_ops.py functions in the ToolResult envelope.

Why we expose these even on a desktop-only build: the LM Studio model
often needs to look at logs, config files, or scripts before deciding
its next action. File access is the cheapest grounding tool we have.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from phantom.contracts import ok, fail
from phantom.tools._base import tool


def _wrap(result: dict, *, success_hint: str | None = None):
    """legacy file_ops returns dict with optional 'error' key."""
    if isinstance(result, dict) and "error" in result:
        return fail(result["error"], hint=result.get("hint"), category="client_error", **{
            k: v for k, v in result.items() if k not in ("error", "hint")
        })
    return ok(result, hint=success_hint)


# ----------------------------------------------------------------------
class ReadFileInput(BaseModel):
    path: str = Field(..., min_length=1)

    model_config = ConfigDict(extra="forbid")


@tool("file_read", category="files", schema=ReadFileInput, timeout_s=10.0)
async def file_read(path: str) -> dict:
    """
    Read a text file. Binary files are detected and rejected with a hint.
    Output is truncated for very large files; use shell_cmd for streamed
    processing of huge files.
    """
    from tools.file_ops import read_file as legacy
    return _wrap(await legacy(path))


class WriteFileInput(BaseModel):
    path: str = Field(..., min_length=1)
    content: str = Field("", description="Full file contents to write.")

    model_config = ConfigDict(extra="forbid")


@tool("file_write", category="files", schema=WriteFileInput, timeout_s=10.0)
async def file_write(path: str, content: str) -> dict:
    """
    Overwrite a file with `content`. Parent directories are created.
    """
    from tools.file_ops import write_file as legacy
    return _wrap(await legacy(path, content))


class AppendFileInput(BaseModel):
    path: str = Field(..., min_length=1)
    content: str

    model_config = ConfigDict(extra="forbid")


@tool("file_append", category="files", schema=AppendFileInput, timeout_s=10.0)
async def file_append(path: str, content: str) -> dict:
    """
    Append text to a file (creating it if needed).
    """
    from tools.file_ops import append_file as legacy
    return _wrap(await legacy(path, content))


class DeleteFileInput(BaseModel):
    path: str = Field(..., min_length=1)

    model_config = ConfigDict(extra="forbid")


@tool("file_delete", category="files", schema=DeleteFileInput, timeout_s=10.0)
async def file_delete(path: str) -> dict:
    """
    Delete a file or directory recursively. Use carefully.
    """
    from tools.file_ops import delete_file as legacy
    return _wrap(await legacy(path))


class ListDirInput(BaseModel):
    path: str = Field(..., min_length=1)

    model_config = ConfigDict(extra="forbid")


@tool("file_list_dir", category="files", schema=ListDirInput, timeout_s=10.0)
async def file_list_dir(path: str) -> dict:
    """
    List the immediate entries of a directory with sizes.
    """
    from tools.file_ops import list_dir as legacy
    return _wrap(await legacy(path))


class SearchFilesInput(BaseModel):
    root: str = Field(..., min_length=1)
    pattern: str = Field(..., min_length=1, description="Glob pattern, e.g. '**/*.py'.")

    model_config = ConfigDict(extra="forbid")


@tool("file_search", category="files", schema=SearchFilesInput, timeout_s=30.0)
async def file_search(root: str, pattern: str) -> dict:
    """
    Recursively search a directory tree by glob pattern.
    Returns matching paths. Loops via symlinks/junctions are detected and skipped.
    """
    from tools.file_ops import search_files as legacy
    return _wrap(await legacy(root, pattern))


class FileExistsInput(BaseModel):
    path: str = Field(..., min_length=1)

    model_config = ConfigDict(extra="forbid")


@tool("file_exists", category="files", schema=FileExistsInput, timeout_s=3.0)
def file_exists(path: str) -> dict:
    """
    Cheap probe: does the path exist, and is it a file or a directory?
    """
    from tools.file_ops import file_exists as legacy
    return _wrap(legacy(path))


class ReadDirTreeInput(BaseModel):
    root: str = Field(..., min_length=1)
    pattern: str = Field("**/*", description="Glob pattern.")
    max_files: int = Field(10, ge=1, le=100)

    model_config = ConfigDict(extra="forbid")


@tool("file_read_tree", category="files", schema=ReadDirTreeInput, timeout_s=60.0)
def file_read_tree(root: str, pattern: str = "**/*", max_files: int = 10) -> dict:
    """
    Read up to `max_files` text files matching `pattern` under `root`.
    Returns each file's content (truncated). Use to inspect a project at
    a glance without firing N separate file_read calls.
    """
    from tools.file_ops import read_dir_tree as legacy
    return _wrap(legacy(root, pattern, max_files))
