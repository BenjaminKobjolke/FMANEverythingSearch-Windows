import os
import json
import threading
import urllib.request
import urllib.parse
import urllib.error
import logging
import tempfile
from fman import DirectoryPaneCommand, DirectoryPaneListener, show_alert, \
    show_prompt, show_status_message, clear_status_message
from fman.fs import FileSystem, Column, cached
from fman.url import as_url, splitscheme, basename, dirname
from datetime import datetime

# Set up logging in the system's temporary directory.
# basicConfig() is NOT usable here: fman configures the root logger before plugins
# import, so basicConfig() is a silent no-op and the log file is never created.
# Attach our own handler instead, and don't propagate into fman's own log.
log_file = os.path.join(tempfile.gettempdir(), 'fman_everything.log')
logger = logging.getLogger('everything')
logger.setLevel(logging.DEBUG)
logger.propagate = False
if not logger.handlers:
    try:
        _handler = logging.FileHandler(log_file, encoding='utf-8')
        _handler.setFormatter(
            logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        )
        logger.addHandler(_handler)
    except OSError:
        # A locked or unwritable temp file must not stop the plugin from loading.
        pass
logger.info('Everything plugin initialized')

# Default settings
DEFAULT_API_ENDPOINT = "http://localhost:5000/everything-search-api/search"
DEFAULT_SEARCH_MODE = "dll"
# Must match the bitness of the Python that fman ships (64-bit). A 32-bit default
# here means a missing settings.json silently produces WinError 193 on load.
DEFAULT_DLL_PATH = "Everything64.dll"

# Read settings from settings.ini if available
def read_settings():
    settings = {
        'mode': DEFAULT_SEARCH_MODE,
        'api_endpoint': DEFAULT_API_ENDPOINT,
        'dll_path': DEFAULT_DLL_PATH
    }
    
    try:
        # Get the directory where the plugin is located
        plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        settings_path = os.path.join(plugin_dir, 'settings.ini')
        
        if os.path.exists(settings_path):
            logger.info(f"Reading settings from {settings_path}")
            
            # Read the INI file
            import configparser
            config = configparser.ConfigParser()
            config.read(settings_path)
            
            if 'search' in config:
                if 'mode' in config['search']:
                    settings['mode'] = config['search']['mode']
                if 'api_endpoint' in config['search']:
                    settings['api_endpoint'] = config['search']['api_endpoint']
                if 'dll_path' in config['search']:
                    settings['dll_path'] = config['search']['dll_path']
            
            logger.info(f"Settings loaded: {settings}")
        else:
            # Try to read from settings.json for backward compatibility
            settings_path = os.path.join(plugin_dir, 'settings.json')
            if os.path.exists(settings_path):
                logger.info(f"Reading settings from {settings_path}")
                with open(settings_path, 'r') as f:
                    config = json.load(f)
                
                if 'search' in config:
                    if 'mode' in config['search']:
                        settings['mode'] = config['search']['mode']
                    if 'api_endpoint' in config['search']:
                        settings['api_endpoint'] = config['search']['api_endpoint']
                    if 'dll_path' in config['search']:
                        settings['dll_path'] = config['search']['dll_path']
                
                logger.info(f"Settings loaded: {settings}")
            else:
                logger.info("Settings file not found, using defaults")
    except Exception as e:
        logger.error(f"Error reading settings: {str(e)}")
        logger.info("Using default settings")
    
    return settings

# Load settings
SETTINGS = read_settings()

# Import ctypes for DLL access
import ctypes
from ctypes.wintypes import BOOL, LPCWSTR, DWORD, LPWSTR, PULARGE_INTEGER

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
            cls.InvalidIndex: "Invalid index. The index must be greater or equal to 0 and less than the number of visible results.",
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
            
            everything_dll.Everything_GetResultFullPathNameW.argtypes = [DWORD, LPWSTR, DWORD]
            everything_dll.Everything_GetResultFullPathNameW.restype = DWORD
            
            everything_dll.Everything_GetResultSize.argtypes = [DWORD, PULARGE_INTEGER]
            everything_dll.Everything_GetResultSize.restype = BOOL
            
            everything_dll.Everything_GetResultDateModified.argtypes = [DWORD, PULARGE_INTEGER]
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

