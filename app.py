"""Web entry point:  python app.py [--host 127.0.0.1] [--port 5000] [--seed 42]"""
import argparse

from web import create_app


def main():
    ap = argparse.ArgumentParser(description="Distributed Blockchain Network Simulator - web server")
    ap.add_argument("-H", "--host", default="127.0.0.1", help="interface to bind (default: %(default)s)")
    ap.add_argument("-p", "--port", type=int, default=5000, help="port (default: %(default)s)")
    ap.add_argument("-s", "--seed", type=int, default=None, help="override the seed from config.json")
    ap.add_argument("-c", "--config", default=None, help="path to a config JSON file")
    args = ap.parse_args()
    app, socketio = create_app(args.config, args.seed)
    print(f"Simulator running at http://{args.host}:{args.port}")
    socketio.run(app, host=args.host, port=args.port, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
