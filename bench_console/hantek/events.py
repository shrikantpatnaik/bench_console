"""Socket.IO namespace keeps Hantek traffic out of the OWON dashboard."""
from flask import current_app
from flask_socketio import emit


def register_events(socketio):
    def controller():
        return current_app.extensions['hantek_controller']

    @socketio.on('connect', namespace='/hantek')
    def connected():
        c = controller()
        emit('state', c.state())
        with c.lock:
            frame = c.last_frame
        if frame: emit('frame', frame)

    def operation(action, data=None):
        c = controller()
        def work():
            try:
                if action == 'scan':
                    devices = c.discover((data or {}).get('mode', 'scope'))
                    socketio.emit('devices', devices, namespace='/hantek')
                elif action == 'connect': c.connect(data or {})
                elif action == 'disconnect': c.disconnect()
                elif action == 'settings': c.configure(data or {})
                elif action == 'run': c.run()
                elif action == 'single': c.run(single=True)
                elif action == 'stop': c.stop_capture()
                elif action == 'autoset': c.autoset()
            except Exception as exc:
                c.app.logger.warning('Hantek %s failed: %s', action, exc)
                with c.lock:
                    c.error = str(exc)
                    if c.status == 'connecting': c.status = 'disconnected'
                c.publish()
                socketio.emit('operation_error', str(exc), namespace='/hantek')
        socketio.start_background_task(work)
        return {'accepted': True}

    for name in ('scan', 'connect', 'disconnect', 'settings', 'run', 'single', 'stop', 'autoset'):
        # Bind the name immediately; event handlers execute later.
        socketio.on_event('hantek_' + name,
                         lambda data=None, action=name: operation(action, data), namespace='/hantek')
