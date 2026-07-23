import asyncio
import websockets
import json
import threading
import http.server
import socketserver
import urllib.request
import yt_dlp
import subprocess
import urllib.parse
import os

# --- CONFIGURATION ---
# We use environment variables so your secret token isn't exposed on the internet!
MCP_WSS_URL = os.environ.get(
    "MCP_WSS_URL", 
    "wss://api.xiaozhi.me/mcp/?token=eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOjk1MzY3MSwiYWdlbnRJZCI6MjE0MDU0NCwiZW5kcG9pbnRJZCI6ImFnZW50XzIxNDA1NDQiLCJwdXJwb3NlIjoibWNwLWVuZHBvaW50IiwiaWF0IjoxNzg0NzIyMTI0LCJleHAiOjE4MTYyNzk3MjR9.Gk3iP-rHP9YTRiaA_lCATgqj1apdpYEGTRgETpvMPk9q_sEZxXMmqaBMoGj9IdG4ulSao2xrlaC4dhL2176Nbw"
)

# Hugging Face ONLY allows port 7860
HTTP_PORT = 7860

# When hosting on Hugging Face or Render, you will set this secret to your URL
# For local testing, it defaults to your laptop IP, but we'll default it to Render here.
PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "megan-endpoint.onrender.com")

# Global variable to hold the current raw stream URL from YouTube
CURRENT_STREAM_URL = ""

# --- HTTP Proxy Server ---
class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global CURRENT_STREAM_URL
        parsed_path = urllib.parse.urlparse(self.path)
        if parsed_path.path == '/stream.pcm' and CURRENT_STREAM_URL:
            query = urllib.parse.parse_qs(parsed_path.query)
            rate = query.get('rate', ['24000'])[0]  # default to 24000 for this board
            
            self.send_response(200)
            self.send_header('Content-type', f'audio/l16;rate={rate};channels=1')
            self.send_header('Connection', 'close')
            self.end_headers()
            try:
                print(f"[*] Starting FFmpeg to transcode stream to {rate}Hz PCM...")
                cmd = [
                    'ffmpeg', '-i', CURRENT_STREAM_URL,
                    '-f', 's16le', '-acodec', 'pcm_s16le', '-ar', rate, '-ac', '1', 'pipe:1'
                ]
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                while True:
                    chunk = process.stdout.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                process.wait()
            except Exception as e:
                print("Streaming error:", e)
        elif parsed_path.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b"<html><body><h1>XiaoZhi Music MCP Server is Running!</h1></body></html>")
        else:
            self.send_response(404)
            self.end_headers()

def start_http_proxy():
    # Fix for Address already in use error on restart
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("", HTTP_PORT), ProxyHandler)
    print(f"[*] Audio Proxy running on port {HTTP_PORT}")
    print(f"[*] Accessible externally at: {PUBLIC_HOST}")
    httpd.serve_forever()


# --- YT-DLP Music Functions ---
def search_music(query):
    print(f"[*] Searching SoundCloud for: {query}")
    ydl_opts = {
        'format': 'bestaudio/best', 
        'noplaylist': True, 
        'quiet': True
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"scsearch1:{query}", download=False)
        if 'entries' in info and len(info['entries']) > 0:
            entry = info['entries'][0]
            return entry['webpage_url'], entry['title']
        return None, None

def get_stream_url(video_url):
    print(f"[*] Extracting stream URL for: {video_url}")
    ydl_opts = {
        'format': 'bestaudio/best', 
        'quiet': True
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        return info['url']


# --- MCP WebSocket Server ---
async def handle_mcp_message(websocket, message):
    try:
        req = json.loads(message)
    except Exception:
        return

    req_id = req.get("id")
    method = req.get("method")
    
    if method == "initialize":
        res = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "python-music-mcp", "version": "1.0.0"}
            }
        }
        await websocket.send(json.dumps(res))
    
    elif method == "ping":
        res = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {}
        }
        await websocket.send(json.dumps(res))
        
    elif method == "tools/list":
        res = {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "search_global_music",
                        "description": "Search for music globally. Returns the URL and title.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "The song name or artist to search for."}
                            },
                            "required": ["query"]
                        }
                    },
                    {
                        "name": "play_global_music",
                        "description": "Prepares the requested video URL for streaming and returns the local HTTP URL. You MUST then use the ESP32's 'self.audio.play_music' tool with this local URL to actually play the audio from the speaker.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "video_id": {"type": "string", "description": "The URL returned by search_global_music"}
                            },
                            "required": ["video_id"]
                        }
                    }
                ]
            }
        }
        await websocket.send(json.dumps(res))
        
    elif method == "tools/call":
        params = req.get("params", {})
        tool_name = params.get("name")
        args = params.get("arguments", {})
        
        if tool_name == "search_global_music":
            query = args.get("query")
            vid, title = search_music(query)
            if vid:
                text = f"Found song: '{title}'. URL: {vid}"
            else:
                text = "Could not find any songs matching that query."
                
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": text}]
                }
            }
            await websocket.send(json.dumps(res))
            
        elif tool_name == "play_global_music":
            global CURRENT_STREAM_URL
            video_id = args.get("video_id")
            
            try:
                CURRENT_STREAM_URL = get_stream_url(video_id)
                
                # Use http:// for local laptop testing, but https:// if deployed to Render or Hugging Face
                protocol = "https" if ("hf.space" in PUBLIC_HOST or "onrender.com" in PUBLIC_HOST) else "http"
                local_url = f"{protocol}://{PUBLIC_HOST}/stream.pcm"
                
                text = f"Stream is ready at {local_url}. Now CALL the 'self.audio.play_music' tool with this URL. CRITICAL: You MUST NOT say anything after calling the tool! Stop your response immediately. Do not ask the user any questions. This is required to save memory on the device."
            except Exception as e:
                text = f"Error extracting stream: {e}"
                
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": text}]
                }
            }
            await websocket.send(json.dumps(res))

async def mcp_worker():
    print(f"[*] Connecting to XiaoZhi MCP WebSocket...")
    async for websocket in websockets.connect(MCP_WSS_URL):
        print("[*] Connected to MCP successfully! Waiting for AI requests...")
        try:
            async for message in websocket:
                print(f"[>] Received request: {message[:100]}...")
                await handle_mcp_message(websocket, message)
        except websockets.ConnectionClosed:
            print("[!] Connection lost. Reconnecting...")
            continue
        except Exception as e:
            print(f"[!] Error: {e}")
            await asyncio.sleep(2)

def main():
    # Start the HTTP proxy server in a background thread
    threading.Thread(target=start_http_proxy, daemon=True).start()
    
    # Start the MCP WebSocket loop
    asyncio.run(mcp_worker())

if __name__ == "__main__":
    main()