# Singleton instance of EverythingFS
_everything_fs_instance = None
def get_everything_fs():
    global _everything_fs_instance
    if _everything_fs_instance is None:
        _everything_fs_instance = EverythingFS()
    return _everything_fs_instance

def search_everything_api(query):
    """
    Search Everything using the HTTP API
    
    Args:
        query: The search query
        
    Returns:
        A tuple of (count, results) where results is a list of dictionaries
        with keys: filename, path, size, date_modified
        
    Raises:
        Exception: If the API request fails
    """
    try:
        # Log the original query
        logger.info(f"API search - Original query: '{query}'")
            
        # Encode the query for URL
        encoded_query = urllib.parse.quote(query)
        
        # Make the request
        url = f"{SETTINGS['api_endpoint']}?q={encoded_query}"
        logger.info(f"Making API request to: {url}")
        
        with urllib.request.urlopen(url, timeout=5) as response:
            # Log response status
            logger.info(f"API response status: {response.status}")
            
            # Parse the JSON response
            response_data = response.read()
            logger.debug(f"Raw response: {response_data[:200]}...")  # Log first 200 chars
            
            data = json.loads(response_data.decode('utf-8'))
            
            # Log result count
            count = data.get('count', 0)
            logger.info(f"API returned {count} results")
            
            # Return the count and results
            return count, data.get('results', [])
    except urllib.error.HTTPError as e:
        logger.error(f"HTTP Error: {e.code} - {e.reason}")
        if e.code == 400:
            raise Exception(f"Bad request to Everything API: The query '{query}' is invalid")
        elif e.code == 404:
            raise Exception("Everything API endpoint not found. Check the API URL")
        else:
            raise Exception(f"HTTP Error {e.code} from Everything API: {e.reason}")
    except urllib.error.URLError as e:
        logger.error(f"URL Error: {str(e)}")
        raise Exception(f"Error connecting to Everything API: {str(e)}\nMake sure the API server is running")
    except json.JSONDecodeError as e:
        logger.error(f"JSON Decode Error: {str(e)}")
        raise Exception("Error parsing response from Everything API. The response is not valid JSON")
    except Exception as e:
        logger.error(f"Unexpected error in search_everything_api: {str(e)}", exc_info=True)
        raise Exception(f"Error searching Everything API: {str(e)}")

def search_everything_dll(query):
    """
    Search Everything using the DLL API directly
    
    Args:
        query: The search query
        
    Returns:
        A tuple of (count, results) where results is a list of dictionaries
        with keys: filename, path, size, date_modified
        
    Raises:
        Exception: If the DLL search fails
    """
    try:
        # Log the original query
        logger.info(f"DLL search - Original query: '{query}'")
        
        # Check if the DLL is loaded
        if everything_dll is None:
            error_msg = "Everything DLL not loaded"
            logger.error(error_msg)
            raise Exception(error_msg)
        
        # Set the search query
        logger.info("Setting search query")
        # Call the DLL function directly
        everything_dll.Everything_SetSearchW(query)
        
        # Set request flags
        logger.info("Setting request flags")
        # Call the DLL function directly
        everything_dll.Everything_SetRequestFlags(Request.FullPathAndFileName | Request.DateModified | Request.Size)
        
        # Execute the search
        logger.info("Executing search query")
        # Call the DLL function directly
        if not everything_dll.Everything_QueryW(True):
            error_code = everything_dll.Everything_GetLastError()
            error_msg = f"Everything DLL search error: {Error.get_error_message(error_code)}"
            logger.error(error_msg)
            raise Exception(error_msg)
        
        # Get the number of results
        count = everything_dll.Everything_GetNumResults()
        logger.info(f"DLL returned {count} results")
        
        # Process the results
        results = []
        for i in range(min(count, 100)):  # Limit to 100 results for performance
            # Get the full path and filename
            filename_buffer = ctypes.create_unicode_buffer(32767)  # MAX_PATH
            if everything_dll.Everything_GetResultFullPathNameW(i, filename_buffer, 32767):
                filename = filename_buffer.value
                
                # Get the file size
                size = 0
                size_value = ctypes.c_ulonglong(0)
                if everything_dll.Everything_GetResultSize(i, ctypes.byref(size_value)):
                    size = size_value.value
                
                # Get the date modified
                date_modified = None
                date_value = ctypes.c_ulonglong(0)
                if everything_dll.Everything_GetResultDateModified(i, ctypes.byref(date_value)):
                    # Convert Windows file time to datetime
                    winticks = date_value.value
                    if winticks > 0:
                        try:
                            date_modified = datetime.fromtimestamp((winticks - 116444736000000000) / 10000000)
                            date_modified = date_modified.isoformat()
                        except Exception as e:
                            logger.error(f"Error converting date: {str(e)}")
                
                # Add the result to the list
                if filename:
                    results.append({
                        'filename': os.path.basename(filename),
                        'path': filename,
                        'size': size,
                        'date_modified': date_modified
                    })
        
        logger.info(f"Processed {len(results)} results")
        return count, results
    except Exception as e:
        error_msg = f"Error searching Everything DLL: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise Exception(error_msg)

