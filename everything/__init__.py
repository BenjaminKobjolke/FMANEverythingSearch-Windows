import os
import json
import urllib.request
import urllib.parse
import urllib.error
import logging
import tempfile
from fman import DirectoryPaneCommand, DirectoryPaneListener, show_alert, show_prompt
from fman.fs import FileSystem, Column, cached
from fman.url import as_url, splitscheme, basename, dirname
from datetime import datetime

# Set up logging in the system's temporary directory
log_file = os.path.join(tempfile.gettempdir(), 'fman_everything.log')
logging.basicConfig(filename=log_file, level=logging.DEBUG, 
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('everything')
logger.info('Everything plugin initialized')

# Default settings
DEFAULT_API_ENDPOINT = "http://localhost:5000/everything-search-api/search"
DEFAULT_SEARCH_MODE = "dll"
DEFAULT_DLL_PATH = "Everything32.dll"

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
                logger.info(f"Settings file not found, using defaults")
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
            SETTINGS['mode'] = 'api'
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
            
            logger.info("Successfully loaded Everything DLL API")
    except Exception as e:
        error_msg = f"Error loading Everything DLL: {str(e)}"
        logger.error(error_msg)
        # Fallback to API mode if DLL loading fails
        SETTINGS['mode'] = 'api'
        logger.info("Falling back to API mode")

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
    if SETTINGS['mode'] == 'dll':
        try:
            return search_everything_dll(query)
        except Exception as e:
            logger.error(f"DLL search failed: {str(e)}, falling back to API")
            # Fallback to API if DLL search fails
            return search_everything_api(query)
    else:
        return search_everything_api(query)

class Everything(DirectoryPaneCommand):
    def __call__(self):
        # Show the log file path
        log_file = os.path.join(tempfile.gettempdir(), 'fman_everything.log')
        logger.info("Everything plugin started")
        
        # Show prompt to enter search term
        search_term, ok = show_prompt('Enter search term (minimum 3 characters)', default='')
        
        # Check if user cancelled or entered a valid search term
        if not ok:
            logger.info("User cancelled search prompt")
            return  # User cancelled
        
        logger.info(f"User entered search term: '{search_term}'")
        
        if len(search_term) < 3:
            logger.warning(f"Search term too short: '{search_term}'")
            return
        
        # URL encode the search term to handle special characters like colons
        encoded_search_term = urllib.parse.quote(search_term)
        logger.info(f"Encoded search term: '{encoded_search_term}'")
        
        # Set the path to our custom file system with the encoded search term as a parameter
        # Use a slash instead of a question mark
        self.pane.set_path(f"everything:///{encoded_search_term}")
        logger.info(f"Set path to everything:///{encoded_search_term}")

class EverythingFS(FileSystem):
    scheme = 'everything://'
    
    # Static class variables for caching
    _current_search_term = ""
    _results_cache = {}  # Dictionary to store results with query as key
    _last_selected_file = None  # Store the last selected file
    _last_selected_index = 0
    
    def __init__(self):
        super().__init__()
    
    def get_default_columns(self, path):
        return 'everything.Name', 'everything.Path', 'everything.Size', 'everything.DateModified'
    
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
            self.update_search(search_term)
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
                    self.update_search(search_term)
            else:
                encoded_search_term = path
                logger.info(f"Extracted encoded search term (new format without leading slash): '{encoded_search_term}' from path")
                # URL decode the search term
                search_term = urllib.parse.unquote(encoded_search_term)
                logger.info(f"Decoded search term: '{search_term}'")
                self.update_search(search_term)
        else:
            # Default to empty search
            logger.info("No search term in path, using empty search")
            self.update_search("")
        
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
                return
            
            try:
                # Debug: Log that we're performing the search
                logger.info(f"Performing search with query: '{search_term}'")
                
                # Perform the search
                count, results = search_everything(search_term)
                
                # Store in cache
                EverythingFS._results_cache[search_term] = (count, results)
                
                # Debug: Log search results
                logger.info(f"Cached results for '{search_term}': {count} items")
                # Log the count of cached results
                
                # Limit the number of results to 100 for performance
                if len(results) > 100:
                    results = results[:100]
                    EverythingFS._results_cache[search_term] = (count, results)
                    logger.info(f"Limited to 100 results")
            except Exception as e:
                # Handle API errors
                error_msg = f"Search error: {str(e)}"
                logger.error(error_msg)
                # Clear cache for this search term
                if search_term in EverythingFS._results_cache:
                    del EverythingFS._results_cache[search_term]
        else:
            # If search term is too short or empty, clear current search term
            logger.debug(f"Search term too short, not updating cache")
    
    def resolve(self, path):
        # Handle placeholder item
        if path == 'placeholder':
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
        if path == 'placeholder':
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
            if path == 'placeholder':
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
            if path == 'placeholder':
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
            if path == 'placeholder':
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
            if path == 'placeholder':
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
        if path == 'placeholder':
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
                    if path == 'placeholder' or path == '/placeholder':
                        # Do nothing when clicking on the placeholder
                        return False
                    
                    index:int = 0

                    if '/' in path:
                        # More general extraction
                        parts = path.split('/')
                        # The last non-empty part should be the index
                        show_alert(f"parts: {parts}")
                        index_str = next((p for p in reversed(parts) if p), None)
                        if index_str and index_str.isdigit():
                            show_alert(f"Extracted index: '{index_str}'")
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
                                show_alert(f"Opening directory: {file_path}")
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
                                show_alert(f"Opening parent directory: {parent_dir}")
                                return 'open_directory', {'url': as_url(parent_dir)}
                    except (ValueError, IndexError, KeyError) as e:
                        show_alert(f"Error handling open_file: {str(e)}")
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
            if fs._last_selected_file:
                #file_hash = hash(fs._last_selected_file)
                #file_url = as_url(str(file_hash))   
                # Encode the search term to handle special characters like colons
                encoded_search_term = urllib.parse.quote(fs._current_search_term)
                file_url = "everything:///" + encoded_search_term + "//" + str(fs._last_selected_index)        
                self.pane.place_cursor_at(file_url)
                #self.pane.select([file_url])
                pass
