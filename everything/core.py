import ctypes
import os
import threading
from ctypes.wintypes import BOOL, LPCWSTR, DWORD, LPWSTR, PULARGE_INTEGER
from fman import clear_status_message, show_status_message

from .settings import SETTINGS, logger


# Define constants for request flags
class Request:
    FileName = 0x00000001
    Path = 0x00000002
    FullPathAndFileName = 0x00000004
    Extension = 0x00000008
    Size = 0x00000010
    DateCreated = 0x00000020
    DateModified = 0x00000040
    DateAccessed = 0x00000080
    Attributes = 0x00000100
    FileListFileName = 0x00000200
    RunCount = 0x00000400
    DateRun = 0x00000800
    DateRecentlyChanged = 0x00001000
    HighlightedFileName = 0x00002000
    HighlightedPath = 0x00004000
    HighlightedFullPathAndFileName = 0x00008000
    All = 0x0000FFFF

# Define error codes
class Error:
    Ok = 0
    Memory = 1
    IPC = 2
    RegisterClassEx = 3
    CreateWindow = 4
    CreateThread = 5
    InvalidIndex = 6
    InvalidCall = 7

    @classmethod
    def get_error_message(cls, code):
        error_messages = {
            cls.Ok: "The operation completed successfully.",
            cls.Memory: "Failed to allocate memory for the search query.",
            cls.IPC: "IPC is not available.",
            cls.RegisterClassEx: "Failed to register the search query window class.",
            cls.CreateWindow: "Failed to create the search query window.",
            cls.CreateThread: "Failed to create the search query thread.",
            cls.InvalidIndex: (
                "Invalid index. The index must be greater or equal to 0 and less "
                "than the number of visible results."
            ),
            cls.InvalidCall: "Invalid call."
        }
        return error_messages.get(code, f"Unknown error code: {code}")

def _clean(text):
    """
    Replace angle brackets: show_alert goes through QMessageBox, which auto-detects
    rich text, so a wrapped urllib error like "<urlopen error [WinError 10061] ...>"
    would be parsed as an unknown HTML tag and vanish from the dialog entirely.

    Line breaks are deliberately preserved. QMessageBox sizes a plain-text dialog to
    its longest line; one long paragraph makes it word-wrap into a tall, narrow
    column, so messages are written as short explicit lines. `_one_line()` flattens
    them again for the pane, which renders single-line cells.
    """
    return str(text).strip().replace('<', '[').replace('>', ']')

def _one_line(text):
    """Collapse a message to a single line, for rendering in a table cell."""
    return ' '.join(str(text).split())

class Timeout(Exception):
    """A call into the Everything DLL did not return in time."""

class SearchUnavailable(Exception):
    """
    A search could not be performed. Both strings are finished, user-facing text:
    `headline` says what is wrong, `remedy` says what to do about it. They are
    rendered as the Name and Path cells of the single row shown in the pane.
    """
    def __init__(self, headline, remedy=""):
        super().__init__(headline)
        self.headline = _one_line(_clean(headline))
        self.remedy = _clean(remedy)

    @property
    def alert_text(self):
        """Multi-line text for show_alert."""
        return f"{self.headline}\n\n{self.remedy}" if self.remedy else self.headline

    @property
    def cell_remedy(self):
        """Single-line text for the pane's Path column."""
        return _one_line(self.remedy)

class StatusMessage:
    """Show a status message while a block runs. Same idiom as fman's Core plugin."""
    def __init__(self, message):
        self._message = message

    def __enter__(self):
        show_status_message(self._message)

    def __exit__(self, *_):
        clear_status_message()

def _is_placeholder(path):
    """
    True for the single non-result row iterdir() yields.

    fman joins the yielded name onto the current directory, so the url is
    'everything:///<search term>/placeholder' and `path` is '/<term>/placeholder'
    - never the bare string. Comparing with == silently never matched, which is
    why the raw word "placeholder" showed up in the pane instead of a message.
    """
    return path == 'placeholder' or path.endswith('/placeholder')

# Everything's IPC can block forever rather than fail: with Everything running
# elevated (or the Everything service stopped), WinDLL() loads fine but the first
# IPC call - even Everything_GetMajorVersion() - never returns. No try/except can
# catch that, so every DLL call goes through a thread we are willing to abandon.
def _call_with_timeout(fn, secs):
    box = {}
    def run():
        try:
            box['ok'] = fn()
        except BaseException as e:
            box['err'] = e
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(secs)
    if thread.is_alive():
        # Deliberately abandoned. Joining again would hang us too.
        raise Timeout()
    if 'err' in box:
        raise box['err']
    return box['ok']

