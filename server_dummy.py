import os
import json
import boto3
from http.server import HTTPServer, BaseHTTPRequestHandler

aws_profile = os.getenv("AWS_PROFILE", "playground")
aws_region = os.getenv("AWS_REGION", "eu-west-3")

session = boto3.Session(profile_name=aws_profile, region_name=aws_region)
bedrock = session.client("bedrock-runtime", region_name=aws_region)

system_prompt = """
You are a cynical assistant. Just say hello coldly.
"""

model_id = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
configuration = {
    "maxTokens": 1000,
    "temperature": 1,
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
                                Hello there !
                                """
                            },
                        ],
                    }
                ]

                # Call Bedrock
                response = bedrock.converse(
                    modelId=model_id,
                    messages=conversation,
                    system=[{"text": system_prompt}],
                    inferenceConfig=configuration,
                )

                # Extract the response text
                output = response["output"]["message"]["content"][0]["text"]

                # Send response
                self.send_response(200)
                self.send_header('Content-type', 'text/plain; charset=utf-8')
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
