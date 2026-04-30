#!/usr/bin/env python3
import http.server, socketserver, os

PORT = 8766
os.chdir(os.path.dirname(os.path.abspath(__file__)))

class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # quiet

with socketserver.TCPServer(('', PORT), Handler) as httpd:
    print(f'Air Drummer server running at http://localhost:{PORT}/game.html')
    httpd.serve_forever()