def _dll_probe_and_search(query):
    """
    Preflight then search, on the caller's thread. Runs inside _call_with_timeout,
    so all three IPC calls share one timeout instead of needing three.

    Raises:
        SearchUnavailable: if Everything is not running or is still indexing
    """
    if not everything_dll.Everything_GetMajorVersion():
        raise SearchUnavailable(NOT_RUNNING_MSG, NOT_RUNNING_REMEDY)
    if not everything_dll.Everything_IsDBLoaded():
        raise SearchUnavailable(INDEXING_MSG, INDEXING_REMEDY)
    return search_everything_dll(query)

def search_everything(query):
    """
    Search Everything using either the HTTP API or DLL API based on settings
    
    Args:
        query: The search query
        
    Returns:
        A tuple of (count, results) where results is a list of dictionaries
        with keys: filename, path, size, date_modified
        
    Raises:
        Exception: If the search fails
    """
    global _dll_hung

    if SETTINGS['mode'] != 'dll':
        try:
            return search_everything_api(query)
        except Exception as e:
            raise SearchUnavailable(
                "Everything API server unreachable.",
                f"{SETTINGS['api_endpoint']} - {e}"
            )

    # Work out why the DLL path is unusable, if it is. None means "it worked".
    dll_problem = None
    if DLL_LOAD_ERROR:
        dll_problem = DLL_LOAD_ERROR
    elif everything_dll is None:
        dll_problem = ("Everything DLL was not loaded.", f"See {log_file}")
    elif _dll_hung:
        dll_problem = (HUNG_MSG, HUNG_REMEDY)
    else:
        try:
            return _call_with_timeout(lambda: _dll_probe_and_search(query), 10)
        except Timeout:
            _dll_hung = True
            logger.error("Everything DLL call timed out; disabling DLL mode")
            dll_problem = (HUNG_MSG, HUNG_REMEDY)
        except SearchUnavailable as e:
            dll_problem = (e.headline, e.remedy)
        except Exception as e:
            dll_problem = ("Everything search failed.", str(e))

    # The DLL is out. Fall back to the API - but say so, rather than reporting a
    # localhost connection error for a problem that has nothing to do with it.
    logger.error(f"DLL search unavailable ({dll_problem[0]}), falling back to API")
    try:
        return search_everything_api(query)
    except Exception as api_error:
        # Full API error goes to the log only. Appending it to the dialog turns a
        # readable message into a wall of text about a subsystem the user is
        # probably not even using.
        logger.error(f"API fallback also failed: {api_error}")
        # The API fallback's own error stays in the log. Appending it to the dialog
        # doubles the message length to describe a subsystem the user is usually
        # not running, and pushes the actionable part off the screen.
        raise SearchUnavailable(dll_problem[0], dll_problem[1])

# Last term the user confirmed in the prompt, kept so a failed search (Everything
# down, no results, too short) can be retried without retyping it. Preselected in
# the next prompt, so Enter repeats it and Del/typing replaces it.
# ponytail: process-local only; persist via save_json if it should survive restarts.
_last_search_term = ''

