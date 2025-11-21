import os
import json
import boto3
from http.server import HTTPServer, BaseHTTPRequestHandler

from model import bedrock, model_id, configuration
from prompt import SYSTEM_PROMPT, make_final_prompt
from woob_gap_analyzer.api_gap_analyzer.context_formatter import ContextFormatter
from woob_gap_analyzer.api_gap_analyzer.explorer import ModuleExplorer


def get_woob_context(module_name: str) -> str:
    woob_analysis = ModuleExplorer().explore_module(module_name)
    woob_context = ContextFormatter.format_woob_analysis(woob_analysis)
    return woob_context


def prompt_final_model(module_name: str, prompt: str) -> str:
    conversation = [
        {
            "role": "user",
            "content": [
                {
                    "text": prompt,
                },
            ],
        }
    ]

    # Call Bedrock
    response = bedrock.converse(
        modelId=model_id,
        messages=conversation,
        system=[{"text": SYSTEM_PROMPT}],
        inferenceConfig=configuration,
    )
    return response


def get_built_in_context() -> list[tuple[str, str]]:
    topics = [
        ("API Specification", "data/swagger_clean.json"),
        ("HAR archive file", "data/bundle.anonymized.har"),
    ]
    contents = []
    for topic, filename in topics:
        try:
            with open(filename, "r") as f:
                contents.append((topic, f.read()))
        except FileNotFoundError:
            print(
                f"Warning: File {filename} not found. Server will start but may fail on requests."
            )
            contents.append("")

    return contents


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/":
            # Read the request body
            content_length = int(self.headers["Content-Length"])
            module_name = "cragr_stet"
            #module_name = self.rfile.read(content_length).decode("utf-8")

            woob_context = get_woob_context(module_name)

            #print("woob context: ")
            #print(woob_context)

            built_in_context = get_built_in_context()
            context = [*built_in_context, ("Woob module content", woob_context)]

            prompt = make_final_prompt(module_name, context)
            response = prompt_final_model(module_name, prompt)
            output = response["output"]["message"]["content"][0]["text"]

            # Send response
            self.send_response(200)
            self.send_header("Content-type", "text/plain; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            #self.wfile.write("".encode("utf-8"))
            self.wfile.write(output.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Custom log format
        print(f"[{self.log_date_time_string()}] {format % args}")


def run_server(port=9999):
    server_address = ("", port)
    httpd = HTTPServer(server_address, Handler)
    print(f"Starting PSD2 Analysis Server on port {port}...")
    print(f"Server is ready to accept requests at http://localhost:{port}/")
    print("Press Ctrl+C to stop the server")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        httpd.shutdown()


if __name__ == "__main__":
    run_server(9999)
