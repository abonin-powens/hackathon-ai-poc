import os
import json
import boto3
from http.server import HTTPServer, BaseHTTPRequestHandler

from prompt import SYSTEM_PROMPT

aws_profile = os.getenv("AWS_PROFILE", "playground")
aws_region = os.getenv("AWS_REGION", "eu-west-3")

session = boto3.Session(profile_name=aws_profile, region_name=aws_region)
bedrock = session.client("bedrock-runtime", region_name=aws_region)

# Load context files at startup
contexts = ["data/swagger_clean.json", "data/pages.py", "data/stet_pages.py", "data/bundle.anonymized.har"]
contents = []
for context in contexts:
    try:
        with open(context, "r") as f:
            contents.append(f.read())
    except FileNotFoundError:
        print(f"Warning: File {context} not found. Server will start but may fail on requests.")
        contents.append("")

# model_id = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
model_id = "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
# model_id = "mistral.mistral-large-2402-v1:0" <- context trop grand
configuration = {
    #"maxTokens": 10000,
    #"maxTokens": 10000,
    #"temperature": 0,
    "topP": 0.9,              # Nucleus sampling, réduit les tokens improbables
    #"topK": 40,               # Limite aux 40 meilleurs tokens (si disponible)
    "maxTokens": 10000,        # Limite la longueur pour garder le focus
}


class BedrockHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/":
            try:
                # Read the request body
                #content_length = int(self.headers['Content-Length'])
                #body = self.rfile.read(content_length).decode('utf-8')

                # Parse the user prompt from the body
                #user_prompt = body

                print("Received request, processing...")

                # Construct the conversation with context files
                conversation = [
                    {
                        "role": "user",
                        "content": [
                            {
                                "text": f"""
                                Please analyse the implementation of cragr_stet and compare it to the swagger.

                                Here's the content of the cragr_stet/pages.py file: ```{contents[1]}```

                                Here's the content of the parent class stet/pages.py file: ```{contents[2]}```

                                Here's the content of the swagger file: ```{contents[0]}```

                                Here's the content of the HAR file (session folder): ```{contents[3]}```
                                """
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

                # Extract the response text
                output = response["output"]["message"]["content"][0]["text"]

                # Send response
                self.send_response(200)
                self.send_header('Content-type', 'text/plain; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(output.encode('utf-8'))

            except Exception as e:
                # Handle errors
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                error_response = json.dumps({"error": str(e)})
                self.wfile.write(error_response.encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"PSD2 API & Woob Implementation Analyzer Server\n\nPOST your analysis prompt to / to get results.")
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Custom log format
        print(f"[{self.log_date_time_string()}] {format % args}")


def run_server(port=9999):
    server_address = ('', port)
    httpd = HTTPServer(server_address, BedrockHandler)
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
