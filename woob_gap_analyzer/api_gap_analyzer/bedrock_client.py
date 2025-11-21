"""AWS Bedrock client for LLM-based analysis."""

import logging
import os
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)


class BedrockAnalyzer:
    """Client for sending analysis requests to AWS Bedrock."""

    def __init__(
        self,
        model_id: Optional[str] = None,
        aws_profile: Optional[str] = None,
        aws_region: Optional[str] = None,
    ):
        """Initialize Bedrock client using AWS SSO profile.

        Args:
            model_id: Bedrock model ID (default: Claude Haiku)
            aws_profile: AWS profile name (default: from AWS_PROFILE env var or 'playground-hackathon')
            aws_region: AWS region (default: from AWS_REGION env var or 'eu-west-3')

        Raises:
            ValueError: If AWS profile is not configured
        """
        self.model_id = model_id or "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
        self.aws_profile = aws_profile or os.getenv("AWS_PROFILE", "playground-hackathon")
        self.aws_region = aws_region or os.getenv("AWS_REGION", "eu-west-3")

        # Initialize Bedrock client using SSO profile
        try:
            session = boto3.Session(profile_name=self.aws_profile, region_name=self.aws_region)
            self.client = session.client("bedrock-runtime", region_name=self.aws_region)
            logger.info(
                f"Bedrock client initialized (profile: {self.aws_profile}, region: {self.aws_region})"
            )
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to initialize Bedrock client: {e}")
            raise ValueError(
                f"Failed to initialize AWS Bedrock client with profile '{self.aws_profile}'. "
                f"Make sure you have run 'aws sso login --profile sso' and have the profile configured in ~/.aws/credentials"
            ) from e

    def send_analysis_request(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 10000,
        temperature: float = 0.5,
    ) -> Dict[str, Any]:
        """Send an analysis request to Bedrock.

        Args:
            system_prompt: System prompt for the model
            user_message: User message/question
            max_tokens: Maximum tokens in response
            temperature: Model temperature (0-1)

        Returns:
            Response dictionary with model output

        Raises:
            RuntimeError: If Bedrock API call fails
        """
        try:
            logger.debug(f"Sending request to Bedrock model: {self.model_id}")

            response = self.client.converse(
                modelId=self.model_id,
                messages=[
                    {
                        "role": "user",
                        "content": [{"text": user_message}],
                    }
                ],
                system=[{"text": system_prompt}],
                inferenceConfig={
                    "maxTokens": max_tokens,
                    "temperature": temperature,
                },
            )

            logger.debug("Bedrock request completed successfully")
            return response

        except (BotoCoreError, ClientError) as e:
            logger.error(f"Bedrock API error: {e}")
            raise RuntimeError(f"Bedrock API call failed: {e}") from e

    def extract_response_text(self, response: Dict[str, Any]) -> str:
        """Extract text content from Bedrock response.

        Args:
            response: Response dictionary from Bedrock

        Returns:
            Extracted text content
        """
        try:
            content = response["output"]["message"]["content"]
            if content and len(content) > 0:
                return content[0].get("text", "")
            return ""
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Failed to extract response text: {e}")
            return ""

    def get_usage_stats(self, response: Dict[str, Any]) -> Dict[str, int]:
        """Extract token usage statistics from response.

        Args:
            response: Response dictionary from Bedrock

        Returns:
            Dictionary with input_tokens and output_tokens
        """
        try:
            usage = response.get("usage", {})
            return {
                "input_tokens": usage.get("inputTokens", 0),
                "output_tokens": usage.get("outputTokens", 0),
            }
        except (KeyError, TypeError) as e:
            logger.error(f"Failed to extract usage stats: {e}")
            return {"input_tokens": 0, "output_tokens": 0}

    def format_context_for_llm(
        self, swagger_spec: str, woob_analysis: str, comparison_data: Optional[str] = None
    ) -> str:
        """Format analysis context for LLM.

        Args:
            swagger_spec: Swagger specification content
            woob_analysis: Woob implementation analysis
            comparison_data: Optional comparison results

        Returns:
            Formatted context string
        """
        context = f"""# Analysis Context

## Swagger API Specification
```json
{swagger_spec}
```

## Woob Implementation Analysis
```
{woob_analysis}
```
"""

        if comparison_data:
            context += f"""
## Comparison Data
```
{comparison_data}
```
"""

        return context

    def analyze_gap(
        self,
        swagger_spec: str,
        woob_analysis: str,
        system_prompt: str,
        max_tokens: int = 10000,
    ) -> Dict[str, Any]:
        """Perform gap analysis between API spec and Woob implementation.

        Args:
            swagger_spec: Swagger specification content
            woob_analysis: Woob implementation analysis
            system_prompt: System prompt for analysis
            max_tokens: Maximum tokens in response

        Returns:
            Analysis results with issues and recommendations
        """
        logger.info("Starting gap analysis with Bedrock")

        # Format context
        context = self.format_context_for_llm(swagger_spec, woob_analysis)

        # Create user message
        user_message = f"""{context}

Please analyze the gap between the Swagger API specification and the Woob implementation.
Identify all discrepancies, missing fields, type mismatches, and other issues.
Provide a detailed report with specific locations and suggested fixes."""

        # Send to Bedrock
        try:
            response = self.send_analysis_request(
                system_prompt=system_prompt,
                user_message=user_message,
                max_tokens=max_tokens,
            )

            # Extract results
            analysis_text = self.extract_response_text(response)
            usage = self.get_usage_stats(response)

            result = {
                "status": "success",
                "analysis": analysis_text,
                "usage": usage,
                "model": self.model_id,
            }

            logger.info(
                f"Gap analysis complete (tokens: {usage['input_tokens']} in, {usage['output_tokens']} out)"
            )
            return result

        except RuntimeError as e:
            logger.error(f"Gap analysis failed: {e}")
            return {
                "status": "error",
                "error": str(e),
                "model": self.model_id,
            }
