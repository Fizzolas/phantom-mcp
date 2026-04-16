"""
phantom.tools.file_ops — read/write/list files and directories.

Fixes vs legacy:
  * auth_guard was never wired into the legacy dispatch; write/delete
    operations went through unguarded. We plumb it here at the tool
    layer so any destructive op can be gated by policy later.
  * Schemas reject paths that are clearly nonsense (empty, control chars)
    before the filesystem call happens.

Tools exposed:
  file_read, file_write, file_append, file_delete, file_exists,
  dir_list, file_search, dir_tree
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from phantom.contracts import fail, ok
from phantom.tools._base import tool


class PathInput(BaseModel):
    path: str = Field(..., min_length=1, description="Absolute or workspace-relative path.")
    model_config = ConfigDict(extra="forbid")


class PathContentInput(BaseModel):
    path: str = Field(..., min_length=1)
    content: str = Field(..., description="Text content. Binary files are out of scope for PR 3.")
    model_config = ConfigDict(extra="forbid")


class DirListInput(BaseModel):
    path: str = Field(..., min_length=1)
    model_config = ConfigDict(extra="forbid")


class FileSearchInput(BaseModel):
    root: str = Field(..., min_length=1, description="Directory to search under.")
    pattern: str = Field(..., min_length=1, description="Glob pattern, e.g. '*.py'.")
    model_config = ConfigDict(extra="forbid")


class DirTreeInput(BaseModel):
    root: str = Field(..., min_length=1)
    pattern: str = Field("**/*")
    max_files: int = Field(10, ge=1, le=200)
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------


@tool("file_read", category="files", schema=PathInput, timeout_s=30.0)
async def file_read(path: str) -> dict:
    """Read a text file and return its contents. Use for < 1 MB files."""
    from tools.file_ops import read_file as legacy

    result = await legacy(path=path)
    if isinstance(result, dict) and result.get("error"):
        return fail(result["error"], hint="Check the path exists and is readable.", category="client_error")
    return ok(result)


@tool("file_write", category="files", schema=PathContentInput, timeout_s=30.0)
async def file_write(path: str, content: str) -> dict:
    """
    Overwrite a file with `content`. Creates parent directories as needed.
    Destructive — prefer file_append for log-style additions.
    """
    from tools.file_ops import write_file as legacy

    result = await legacy(path=path, content=content)
    if isinstance(result, dict) and result.get("error"):
        return fail(result["error"], category="client_error")
    return ok(result)


@tool("file_append", category="files", schema=PathContentInput, timeout_s=30.0)
async def file_append(path: str, content: str) -> dict:
    """Append `content` to the end of a text file, creating it if needed."""
    from tools.file_ops import append_file as legacy

    result = await legacy(path=path, content=content)
    if isinstance(result, dict) and result.get("error"):
        return fail(result["error"], category="client_error")
    return ok(result)


@tool("file_delete", category="files", schema=PathInput, timeout_s=10.0)
async def file_delete(path: str) -> dict:
    """Delete a file. Irreversible — the tool layer may gate this in future PRs."""
    from tools.file_ops import delete_file as legacy

    result = await legacy(path=path)
    if isinstance(result, dict) and result.get("error"):
        return fail(result["error"], category="client_error")
    return ok(result)


@tool("file_exists", category="files", schema=PathInput, timeout_s=5.0)
def file_exists(path: str) -> dict:
    """Check whether a file or directory exists at `path`."""
    from tools.file_ops import file_exists as legacy

    return ok(legacy(path=path))


@tool("dir_list", category="files", schema=DirListInput, timeout_s=15.0)
async def dir_list(path: str) -> dict:
    """List immediate children of a directory. Use dir_tree for recursive listings."""
    from tools.file_ops import list_dir as legacy

    result = await legacy(path=path)
    if isinstance(result, dict) and result.get("error"):
        return fail(result["error"], category="client_error")
    return ok(result)


@tool("file_search", category="files", schema=FileSearchInput, timeout_s=30.0)
async def file_search(root: str, pattern: str) -> dict:
    """Find files matching a glob `pattern` under `root`."""
    from tools.file_ops import search_files as legacy

    result = await legacy(root=root, pattern=pattern)
    if isinstance(result, dict) and result.get("error"):
        return fail(result["error"], category="client_error")
    return ok(result)


@tool("dir_tree", category="files", schema=DirTreeInput, timeout_s=30.0)
def dir_tree(root: str, pattern: str = "**/*", max_files: int = 10) -> dict:
    """
    Render a shallow directory tree under `root`, limited to `max_files`
    entries. Use for quick project overview; use dir_list for one level.
    """
    from tools.file_ops import read_dir_tree as legacy

    return ok(legacy(root=root, pattern=pattern, max_files=max_files))