def _set_everything_path(pane_command):
    """
    Prompts the user for a search term and sets the path for the Everything file system.
    
    Args:
        pane_command: The DirectoryPaneCommand object.
    """
    global _last_search_term

    # Show prompt to enter search term, prefilled with the previous one and fully
    # selected.
    search_term, ok = show_prompt(
        'Enter search term (minimum 3 characters)', _last_search_term,
        0, len(_last_search_term)
    )
    
    # Check if user cancelled or entered a valid search term
    if not ok:
        logger.info("User cancelled search prompt")
        return  # User cancelled
    
    _last_search_term = search_term
    logger.info(f"User entered search term: '{search_term}'")
    
    if len(search_term) < 3:
        logger.warning(f"Search term too short: '{search_term}'")
        show_alert("Please enter at least 3 characters.")
        return

    # Search here, before navigating. This runs on fman's command thread, where
    # blocking - and therefore a modal alert - is safe; iterdir() runs on the pane
    # model, where it is not. On success the results are cached, so the iterdir()
    # that follows set_path() is a pure cache read.
    try:
        with StatusMessage(f"Searching Everything for '{search_term}'..."):
            count, results = get_everything_fs().update_search(search_term)
    except SearchUnavailable as e:
        show_alert(e.alert_text)
        return
    if not results:
        show_alert(f"No files matching '{search_term}'.")
        return

    # URL encode the search term to handle special characters like colons
    encoded_search_term = urllib.parse.quote(search_term)
    logger.info(f"Encoded search term: '{encoded_search_term}'")

    # Set the path to our custom file system with the encoded search term as a parameter
    # Use a slash instead of a question mark
    pane_command.pane.set_path(f"everything:///{encoded_search_term}")
    logger.info(f"Set path to everything:///{encoded_search_term}")
    return True

class Everything(DirectoryPaneCommand):
    def __call__(self):
        logger.info("Everything plugin started")
        
        _set_everything_path(self)

