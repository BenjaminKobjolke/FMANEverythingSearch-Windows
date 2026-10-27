import ctypes
import datetime
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .core import (
    DLL_LOAD_ERROR, Error, HUNG_MSG, HUNG_REMEDY, INDEXING_MSG,
    INDEXING_REMEDY, NOT_RUNNING_MSG, NOT_RUNNING_REMEDY, Request,
    SearchUnavailable, Timeout, _call_with_timeout, everything_dll,
)
from .settings import SETTINGS, logger, log_file

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
            logger.debug("Raw response prefix: %s", response_data[:200])

            data = json.loads(response_data.decode('utf-8'))

            # Log result count
            count = data.get('count', 0)
            logger.info(f"API returned {count} results")

            # Return the count and results
            return count, data.get('results', [])
    except urllib.error.HTTPError as e:
        logger.error(f"HTTP Error: {e.code} - {e.reason}")
        if e.code == 400:
            raise Exception(
                f"Bad request to Everything API: The query '{query}' is invalid"
            )
        elif e.code == 404:
            raise Exception("Everything API endpoint not found. Check the API URL")
        else:
            raise Exception(f"HTTP Error {e.code} from Everything API: {e.reason}")
    except urllib.error.URLError as e:
        logger.error(f"URL Error: {str(e)}")
        raise Exception(
            f"Error connecting to Everything API: {e}\n"
            "Make sure the API server is running"
        )
    except json.JSONDecodeError as e:
        logger.error(f"JSON Decode Error: {str(e)}")
        raise Exception(
            "Error parsing response from Everything API. The response is not valid JSON"
        )
    except Exception as e:
        logger.error(
            f"Unexpected error in search_everything_api: {str(e)}", exc_info=True
        )
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
        everything_dll.Everything_SetRequestFlags(
            Request.FullPathAndFileName | Request.DateModified | Request.Size
        )

        # Execute the search
        logger.info("Executing search query")
        # Call the DLL function directly
        if not everything_dll.Everything_QueryW(True):
            error_code = everything_dll.Everything_GetLastError()
            error_msg = (
                f"Everything DLL search error: {Error.get_error_message(error_code)}"
            )
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
            if everything_dll.Everything_GetResultFullPathNameW(
                i, filename_buffer, 32767
            ):
                filename = filename_buffer.value

                # Get the file size
                size = 0
                size_value = ctypes.c_ulonglong(0)
                if everything_dll.Everything_GetResultSize(i, ctypes.byref(size_value)):
                    size = size_value.value

                # Get the date modified
                date_modified = None
                date_value = ctypes.c_ulonglong(0)
                if everything_dll.Everything_GetResultDateModified(
                    i, ctypes.byref(date_value)
                ):
                    # Convert Windows file time to datetime
                    winticks = date_value.value
                    if winticks > 0:
                        try:
                            timestamp = (winticks - 116444736000000000) / 10000000
                            date_modified = datetime.datetime.fromtimestamp(timestamp)
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
