"""Std-lib tools layer for Vibrantine.

Tools are the deterministic peer to Commissions: typed input, typed
output, errors-as-state, no LLM. Per
`docs/design.md § Three categories, no fourth`, tools subclass
`Commission[InputT, OutputT]` with `max_input_tokens=None`
and no constructor model argument. The "is this a Commission or a
tool" distinction is enforced by authoring discipline, not by the type
system; both wear the same contract.
"""

from vibrantine.tools.delete import DeleteInput, DeleteOutput, DeleteTool
from vibrantine.tools.edit import EditInput, EditOutput, EditTool
from vibrantine.tools.fetch import FetchInput, FetchOutput, FetchTool
from vibrantine.tools.glob import GlobInput, GlobOutput, GlobTool
from vibrantine.tools.grep import GrepInput, GrepMatch, GrepOutput, GrepTool
from vibrantine.tools.list_dir import (
    DirEntry,
    DirEntryKind,
    ListDirInput,
    ListDirOutput,
    ListDirTool,
)
from vibrantine.tools.move import MoveInput, MoveOutput, MoveTool
from vibrantine.tools.read import ReadInput, ReadOutput, ReadTool
from vibrantine.tools.sample import SampleInput, SampleOutput, SampleTool
from vibrantine.tools.shell import ShellInput, ShellOutput, ShellTool
from vibrantine.tools.write import WriteInput, WriteOutput, WriteTool

__all__ = [
    "DeleteInput",
    "DeleteOutput",
    "DeleteTool",
    "DirEntry",
    "DirEntryKind",
    "EditInput",
    "EditOutput",
    "EditTool",
    "FetchInput",
    "FetchOutput",
    "FetchTool",
    "GlobInput",
    "GlobOutput",
    "GlobTool",
    "GrepInput",
    "GrepMatch",
    "GrepOutput",
    "GrepTool",
    "ListDirInput",
    "ListDirOutput",
    "ListDirTool",
    "MoveInput",
    "MoveOutput",
    "MoveTool",
    "ReadInput",
    "ReadOutput",
    "ReadTool",
    "SampleInput",
    "SampleOutput",
    "SampleTool",
    "ShellInput",
    "ShellOutput",
    "ShellTool",
    "WriteInput",
    "WriteOutput",
    "WriteTool",
]