class EverythingFS(FileSystem):
    scheme = 'everything://'
    
    # Static class variables for caching
    _current_search_term = ""
    _results_cache = {}  # Dictionary to store results with query as key
    _last_selected_file = None  # Store the last selected file
    _last_selected_index = 0
    # (headline, remedy) for the placeholder row, or None. This is the whole user
    # feedback channel: iterdir runs on the pane model, where a modal dialog would
    # block it, so failures are rendered as the row's Name and Path cells instead.
    _last_error = None
    
    def __init__(self):
        super().__init__()
    
    def get_default_columns(self, path):
        return 'everything.Name', 'everything.Path', 'everything.Size', 'everything.DateModified'
    
    def _safe_update_search(self, search_term):
        """
        update_search(), but never raising. iterdir() is driven by the pane model:
        an exception escaping it becomes fman's generic "Plugin 'FMANEverything'
        raised an error." Store the reason for the placeholder row instead.
        """
        try:
            self.update_search(search_term)
        except SearchUnavailable as e:
            EverythingFS._last_error = (e.headline, e.cell_remedy)

    def iterdir(self, path):
        # Log the path
        logger.info(f"iterdir called with path: '{path}'")
        
        # Extract search term from path if present
        # First check for the old format with a question mark
        if '?' in path:
            base_path, search_term = path.split('?', 1)
            logger.info(f"Extracted search term (old format): '{search_term}' from path")
            # URL decode the search term
            search_term = urllib.parse.unquote(search_term)
            logger.info(f"Decoded search term: '{search_term}'")
            self._safe_update_search(search_term)
        # Then check for the new format with a slash
        elif path.count('/') >= 1:
            # Skip the first slash if it exists
            if path.startswith('/'):
                # Check if this is a result item path (e.g., "/0", "/1", etc.)
                if path[1:].isdigit():
                    # This is a result item path, not a search term
                    logger.info(f"Path '{path}' is a result item path, not a search term")
                    # Keep the current search term
                    search_term = EverythingFS._current_search_term
                else:
                    # This is a search term path
                    encoded_search_term = path[1:]
                    logger.info(f"Extracted encoded search term (new format): '{encoded_search_term}' from path")
                    # URL decode the search term
                    search_term = urllib.parse.unquote(encoded_search_term)
                    logger.info(f"Decoded search term: '{search_term}'")
                    self._safe_update_search(search_term)
            else:
                encoded_search_term = path
                logger.info(f"Extracted encoded search term (new format without leading slash): '{encoded_search_term}' from path")
                # URL decode the search term
                search_term = urllib.parse.unquote(encoded_search_term)
                logger.info(f"Decoded search term: '{search_term}'")
                self._safe_update_search(search_term)
        else:
            # Default to empty search
            logger.info("No search term in path, using empty search")
            self._safe_update_search("")
        
        # If search term is too short, show placeholder message
        if len(EverythingFS._current_search_term) < 3:
            logger.info(f"Search term too short ({len(EverythingFS._current_search_term)} chars), showing placeholder")
            yield "placeholder"
        else:
            # Return actual search results
            search_term = EverythingFS._current_search_term
            if search_term in EverythingFS._results_cache:
                count, results = EverythingFS._results_cache[search_term]
                logger.info(f"Returning {len(results)} search results from cache")
                for i in range(len(results)):
                    # Add a slash before the index to ensure it's recognized as a path component
                    yield f"/{i}"
            else:
                logger.info("No results in cache")
                yield "placeholder"
    
    def update_search(self, search_term):
        EverythingFS._current_search_term = search_term
        
        # Debug: Log search term and length
        logger.debug(f"update_search: '{search_term}', Length: {len(search_term)}")
        
        # Only perform search if search term is at least 3 characters long
        if len(search_term) >= 3:
            # Check if we already have results for this search term
            if search_term in EverythingFS._results_cache:
                logger.info(f"Using cached results for '{search_term}'")
                EverythingFS._last_error = None
                return EverythingFS._results_cache[search_term]
            
            try:
                # Debug: Log that we're performing the search
                logger.info(f"Performing search with query: '{search_term}'")

                # Perform the search
                count, results = search_everything(search_term)

                # Limit the number of results to 100 for performance
                if len(results) > 100:
                    results = results[:100]
                    logger.info("Limited to 100 results")

                if results:
                    # Never cache an empty result set: a search run while Everything
                    # was still indexing would otherwise keep returning nothing for
                    # the rest of the process.
                    if len(EverythingFS._results_cache) > 20:
                        EverythingFS._results_cache.clear()
                    EverythingFS._results_cache[search_term] = (count, results)
                    EverythingFS._last_error = None
                    logger.info(f"Cached results for '{search_term}': {count} items")
                else:
                    # A successful search that matched nothing is not an error, and
                    # must not look like one.
                    EverythingFS._last_error = (
                        f"No files matching '{search_term}'.", ""
                    )
                    logger.info(f"No results for '{search_term}'")
                return count, results
            except SearchUnavailable as e:
                logger.error(f"Search unavailable: {e.headline} {e.remedy}")
                # Not cached, so fixing Everything and searching again just works.
                if search_term in EverythingFS._results_cache:
                    del EverythingFS._results_cache[search_term]
                raise
        else:
            # If search term is too short or empty, clear current search term
            logger.debug("Search term too short, not updating cache")
    
    def resolve(self, path):
        # Handle placeholder item
        if _is_placeholder(path):
            return self.scheme + path
        
        # Extract the index from the path
        if '/' in path:
            base_path, index = path.rsplit('/', 1)
            try:
                index = int(index)
                # Get the actual file path
                search_term = EverythingFS._current_search_term
                if search_term in EverythingFS._results_cache:
                    count, results = EverythingFS._results_cache[search_term]
                    if 0 <= index < len(results):
                        file_path = results[index]['path']
                        return as_url(file_path)
            except (ValueError, IndexError, KeyError):
                pass
        return self.scheme + path
    
    @cached
    def is_dir(self, path):
        # Handle placeholder item
        if _is_placeholder(path):
            return False
        
        # Check if the path is a directory
        if '/' in path:
            base_path, index = path.rsplit('/', 1)
            try:
                index = int(index)
                search_term = EverythingFS._current_search_term
                if search_term in EverythingFS._results_cache:
                    count, results = EverythingFS._results_cache[search_term]
                    if 0 <= index < len(results):
                        # Check if the path ends with a directory separator
                        file_path = results[index]['path']
                        return os.path.isdir(file_path)
            except (ValueError, IndexError, KeyError):
                pass
        return False
    
    def get_item_at_index(self, index):
        try:
            index = int(index)
            search_term = EverythingFS._current_search_term
            if len(search_term) < 3:
                return None
            
            if search_term in EverythingFS._results_cache:
                count, results = EverythingFS._results_cache[search_term]
                if 0 <= index < len(results):
                    return results[index]
            else:
                logger.info(f"get_item_at_index: No results for '{search_term}'")
            return None
        except (ValueError, IndexError):
            return None

