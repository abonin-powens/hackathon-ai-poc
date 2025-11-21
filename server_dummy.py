import os
import json
import time
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


response = """
# PSD2 API & Woob Implementation Analysis Report
## Credit Agricole STET Payment Module (cragr_stet)

---

## Executive Summary

I have analyzed the Credit Agricole STET payment implementation (`cragr_stet/payment.py`) against the provided Swagger specification. The analysis reveals **no critical discrepancies** between the API specification and the woob implementation. The module correctly implements the PSD2 payment initiation service (PIS) endpoints.

However, I have identified several **observations and areas of note** that warrant attention, though they are not strict violations of the API specification.

---

## Detailed Analysis

### ✅ Endpoint Compliance

**Status**: COMPLIANT

The implementation correctly uses the following endpoints as specified:

| Endpoint | HTTP Method | Woob Implementation | Spec Definition | Status |
|----------|-------------|-------------------|-----------------|--------|
| `/payment-requests` | POST | `new_payment_page.go(json=payload)` | POST to create payment | ✅ Correct |
| `/payment-requests/{id}` | GET | `payment_page.go(payment_id=self.payment.id)` | GET to retrieve payment | ✅ Correct |
| `/payment-requests/{id}/confirmation` | POST | `payment_confirmation_page.go(payment_id=self.payment.id, json=payload)` | POST to confirm | ✅ Correct |

---

### ✅ Request/Response Data Mapping

**Status**: COMPLIANT

The implementation correctly maps request and response data structures:

**Request Payload Construction** (in parent `Stet141PaymentBrowser`):
- Uses `dialect.build_payload()` to construct the request
- Includes all required fields per spec:
  - `paymentInformationId` ✅
  - `creationDateTime` ✅
  - `numberOfTransactions` ✅
  - `creditTransferTransaction` (array) ✅
  - `paymentTypeInformation` ✅
  - `supplementaryData` ✅

**Response Parsing** (in `pages.py`):
- Correctly extracts payment ID from Location header ✅
- Properly parses `paymentRequest/paymentInformationStatus` ✅
- Correctly accesses nested fields like `paymentRequest/debtorAccount/iban` ✅
- Handles instruction status data from `creditTransferTransaction` array ✅

---

### ✅ HTTP Headers and Authentication

**Status**: COMPLIANT

The implementation correctly handles:
- **Accept header**: Set to `application/hal+json` (configurable via `ACCEPT_HAL_JSON`) ✅
- **Content-Type**: Automatically set to `application/json` for POST/PUT requests ✅
- **Authorization**: Bearer token added when available ✅
- **Custom headers**:
  - `X-Request-ID` (UUID) ✅
  - `Date` header in HTTP-Date format ✅
  - `correlationid` (Credit Agricole specific) ✅
  - Digest header (via DigestMixin) ✅

---

## Observations & Recommendations

### 📌 Observation #1: Credit Agricole Specific Business Logic

**Location**: `cragr_stet/payment.py`, lines 56-93

**Description**:
The `CreditAgricoleStetPaymentDialect` class implements custom logic to handle Credit Agricole's restriction on SICT (Instant Credit Transfer) payments between internal accounts. This is a **business rule implementation**, not an API compliance issue.

**What it does**:
- Detects when a payment is between Credit Agricole accounts
- Automatically switches from SICT to SCT (Standard Credit Transfer) for internal transfers
- Raises a validation error for bulk payments with mixed internal/external beneficiaries

**Assessment**: ✅ **CORRECT**
This implementation aligns with Credit Agricole's documented PSD2 API behavior and is properly handled.

---

### 📌 Observation #2: Custom Error Mapping for 403 Responses

**Location**: `cragr_stet/payment.py`, lines 98-101 and lines 116-144

**Description**:
The implementation includes custom error mapping for HTTP 403 responses specific to Credit Agricole:

```python
_response_code_403_error_mapping: dict[str, PaymentValidationErrorCode] = {
    "instant payment unauthorized": PaymentValidationErrorCode.INVALID_BENEFICIARY,
    "exceed the amount limit": PaymentValidationErrorCode.INVALID_AMOUNT,
}
```

**Assessment**: ✅ **CORRECT**
The Swagger spec does not explicitly define 403 error response bodies, so this custom handling is appropriate for Credit Agricole's specific error messages. The implementation correctly:
- Extracts the error message from response JSON
- Maps it to appropriate `PaymentValidationErrorCode` values
- Provides localized French error messages for end users

---

### 📌 Observation #3: Custom Headers for OAuth Token Request

**Location**: `cragr_stet/payment.py`, lines 104-115

**Description**:
The implementation adds three mandatory proprietary headers to the OAuth token request:
- `cats_consommateur`
- `cats_consommateurorigine`
- `cats_canal`

**Assessment**: ✅ **CORRECT**
These headers are not defined in the provided Swagger specification but are documented as mandatory by Credit Agricole's support team (per the code comment noting they're "still needed in August 2023"). This is a valid extension beyond the base STET specification.

---

### 📌 Observation #4: Region-Based URL Formatting

**Location**: `cragr_stet/payment.py`, lines 73-75 and `setup_session()` method

**Description**:
The implementation extracts the region from the website URL and uses it to format the API base URL:

```python
BASEURL = "https://psd2-api.{self.region}.fr/dsp2/v1/"
```

**Assessment**: ✅ **CORRECT**
This is a valid implementation pattern for multi-regional APIs. The Swagger spec defines the server as `/dsp2`, and this implementation correctly prepends the full domain.

---

### 📌 Observation #5: ACCEPT_HAL_JSON Configuration

**Location**: `cragr_stet/payment.py`, line 96

**Description**:
Credit Agricole sets `ACCEPT_HAL_JSON = False`, which means the Accept header is set to `application/json` instead of `application/hal+json`.

**Assessment**: ✅ **CORRECT**
The Swagger spec indicates responses should be `application/hal+json`, but the parent browser class includes a note that some ASPSPs (like LCL) reject `application/hal+json` with HTTP 406. Credit Agricole requires this workaround, which is properly implemented.

---

### 📌 Observation #6: Validation Redirect Flow Configuration

**Location**: `cragr_stet/payment.py`, line 95

**Description**:
Credit Agricole uses `VALIDATION_REDIRECT_FLOW = RedirectFlow.SIMPLE_WITHOUT_CONFIRMATION`

**Assessment**: ✅ **CORRECT**
This is a valid configuration choice for the STET standard. The spec allows for different redirect flow types, and Credit Agricole's choice to use the simple flow without confirmation is properly documented.

---

## Data Type Compliance

### ✅ Amount Handling

**Spec Definition**:
```json
"instructedAmount": {
  "type": "object",
  "required": ["amount", "currency"],
  "properties": {
    "amount": {"type": "string"},
    "currency": {"type": "string"}
  }
}
```

**Implementation**: ✅ Correctly handled by the dialect's `build_payload()` method, which constructs amounts as strings per the spec.

---

### ✅ Date/Time Handling

**Spec Definition**:
- `creationDateTime`: string (ISO 8601 format expected)
- `requestedExecutionDate`: string

**Implementation**: ✅ Correctly handled:
- `creation_date` passed as `datetime` object to dialect
- Dialect converts to ISO 8601 string format
- Parent class includes proper date formatting in headers

---

### ✅ Transaction Status Enums

**Spec Definition**:
```json
"transactionStatus": {
  "type": "string",
  "enum": ["RJCT", "PDNG", "ACSP", "ACSC"]
}
```

**Implementation**: ✅ Correctly mapped in `set_instruction_status()` method (lines 1147-1187 of parent browser)

---

## Security & Authentication

### ✅ OAuth2 Token Handling

**Assessment**: ✅ COMPLIANT

The implementation correctly:
- Requests tokens with proper `grant_type` values
- Handles token expiration and refresh
- Uses Bearer token authentication
- Supports both BASIC and POST client authentication methods

---

### ✅ Signature & Digest

**Assessment**: ✅ COMPLIANT

The implementation correctly:
- Uses DigestMixin for HTTP Digest authentication
- Sets Proxynet-Signature headers
- Includes QSEALC signature type

---

## Potential Issues & Recommendations

### ⚠️ Issue #1: Hardcoded Bank Code Mapping

**Severity**: LOW

**Location**: `cragr_stet/payment.py`, lines 24-57

**Problem**:
The `CRAGR_BANK_CODES` dictionary contains a hardcoded mapping of 42 Credit Agricole regional websites to their bank codes. If Credit Agricole adds new regional websites or changes existing ones, this mapping will become outdated.

**Current Implementation**:
```python
CRAGR_BANK_CODES = {
    "www.ca-alpesprovence.fr": "11306",
    "www.ca-alsace-vosges.fr": "17206",
    # ... 40 more entries
}
```

**Impact**:
- New regional websites won't be recognized
- The code logs a warning but continues, potentially causing payment failures
- No automatic update mechanism exists

**Suggested Fix**:
Consider implementing a fallback mechanism or fetching this mapping from a configuration file:

```python
# Option 1: Configuration-based approach
CRAGR_BANK_CODES = self.config.get("bank_codes_mapping", {}).get()

# Option 2: API-based approach (if Credit Agricole provides an endpoint)
def get_bank_code_for_website(self, website: str) -> str | None:
    # Try cache first
    if website in self.CRAGR_BANK_CODES:
        return self.CRAGR_BANK_CODES[website]
    # Could fetch from API or external source
    return None
```

---

### ⚠️ Issue #2: Error Message Matching is Case-Sensitive

**Severity**: MEDIUM

**Location**: `cragr_stet/payment.py`, lines 116-144

**Problem**:
Error message matching uses `.casefold()` to normalize case, but the mapping keys are lowercase. However, if Credit Agricole changes the exact wording of error messages (e.g., adds punctuation or changes spacing), the matching will fail silently.

**Current Implementation**:
```python
msg = response.json().get("message", "").casefold()
if "exceed the amount limit" in msg:
    # This works, but is fragile
```

**Impact**:
- If error message format changes, errors won't be properly categorized
- Users will receive generic error codes instead of specific ones
- Difficult to debug without seeing actual API responses

**Suggested Fix**:
Implement more robust error matching:

```python
def extract_error_code(self, message: str) -> PaymentValidationErrorCode | None:
    \"\"\"Extract error code from message with fuzzy matching.\"\"\"
    message_lower = message.casefold()

    # Use a mapping with multiple possible variations
    error_patterns = {
        PaymentValidationErrorCode.INVALID_AMOUNT: [
            "exceed the amount limit",
            "amount limit exceeded",
            "exceeds maximum amount",
        ],
        PaymentValidationErrorCode.INVALID_BENEFICIARY: [
            "instant payment unauthorized",
            "invalid data creditor iban",
            "creditor iban invalid",
        ],
    }

    for code, patterns in error_patterns.items():
        if any(pattern in message_lower for pattern in patterns):
            return code

    return None
```

---

### ⚠️ Issue #3: Region Extraction Logic Could Fail

**Severity**: LOW

**Location**: `cragr_stet/payment.py`, lines 73-75 and `setup_session()` method

**Problem**:
The region extraction assumes a specific website format:

```python
self.region = website.replace("www.", "").replace(".fr", "")
```

This logic is fragile:
- If a website doesn't follow the `www.ca-*.fr` pattern, it will produce incorrect results
- Non-French regional websites would break this logic
- The extracted region is then used directly in a URL without validation

**Example Failure Cases**:
- `ca-alpesprovence.fr` (no www) → region = `ca-alpesprovence` ✓ (works)
- `www.ca-alpesprovence.com` (non-.fr) → region = `www.ca-alpesprovence` ✗ (fails)
- `www.credit-agricole.fr` (different format) → region = `credit-agricole` ✗ (fails)

**Impact**:
- API calls will fail with 404 or invalid URL errors
- Difficult to diagnose without proper error logging

**Suggested Fix**:
```python
def setup_session(self):
    \"\"\"Set up the initialized session.\"\"\"
    website = self.config["website"].get()

    # Validate website format
    if not website.startswith("www.ca-") or not website.endswith(".fr"):
        raise ValueError(
            f"Unexpected website format: {website}. "
            "Expected format: www.ca-*.fr"
        )

    # Extract region safely
    self.region = website.replace("www.ca-", "").replace(".fr", "")

    # Validate that we have a known region
    if website not in CRAGR_BANK_CODES:
        self.logger.warning(
            "Unknown Credit Agricole website: %s. "
            "Payment may fail if bank code is needed.",
            website,
        )
```

---

## Summary Table

| Category | Status | Notes |
|----------|--------|-------|
| **Endpoint Paths** | ✅ COMPLIANT | All endpoints match spec |
| **HTTP Methods** | ✅ COMPLIANT | POST/GET/PUT used correctly |
| **Request Parameters** | ✅ COMPLIANT | All required fields included |
| **Response Parsing** | ✅ COMPLIANT | Field names match spec |
| **Data Types** | ✅ COMPLIANT | Strings, objects, arrays handled correctly |
| **Nested Objects** | ✅ COMPLIANT | Proper path traversal (e.g., `paymentRequest/debtorAccount/iban`) |
| **Authentication** | ✅ COMPLIANT | OAuth2 and Digest properly implemented |
| **Error Handling** | ✅ MOSTLY COMPLIANT | Custom error mapping is appropriate but fragile |
| **Business Logic** | ✅ CORRECT | SICT→SCT conversion properly implemented |

---

## Conclusion

The Credit Agricole STET payment implementation is **well-implemented and compliant** with the PSD2 API specification. The code correctly:

1. ✅ Calls the right endpoints with correct HTTP methods
2. ✅ Constructs requests with all required fields
3. ✅ Parses responses with correct field names and data types
4. ✅ Handles authentication and security properly
5. ✅ Implements Credit Agricole-specific business rules correctly

The three low-to-medium severity observations identified are **not specification violations** but rather areas where the implementation could be made more robust to handle edge cases and future changes.

**Recommendation**: No immediate action required. The implementation is production-ready. Consider addressing the three observations in future maintenance cycles to improve robustness.
"""


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
                #conversation = [
                #    {
                #        "role": "user",
                #        "content": [
                #            {
                #                "text": f"""
                #                Hello there !
                #                """
                #            },
                #        ],
                #    }
                #]

                ## Call Bedrock
                #response = bedrock.converse(
                #    modelId=model_id,
                #    messages=conversation,
                #    system=[{"text": system_prompt}],
                #    inferenceConfig=configuration,
                #)

                # Extract the response text
                #output = response["output"]["message"]["content"][0]["text"]

                # Send response
                time.sleep(3)
                self.send_response(200)
                self.send_header('Content-type', 'text/plain; charset=utf-8')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(response.encode('utf-8'))

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
            self.send_header('Access-Control-Allow-Origin', '*')
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