# Once an IPC call has hung, the DLL stays wedged for the life of the process.
# Without this flag every later search leaks another permanently blocked thread
# and waits out the full timeout again.
_dll_hung = False

# Keep every message to a couple of short lines. fman themes can set a large font
# and centre-align dialog text, so QMessageBox re-wraps at roughly 20 characters
# no matter how the lines are written - a long message becomes a column taller than
# the screen. Anything diagnostic belongs in the log, not here.

# Everything_GetMajorVersion() returns 0 both when Everything is not running and
# when it is running but its IPC is unreachable - the two are indistinguishable
# from here, so the message covers both rather than guessing wrong.
NOT_RUNNING_MSG = "Everything is not running, or fman cannot reach it."
NOT_RUNNING_REMEDY = "Start Everything, then search again."
HUNG_MSG = "Everything is running but fman cannot reach it."
HUNG_REMEDY = "Start the Everything service, then restart fman."
# Everything answers version queries but has no index loaded. Checking this is not
# just cosmetic: a search issued in this state never returns, so the preflight turns
# a 10-second hang into an instant message.
INDEXING_MSG = "Everything has no index loaded."
INDEXING_REMEDY = "Start the Everything service:\nsc start Everything"

# Set when the DLL could not be loaded at all; a (headline, remedy) pair.
DLL_LOAD_ERROR = None

# Global variable to hold the DLL instance
everything_dll = None

# Load the DLL if needed
if SETTINGS['mode'] == 'dll':
    try:
        # Get the directory where the plugin is located
        plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dll_path = os.path.join(plugin_dir, SETTINGS['dll_path'])
        logger.info(f"Loading Everything DLL from {dll_path}")

        # Check if the DLL file exists
        if not os.path.exists(dll_path):
            error_msg = f"DLL file not found at {dll_path}"
            logger.error(error_msg)
            DLL_LOAD_ERROR = (
                "Everything DLL not found.",
                "Check \"dll_path\" in settings.json."
            )
        else:
            # Load the DLL
            everything_dll = ctypes.WinDLL(dll_path)

            # Set function argument and return types
            everything_dll.Everything_SetSearchW.argtypes = [LPCWSTR]
            everything_dll.Everything_SetSearchW.restype = None

            everything_dll.Everything_SetRequestFlags.argtypes = [DWORD]
            everything_dll.Everything_SetRequestFlags.restype = None

            everything_dll.Everything_QueryW.argtypes = [BOOL]
            everything_dll.Everything_QueryW.restype = BOOL

            everything_dll.Everything_GetLastError.argtypes = []
            everything_dll.Everything_GetLastError.restype = DWORD

            everything_dll.Everything_GetNumResults.argtypes = []
            everything_dll.Everything_GetNumResults.restype = DWORD

            everything_dll.Everything_GetResultFullPathNameW.argtypes = [
                DWORD, LPWSTR, DWORD
            ]
            everything_dll.Everything_GetResultFullPathNameW.restype = DWORD

            everything_dll.Everything_GetResultSize.argtypes = [DWORD, PULARGE_INTEGER]
            everything_dll.Everything_GetResultSize.restype = BOOL

            everything_dll.Everything_GetResultDateModified.argtypes = [
                DWORD, PULARGE_INTEGER
            ]
            everything_dll.Everything_GetResultDateModified.restype = BOOL

            # Cheap preflight calls: distinguish "not running" and "still indexing"
            # from a real search failure.
            everything_dll.Everything_GetMajorVersion.argtypes = []
            everything_dll.Everything_GetMajorVersion.restype = DWORD

            everything_dll.Everything_IsDBLoaded.argtypes = []
            everything_dll.Everything_IsDBLoaded.restype = BOOL

            logger.info("Successfully loaded Everything DLL API")
    except Exception as e:
        error_msg = f"Error loading Everything DLL: {str(e)}"
        logger.error(error_msg)
        everything_dll = None
        if getattr(e, 'winerror', None) == 193:
            bits = 8 * ctypes.sizeof(ctypes.c_void_p)
            wanted = 'Everything64.dll' if bits == 64 else 'Everything32.dll'
            DLL_LOAD_ERROR = (
                "Wrong Everything DLL bitness.",
                f"fman is {bits}-bit - set \"dll_path\" to {wanted} in settings.json."
            )
        else:
            DLL_LOAD_ERROR = (
                "Everything DLL could not be loaded.",
                "See fman_everything.log in your temp folder."
            )