class Name(Column):
    def get_str(self, url):
        scheme, path = splitscheme(url)
        logger.debug(f"Name.get_str called with url: '{url}', scheme: '{scheme}', path: '{path}'")
        
        if scheme == 'everything://':
            # Handle placeholder item
            if _is_placeholder(path):
                if EverythingFS._last_error:
                    return EverythingFS._last_error[0]
                return "Search using 'Everything' from the command palette"
            
            if '/' in path:
                # More general extraction
                parts = path.split('/')
                # The last non-empty part should be the index
                index_str = next((p for p in reversed(parts) if p), None)
                if index_str and index_str.isdigit():
                    index = int(index_str)
                    logger.debug(f"Name.get_str: Complex index extraction: '{index}'")
                else:
                    logger.error(f"Name.get_str: Failed to extract index from path: '{path}'")
                    return basename(url)
            else:
                logger.error(f"Name.get_str: No slash in path: '{path}'")
                return basename(url)
            
            try:
                fs = get_everything_fs()
                item = fs.get_item_at_index(index)
                logger.debug(f"Name.get_str: item: {item}")
                
                if item and 'filename' in item:
                    filename = item['filename']
                    logger.debug(f"Name.get_str: returning filename: '{filename}'")
                    return filename
            except (ValueError, IndexError, KeyError) as e:
                logger.error(f"Name.get_str: Error getting filename: {str(e)}")
        
        result = basename(url)
        logger.debug(f"Name.get_str: falling back to basename: '{result}'")
        return result

class Path(Column):
    def get_str(self, url):
        scheme, path = splitscheme(url)
        logger.debug(f"Path.get_str called with url: '{url}', scheme: '{scheme}', path: '{path}'")
        
        if scheme == 'everything://':
            # Handle placeholder item
            if _is_placeholder(path):
                if EverythingFS._last_error:
                    return EverythingFS._last_error[1]
                return ""

            index:int = 0
            if '/' in path:
                # More general extraction
                parts = path.split('/')
                # The last non-empty part should be the index
                index_str = next((p for p in reversed(parts) if p), None)
                if index_str and index_str.isdigit():
                    index = int(index_str)
                    logger.debug(f"Path.get_str: Complex index extraction: '{index}'")
                else:
                    logger.error(f"Path.get_str: Failed to extract index from path: '{path}'")
                    return dirname(url)
            else:
                logger.error(f"Path.get_str: No slash in path: '{path}'")
                return dirname(url)
            
            try:
                fs = get_everything_fs()
                item = fs.get_item_at_index(index)
                logger.debug(f"Path.get_str: item: {item}")
                
                if item and 'path' in item:
                    # create a hash of the complete path    
                    file_directory = os.path.dirname(item['path'])
                    return file_directory
            except (ValueError, IndexError, KeyError) as e:
                logger.error(f"Path.get_str: Error getting path: {str(e)}")
        
        result = dirname(url)
        logger.debug(f"Path.get_str: falling back to dirname: '{result}'")
        return result

class Size(Column):
    def get_str(self, url):
        scheme, path = splitscheme(url)
        logger.debug(f"Size.get_str called with url: '{url}', scheme: '{scheme}', path: '{path}'")
        
        if scheme == 'everything://':
            # Handle placeholder item
            if _is_placeholder(path):
                return ""
            
            # Extract the index from the path
            # The path format is like "everything:///search term//0"
            # We need to extract the "0" part
            if path.endswith('/0') or path.endswith('/1') or path.endswith('/2'):
                # This is a simple check for common indices
                index = int(path[-1])
                logger.debug(f"Size.get_str: Simple index extraction: '{index}'")
            elif '/' in path:
                # More general extraction
                parts = path.split('/')
                # The last non-empty part should be the index
                index_str = next((p for p in reversed(parts) if p), None)
                if index_str and index_str.isdigit():
                    index = int(index_str)
                    logger.debug(f"Size.get_str: Complex index extraction: '{index}'")
                else:
                    logger.error(f"Size.get_str: Failed to extract index from path: '{path}'")
                    return ""
            else:
                logger.error(f"Size.get_str: No slash in path: '{path}'")
                return ""
            
            try:
                fs = get_everything_fs()
                item = fs.get_item_at_index(index)
                logger.debug(f"Size.get_str: item: {item}")
                
                if item and 'size' in item:
                    size = item['size']
                    # Format size
                    if size < 1024:
                        result = "{} B".format(size)
                    elif size < 1024 * 1024:
                        result = "{:.1f} KB".format(size / 1024)
                    elif size < 1024 * 1024 * 1024:
                        result = "{:.1f} MB".format(size / (1024 * 1024))
                    else:
                        result = "{:.1f} GB".format(size / (1024 * 1024 * 1024))
                    logger.debug(f"Size.get_str: returning size: '{result}'")
                    return result
            except (ValueError, IndexError, KeyError) as e:
                logger.error(f"Size.get_str: Error getting size: {str(e)}")
        
        logger.debug("Size.get_str: returning empty string")
        return ""

