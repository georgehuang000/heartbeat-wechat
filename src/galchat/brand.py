"""Plain application names; visual brand artwork is kept separately."""
APP_NAME = '心动微信'
LEGACY_APP_NAME = '♥动微信'


def find_desktop_window(gui):
    return gui.FindWindow(None, APP_NAME) or gui.FindWindow(None, LEGACY_APP_NAME)
