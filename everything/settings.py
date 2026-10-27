import json
import logging
import os
import tempfile

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