class DateModified(Column):
    def get_str(self, url):
        scheme, path = splitscheme(url)
        logger.debug(f"DateModified.get_str called with url: '{url}', scheme: '{scheme}', path: '{path}'")
        
        if scheme == 'everything://':
            # Handle placeholder item
            if _is_placeholder(path):
                return ""
            
            # Extract the index from the path
            # The path format is like "everything:///search term//0"
            # We need to extract the "0" part
            if path.endswith('/0') or path.endswith('/1') or path.endswith('/2'):
                # This is a simple check for common indices
                index = int(path[-1])
                logger.debug(f"DateModified.get_str: Simple index extraction: '{index}'")
            elif '/' in path:
                # More general extraction
                parts = path.split('/')
                # The last non-empty part should be the index
                index_str = next((p for p in reversed(parts) if p), None)
                if index_str and index_str.isdigit():
                    index = int(index_str)
                    logger.debug(f"DateModified.get_str: Complex index extraction: '{index}'")
                else:
                    logger.error(f"DateModified.get_str: Failed to extract index from path: '{path}'")
                    return ""
            else:
                logger.error(f"DateModified.get_str: No slash in path: '{path}'")
                return ""
            
            try:
                fs = get_everything_fs()
                item = fs.get_item_at_index(index)
                logger.debug(f"DateModified.get_str: item: {item}")
                
                if item and 'date_modified' in item:
                    try:
                        # Parse the ISO format date using strptime instead of fromisoformat
                        date_str = item['date_modified']
                        # Remove the 'Z' or timezone info for parsing
                        if 'Z' in date_str:
                            date_str = date_str.replace('Z', '')
                        elif '+' in date_str:
                            date_str = date_str.split('+')[0]
                        elif '-' in date_str and date_str.count('-') > 2:  # More than just date separators
                            date_str = date_str.rsplit('-', 1)[0]
                        
                        # Parse the date
                        date = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S')
                        result = date.strftime("%Y-%m-%d %H:%M:%S")
                        logger.debug(f"DateModified.get_str: returning date: '{result}'")
                        return result
                    except Exception as e:
                        logger.error(f"DateModified.get_str: Error parsing date: {str(e)}")
                        # Return the raw date string as fallback
                        return item['date_modified']
            except (ValueError, IndexError, KeyError) as e:
                logger.error(f"DateModified.get_str: Error getting date: {str(e)}")
        
        logger.debug("DateModified.get_str: returning empty string")
        return ""

