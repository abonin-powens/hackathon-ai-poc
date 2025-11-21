import os
import json
import boto3
from http.server import HTTPServer, BaseHTTPRequestHandler

from prompt import SYSTEM_PROMPT

aws_profile = os.getenv("AWS_PROFILE", "playground")
aws_region = os.getenv("AWS_REGION", "eu-west-3")

session = boto3.Session(profile_name=aws_profile, region_name=aws_region)
bedrock = session.client("bedrock-runtime", region_name=aws_region)

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
