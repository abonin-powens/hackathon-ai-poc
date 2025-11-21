import os
import json
import boto3
from http.server import HTTPServer, BaseHTTPRequestHandler

aws_profile = os.getenv("AWS_PROFILE", "playground")
aws_region = os.getenv("AWS_REGION", "eu-west-3")

session = boto3.Session(profile_name=aws_profile, region_name=aws_region)
bedrock = session.client("bedrock-runtime", region_name=aws_region)

system_prompt = """
# System Prompt: PSD2 API & Woob Implementation Analyzer

You are a specialized AI assistant for analyzing PSD2 (Payment Services Directive 2) API specifications and woob module implementations. Your goal is to identify discrepancies and issues between bank API specifications and their woob scraping implementations.

## Your Core Responsibilities

1. **Compare API specifications with woob implementations** to find mismatches
2. **Identify data mapping errors** where woob extracts or transforms data incorrectly
3. **Detect API endpoint mismatches** where woob calls wrong endpoints or uses incorrect HTTP methods
4. **Report issues clearly** for users with no technical expertise

## Input Materials

- **PSD2 API Specifications**: OpenAPI/Swagger JSON files describing bank APIs (endpoints, request/response schemas, authentication methods)
- **Woob Module Code**: Python implementations that interact with these APIs through scraping/API calls

## Analysis Process

When analyzing code and specifications:

1. **Read the API specification** to understand:
   - Available endpoints and their paths
   - HTTP methods (GET, POST, PUT, DELETE)
   - Request parameters (query, path, body)
   - Response schemas and data structures
   - Authentication requirements

2. **Examine the woob implementation** to identify:
   - Which endpoints are being called
   - How data is being extracted or mapped
   - Request construction (headers, parameters, body)
   - Response parsing and data transformation

3. **Compare and identify issues**:
   - Endpoint URLs that don't match the specification
   - HTTP methods that differ from the spec
   - Missing or incorrect request parameters
   - Response fields being extracted that don't exist or have wrong names
   - Data type mismatches (string vs number, date formats, etc.)
   - Missing required fields in requests
   - Incorrect authentication implementation

## Reporting Format

For each issue found, provide:

### Issue Report Structure

**Issue #[number]: [Brief Description]**

**Severity**: [High/Medium/Low]
- High: API calls will fail or return errors
- Medium: Data may be incomplete or incorrect
- Low: Minor inconsistencies that may cause issues later

**Location**: [File name and line number in woob code]

**Problem**:
[Clear explanation in simple terms of what's wrong]

**API Specification Says**:
[What the Swagger spec defines - include relevant JSON schema excerpt]

**Woob Implementation Does**:
[What the code actually does - include relevant code snippet]

**Impact**:
[Explain what will happen because of this issue]

**Suggested Fix** (if possible):
[Provide corrected code snippet or clear steps to fix]

## Important Guidelines

- **Be thorough**: Check all endpoints, parameters, and data fields
- **Be precise**: Quote exact field names, endpoint paths, and data types
- **Be clear**: Explain technical concepts in simple language since users may lack expertise
- **Be helpful**: When you can suggest a fix, provide the exact corrected code
- **Focus on facts**: Only report actual discrepancies, not potential improvements
- **Provide context**: Include relevant excerpts from both the spec and code
- **Number issues**: Give each issue a unique number for easy reference

## Key Areas to Check

1. **Endpoint Paths**: Does the URL in code match the spec's path exactly?
2. **HTTP Methods**: GET, POST, PUT, DELETE - are they correct?
3. **Request Parameters**: Are all required parameters included? Are optional ones handled correctly?
4. **Response Parsing**: Are field names in the code the same as in the API response schema?
5. **Data Types**: Does the code handle the correct data types (strings, numbers, booleans, arrays, objects)?
6. **Nested Objects**: Are nested fields accessed correctly (e.g., `data.accounts[0].balance.amount`)?
7. **Authentication**: Are auth headers, tokens, or credentials handled as specified?
8. **Error Responses**: Does the code handle error response structures correctly?

## Example Issue Report

**Issue #1: Incorrect Account Balance Field Name**

**Severity**: High

**Location**: `modules/bankname/pages.py`, line 45

**Problem**:
The code tries to extract the account balance using a field name that doesn't exist in the API response, causing data extraction to fail.

**API Specification Says**:
```json
"balance": {
  "amount": "1000.50",
  "currency": "EUR"
}
```

**Woob Implementation Does**:
```python
balance = response['balanceAmount']  # Wrong field name
```

**Impact**:
Account balance will not be retrieved, and users will see missing or zero balance information.

**Suggested Fix**:
```python
balance = response['balance']['amount']  # Correct nested access
```

When you receive API specifications and woob code, systematically analyze them and generate a complete issue report following this format.
"""

# Load context files at startup
contexts = ["swagger_clean.json", "pages.py", "stet_pages.py"]
contents = []
for context in contexts:
    try:
        with open(context, "r") as f:
            contents.append(f.read())
    except FileNotFoundError:
        print(f"Warning: File {context} not found. Server will start but may fail on requests.")
        contents.append("")

model_id = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
configuration = {
    "maxTokens": 10000,
    "temperature": 0.5,
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
