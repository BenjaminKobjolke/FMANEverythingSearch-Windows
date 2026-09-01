# FMANEverything

A [fman](https://fman.io) plugin that integrates with [Everything](https://www.voidtools.com/) search to quickly find files.

## Features

- Type "everything" in fman or press Alt+E to start the Everything search
- Enter a search term in the prompt dialog (minimum 3 characters)
- The prompt remembers your last search term and preselects it, so Enter repeats
  the search and typing replaces it
- View search results in a custom view
- Navigate to files or directories by clicking on them

## Requirements

- [Everything](https://www.voidtools.com/) must be installed and running

## Installation

1. Install the plugin from fman's [Plugin Repository](https://fman.io/docs/plugins)
2. Make sure Everything is installed and running

**(Optional) Using the API Server:**

If you prefer to use the Everything API server instead of the bundled DLL for searching, you can install it from [https://github.com/BenjaminKobjolke/everything-api](https://github.com/BenjaminKobjolke/everything-api). Ensure the API server is running before using the plugin. You may need to configure the plugin settings to point to the API endpoint.

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

The prompt is prefilled with the term you entered last and it is fully selected,
so pressing Enter runs it again, and typing (or Del) replaces it. This means a
search dismissed by an error dialog is not lost — fix the cause, press Alt+E,
Enter. The term is kept for the current fman session only.

## When a search fails

The search runs before the pane navigates, so a problem is reported as a **dialog**
and you stay where you are — the pane is never replaced by an empty list.

The same messages also appear as a row in the pane (**Name** = what is wrong,
**Path** = what to do about it) if you navigate back into a search result whose
results are no longer cached, where a dialog cannot safely be shown.

| Message | Meaning |
|---|---|
| `Everything is not running, or fman cannot reach it.` | Everything answered nothing at all. Either it is not open, or it runs **as administrator** while fman does not. The two are indistinguishable through the SDK, so the message covers both. |
| `Everything has no index loaded.` | Everything is reachable but has no database. Either it is still indexing, or the **Everything service is stopped** — with the service down the index never loads, so waiting will not help. Start it from an admin prompt with `sc start Everything`. Note that a search issued in this state never returns, so the plugin refuses it up front rather than hanging. |
| `Everything is running but fman cannot reach it.` | A call into Everything hung and was abandoned. Same causes as above; Everything search stays disabled until fman is restarted. |
| `Everything DLL not found.` / `Wrong Everything DLL bitness.` | Fix `dll_path` in `settings.json`. fman is 64-bit, so it needs `Everything64.dll`. |
| `No files matching '…'.` | The search worked; nothing matched. Not an error. |

If the DLL is unusable the plugin still tries the API server, and says so in the
Path column rather than reporting a bare connection error.

Once an IPC call has hung, Everything stays unreachable for the rest of the fman
session — fix the cause and restart fman.

## Debugging

The plugin logs to a file in your system's temporary directory:

- Windows: `%TEMP%\fman_everything.log`
- macOS/Linux: `/tmp/fman_everything.log`

To check the failure handling without running fman:

```
python check_everything.py
```

## License

MIT
