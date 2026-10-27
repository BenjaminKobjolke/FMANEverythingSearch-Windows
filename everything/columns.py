import os
from datetime import datetime
from fman.fs import Column
from fman.url import basename, dirname, splitscheme

from .core import _is_placeholder
from .filesystem import EverythingFS, get_everything_fs
from .settings import logger

class Name(Column):
    def get_str(self, url):
        scheme, path = splitscheme(url)
        logger.debug(
            f"Name.get_str called with url: '{url}', scheme: '{scheme}', path: '{path}'"
        )

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
                    logger.error(
                        f"Name.get_str: Failed to extract index from path: '{path}'"
                    )
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
        logger.debug(
            f"Path.get_str called with url: '{url}', scheme: '{scheme}', path: '{path}'"
        )

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
                    logger.error(
                        f"Path.get_str: Failed to extract index from path: '{path}'"
                    )
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
        logger.debug(
            f"Size.get_str called with url: '{url}', scheme: '{scheme}', path: '{path}'"
        )

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
                    logger.error(
                        f"Size.get_str: Failed to extract index from path: '{path}'"
                    )
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
        logger.debug(
            "DateModified.get_str called with url: '%s', scheme: '%s', path: '%s'",
            url, scheme, path,
        )

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
                logger.debug(
                    f"DateModified.get_str: Simple index extraction: '{index}'"
                )
            elif '/' in path:
                # More general extraction
                parts = path.split('/')
                # The last non-empty part should be the index
                index_str = next((p for p in reversed(parts) if p), None)
                if index_str and index_str.isdigit():
                    index = int(index_str)
                    logger.debug(
                        f"DateModified.get_str: Complex index extraction: '{index}'"
                    )
                else:
                    logger.error(
                        "DateModified.get_str: Failed to extract index from path: "
                        f"'{path}'"
                    )
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
                        # Parse the ISO date with strptime for compatibility.
                        date_str = item['date_modified']
                        # Remove the 'Z' or timezone info for parsing
                        if 'Z' in date_str:
                            date_str = date_str.replace('Z', '')
                        elif '+' in date_str:
                            date_str = date_str.split('+')[0]
                        elif '-' in date_str and date_str.count('-') > 2:
                            # More than the date separators.
                            date_str = date_str.rsplit('-', 1)[0]

                        # Parse the date
                        date = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%S')
                        result = date.strftime("%Y-%m-%d %H:%M:%S")
                        logger.debug(
                            f"DateModified.get_str: returning date: '{result}'"
                        )
                        return result
                    except Exception as e:
                        logger.error(
                            f"DateModified.get_str: Error parsing date: {str(e)}"
                        )
                        # Return the raw date string as fallback
                        return item['date_modified']
            except (ValueError, IndexError, KeyError) as e:
                logger.error(f"DateModified.get_str: Error getting date: {str(e)}")

        logger.debug("DateModified.get_str: returning empty string")
        return ""
