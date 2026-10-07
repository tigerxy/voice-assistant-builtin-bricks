"""Offline voice assistant for the Arduino VENTUNO Q, built from Arduino's built-in bricks.

Settings (including the language) are in config.py, all texts in languages/,
the assistant itself in assistant/ (see assistant/__init__.py for an overview).
"""

from arduino.app_utils import App

from assistant.setup import build_assistant
from config import SETTINGS


def main():
    conversation = build_assistant(SETTINGS)

    try:
        App.run(user_loop=conversation.step)
    except KeyboardInterrupt:
        print("\nStopping application...")
    finally:
        conversation.shutdown()


if __name__ == "__main__":
    main()
