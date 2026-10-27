import urllib.parse
from fman import DirectoryPaneCommand

from .core import SearchUnavailable, StatusMessage
from .filesystem import get_everything_fs
from .settings import logger

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
    from . import show_alert, show_prompt

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
