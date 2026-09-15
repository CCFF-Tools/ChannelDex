"""PyInstaller entrypoint for the ChannelDex macOS application bundle."""
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pubtv.config.settings")

from pubtv.config.launcher import main


if __name__ == "__main__":
    main()