# Handle opening files directly from Everything search results
class EverythingOpenListener(DirectoryPaneListener):
    def on_doubleclicked(self, file_url):
        scheme, path = splitscheme(file_url)
        if scheme != 'everything://':
            return
        
        # Handle placeholder item
        if _is_placeholder(path):
            return
        
        # Extract the index from the path
        # The path format is like "everything:///search term//0"
        if path.endswith('/0') or path.endswith('/1') or path.endswith('/2'):
            # This is a simple check for common indices
            index = int(path[-1])
        elif '/' in path:
            # More general extraction
            parts = path.split('/')
            # The last non-empty part should be the index
            index_str = next((p for p in reversed(parts) if p), None)
            if index_str and index_str.isdigit():
                index = int(index_str)
            else:
                return
        else:
            return
        
        try:
            fs = get_everything_fs()
            item = fs.get_item_at_index(index)
            if item and 'path' in item:
                file_path = item['path']
                if os.path.isdir(file_path):
                    # Navigate to the directory
                    self.pane.set_path(as_url(file_path))
                else:
                    # Open the file directly without navigating to its directory
                    logger.info(f"Opening file directly: {file_path}")
                    # Use the 'open' command directly with the file URL
                    self.pane.run_command('open', {'url': as_url(file_path)})
                    # Explicitly return True to prevent default behavior
                    return True
        except (ValueError, IndexError, KeyError) as e:
            logger.error(f"Error handling double-click: {str(e)}")
        
        # Always return True for everything:// URLs to prevent default behavior
        if scheme == 'everything://':
            return True
    
    def on_command(self, command_name, args):
        if command_name == 'open_file':
            if 'url' in args:
                url = args['url']
                scheme, path = splitscheme(url)
                if scheme == 'everything://':
                    # Handle placeholder item
                    if _is_placeholder(path):
                        _set_everything_path(self)
                        return
                    
                    index:int = 0

                    if '/' in path:
                        # More general extraction
                        parts = path.split('/')
                        # The last non-empty part should be the index
                        index_str = next((p for p in reversed(parts) if p), None)
                        if index_str and index_str.isdigit():
                            index = int(index_str)
                        else:
                            return None
                    else:
                        return None
                    
                    try:
                        fs = get_everything_fs()
                        item = fs.get_item_at_index(index)
                        if item and 'path' in item:
                            file_path = item['path']
                            if os.path.isdir(file_path):
                                # Navigate to the directory
                                self.pane.set_path(as_url(file_path))
                                return True
                            else:
                                # Store the file path as an instance variable
                                self.file_path = file_path
                                # Store the file path in the EverythingFS class for later use
                                fs._last_selected_file = file_path
                                fs._last_selected_index = index
                                # Store the selected file path
                                # Get the parent directory
                                parent_dir = os.path.dirname(file_path)
                                
                                # Navigate to the parent directory with a callback
                                def callback():
                                    # Get the full URL of the file
                                    file_url = as_url(self.file_path)
                                    #s how_alert(f"Callback: Placing cursor at: {file_url}")
                                    logger.info(f"Placing cursor at: {file_url}")
                                    
                                    # First place the cursor at the file (this will scroll it into view)
                                    self.pane.place_cursor_at(file_url)
                                    
                                    # Then select the file
                                    #self.pane.select([file_url])
                                
                                self.pane.set_path(as_url(parent_dir), callback=callback)
                                
                                # Return a command to open the parent directory
                                logger.info(f"Navigating to parent directory: {parent_dir}")
                                return 'open_directory', {'url': as_url(parent_dir)}
                    except (ValueError, IndexError, KeyError) as e:
                        logger.error(f"Error handling open_file: {str(e)}")
        return None
    
    def callback(self):
        # This method is needed for the callback to work properly
        # Get the full URL of the file
        file_url = as_url(self.file_path)
        logger.info(f"Callback: Placing cursor at: {file_url}")
        
        # First place the cursor at the file (this will scroll it into view)
        self.pane.place_cursor_at(file_url)
        
        # Then select the file
        logger.info(f"Callback: Selecting file: {file_url}")
        self.pane.select([file_url])
    
    def on_path_changed(self):
        # Get the current path
        current_path = self.pane.get_path()
        scheme, path = splitscheme(current_path)

        # Check if we're navigating back to the search results
        if scheme == 'everything://':
            # Check if there's a stored file path
            fs = get_everything_fs()
            if fs._last_selected_file and fs._last_selected_index >= 0:
                try:
                    # Construct the file URL to match the format returned by iterdir
                    # The format should be: everything:///search_term/index
                    encoded_search_term = urllib.parse.quote(fs._current_search_term)
                    file_url = f"everything:///{encoded_search_term}/{fs._last_selected_index}"
                    logger.info(f"Attempting to place cursor at: {file_url}")
                    self.pane.place_cursor_at(file_url)
                except ValueError as e:
                    # If the file is not in the list, just ignore the error
                    logger.warning(f"Could not place cursor at {file_url}: {str(e)}")
                except Exception as e:
                    # Log any other errors but don't crash
                    logger.error(f"Error placing cursor: {str(e)}")
