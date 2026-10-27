import os
import urllib.parse
from fman.fs import FileSystem, cached
from fman.url import as_url

from .core import SearchUnavailable, _is_placeholder
from .settings import logger

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
        return (
            'everything.Name', 'everything.Path', 'everything.Size',
            'everything.DateModified'
        )

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
            logger.info(
                f"Extracted search term (old format): '{search_term}' from path"
            )
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
                    logger.info(
                        f"Path '{path}' is a result item path, not a search term"
                    )
                    # Keep the current search term
                    search_term = EverythingFS._current_search_term
                else:
                    # This is a search term path
                    encoded_search_term = path[1:]
                    logger.info(
                        "Extracted encoded search term (new format): "
                        f"'{encoded_search_term}' from path"
                    )
                    # URL decode the search term
                    search_term = urllib.parse.unquote(encoded_search_term)
                    logger.info(f"Decoded search term: '{search_term}'")
                    self._safe_update_search(search_term)
            else:
                encoded_search_term = path
                logger.info(
                    "Extracted encoded search term (new format without leading "
                    f"slash): '{encoded_search_term}' from path"
                )
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
            logger.info(
                f"Search term too short ({len(EverythingFS._current_search_term)} "
                "chars), showing placeholder"
            )
            yield "placeholder"
        else:
            # Return actual search results
            search_term = EverythingFS._current_search_term
            if search_term in EverythingFS._results_cache:
                count, results = EverythingFS._results_cache[search_term]
                logger.info(f"Returning {len(results)} search results from cache")
                for i in range(len(results)):
                    # Prefix the index so fman treats it as a path component.
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
                from . import search_everything
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


_everything_fs_instance = None

def get_everything_fs():
    global _everything_fs_instance
    if _everything_fs_instance is None:
        _everything_fs_instance = EverythingFS()
    return _everything_fs_instance
