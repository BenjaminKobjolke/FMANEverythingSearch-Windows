import os
import urllib.parse
from fman import DirectoryPaneListener
from fman.url import as_url, splitscheme

from .commands import _set_everything_path
from .core import _is_placeholder
from .filesystem import get_everything_fs
from .settings import logger

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
                                # Keep the selected result available to the callback.
                                fs._last_selected_file = file_path
                                fs._last_selected_index = index
                                # Store the selected file path
                                # Get the parent directory
                                parent_dir = os.path.dirname(file_path)

                                # Navigate to the parent directory with a callback
                                def callback():
                                    # Get the full URL of the file
                                    file_url = as_url(self.file_path)
                                    logger.info(f"Placing cursor at: {file_url}")

                                    # Scroll the selected file into view.
                                    self.pane.place_cursor_at(file_url)

                                    # Then select the file
                                    #self.pane.select([file_url])

                                self.pane.set_path(
                                    as_url(parent_dir), callback=callback
                                )

                                # Return a command to open the parent directory
                                logger.info(
                                    f"Navigating to parent directory: {parent_dir}"
                                )
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
                    file_url = (
                        f"everything:///{encoded_search_term}/"
                        f"{fs._last_selected_index}"
                    )
                    logger.info(f"Attempting to place cursor at: {file_url}")
                    self.pane.place_cursor_at(file_url)
                except ValueError as e:
                    # If the file is not in the list, just ignore the error
                    logger.warning(f"Could not place cursor at {file_url}: {str(e)}")
                except Exception as e:
                    # Log any other errors but don't crash
                    logger.error(f"Error placing cursor: {str(e)}")
