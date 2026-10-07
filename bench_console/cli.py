"""Local Socket.IO runner. Launch with: python main.py"""

import atexit

from . import create_app


def main():
    app = create_app()
    controller = app.extensions["bench_controller"]
    hantek = app.extensions["hantek_controller"]
    socketio = app.extensions["socketio"]
    atexit.register(controller.shutdown)
    atexit.register(hantek.shutdown)
    try:
        socketio.run(
            app,
            host=app.config["HOST"],
            port=app.config["PORT"],
            debug=False,
            use_reloader=False,
            allow_unsafe_werkzeug=True,
        )
    finally:
        hantek.shutdown()
        controller.shutdown()
        atexit.unregister(hantek.shutdown)
        atexit.unregister(controller.shutdown)


if __name__ == "__main__":
    main()
