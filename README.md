# FMANEverything

A [fman](https://fman.io) plugin that integrates with [Everything](https://www.voidtools.com/) search to quickly find files.

## Features

- Type "everything" in fman or press Alt+E to start the Everything search
- Enter a search term in the prompt dialog (minimum 3 characters)
- View search results in a custom view
- Navigate to files or directories by clicking on them

## Requirements

- [Everything](https://www.voidtools.com/) must be installed and running
- The Everything API server must be running at `http://localhost:5000/everything-search-api/search`

## Installation

1. Install the plugin from fman's [Plugin Repository](https://fman.io/docs/plugins)
2. Make sure Everything is installed and running
3. Make sure the Everything API server is running

## Usage

1. Type "everything" in fman or press Alt+E
2. Enter a search term in the prompt dialog (minimum 3 characters)
3. The plugin will search for files and directories matching your search term
4. Results will be displayed in a custom view with columns for:
   - Name: The filename
   - Path: The directory containing the file
   - Size: The file size
   - Date Modified: When the file was last modified
5. Click on a result to navigate to it:
   - For files: You'll be navigated to the parent directory with the file selected
   - For folders: You'll be navigated directly to the folder

## Debugging

The plugin logs detailed information to a log file in your system's temporary directory:

- Windows: `%TEMP%\fman_everything.log`
- macOS/Linux: `/tmp/fman_everything.log`

This log file can be useful for troubleshooting if you encounter any issues.

## License

MIT
