#!/usr/bin/env python3
"""Compare Woob implementation against Bank API specification.

This script analyzes the gap between what a Bank API (PSD2 Swagger specification)
theoretically provides and what the Woob scraping module actually extracts.

Usage:
    # Auto-detect swagger.json from modules/{module}/api-spec/
    python woob/dev_tools/compare_scraping.py --module cragr_stet

    # Specify custom swagger path
    python woob/dev_tools/compare_scraping.py \\
        --module cragr_stet \\
        --swagger path/to/swagger.json

    # Specify custom output path
    python woob/dev_tools/compare_scraping.py \\
        --module cragr_stet \\
        --output custom_report.md

    # Verbose output
    python woob/dev_tools/compare_scraping.py --module cragr_stet -v

Report Location:
    By default, reports are saved to: modules/{module}/api-spec/gap_analysis_{module}.md
    Swagger specs should be placed at: modules/{module}/api-spec/swagger.json
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .api_gap_analyzer.bedrock_client import BedrockAnalyzer
from .api_gap_analyzer.context_formatter import ContextFormatter
from .api_gap_analyzer.explorer import ModuleExplorer
from .api_gap_analyzer.report_generator import ReportGenerator
from .api_gap_analyzer.swagger_parser import SwaggerParser
from .api_gap_analyzer.system_prompt import get_system_prompt


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def resolve_swagger_path(module_name: str, swagger_arg: str = None) -> Path:
    """Resolve the Swagger spec path.

    First tries the provided argument, then looks for api-spec/swagger.json in the module folder.

    Args:
        module_name: Module name (e.g., 'cragr_stet')
        swagger_arg: Optional swagger path argument

    Returns:
        Path to Swagger spec

    Raises:
        FileNotFoundError: If Swagger spec cannot be found
    """
    # If swagger argument provided, use it
    if swagger_arg:
        swagger_path = Path(swagger_arg)
        if swagger_path.exists():
            return swagger_path
        raise FileNotFoundError(f"Swagger spec not found: {swagger_arg}")

    # Try to find api-spec/swagger.json in the module folder
    module_spec_path = Path("..") / ".." / "modules" / module_name / "api-spec" / "swagger.json"
    if module_spec_path.exists():
        return module_spec_path

    # Try the old location (module root)
    old_spec_path = Path("..") / ".." / "modules" / module_name / "Swagger-DSP2-v1.21.json"
    if old_spec_path.exists():
        return old_spec_path

    raise FileNotFoundError(
        f"Swagger spec not found. Tried:\n"
        f"  - {module_spec_path}\n"
        f"  - {old_spec_path}\n"
        f"Please provide --swagger argument or place swagger.json in modules/{module_name}/api-spec/"
    )


def validate_arguments(args: argparse.Namespace) -> None:
    """Validate command-line arguments.

    Args:
        args: Parsed arguments

    Raises:
        ValueError: If arguments are invalid
    """
    # Swagger path is now optional - will be auto-detected
    if args.swagger:
        swagger_path = Path(args.swagger)
        if not swagger_path.exists():
            raise FileNotFoundError(f"Swagger spec not found: {args.swagger}")
        if not swagger_path.suffix == ".json":
            raise ValueError(f"Swagger spec must be JSON file: {args.swagger}")


def load_swagger_spec(swagger_path: str) -> tuple[SwaggerParser, str]:
    """Load and parse Swagger specification.

    Args:
        swagger_path: Path to Swagger JSON file

    Returns:
        Tuple of (SwaggerParser instance, raw JSON content)

    Raises:
        FileNotFoundError: If file doesn't exist
        json.JSONDecodeError: If JSON is invalid
    """
    logger = logging.getLogger(__name__)
    logger.info(f"Loading Swagger spec from: {swagger_path}")

    try:
        parser = SwaggerParser(swagger_path)
        with open(swagger_path, "r", encoding="utf-8") as f:
            content = f.read()
        logger.info("Swagger spec loaded successfully")
        return parser, content
    except FileNotFoundError as e:
        logger.error(f"Swagger spec not found: {e}")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in Swagger spec: {e}")
        raise


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable format.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted duration string
    """
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}m"
    else:
        hours = seconds / 3600
        return f"{hours:.1f}h"


def main() -> int:
    """Main entry point.

    Returns:
        Exit code (0 for success, 1 for error)
    """
    start_time = time.time()
    parser = argparse.ArgumentParser(
        description="Compare Woob implementation against Bank API specification",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-detect swagger.json from modules/{module}/api-spec/
  python compare_scraping.py --module cragr_stet

  # Specify custom swagger path
  python compare_scraping.py --module cragr_stet --swagger path/to/swagger.json

  # Specify custom output path
  python compare_scraping.py --module cragr_stet --output custom_report.md

  # Verbose output
  python compare_scraping.py --module cragr_stet -v

Report Location:
  Default: modules/{module}/api-spec/gap_analysis_{module}.md
  Swagger: modules/{module}/api-spec/swagger.json
        """,
    )

    parser.add_argument(
        "--module",
        required=True,
        help="Module name to analyze (e.g., cragr_stet)",
    )
    parser.add_argument(
        "--capability",
        default="Account",
        help="Capability type to analyze (default: Account)",
    )
    parser.add_argument(
        "--swagger",
        help="Path to Swagger/OpenAPI specification JSON file (optional, auto-detected from modules/{module}/api-spec/swagger.json)",
    )
    parser.add_argument(
        "--output",
        help="Output file path for markdown report (default: modules/{module}/api-spec/gap_analysis_{module}.md)",
    )
    parser.add_argument(
        "--model",
        help="Bedrock model ID (optional, uses default if not specified)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    try:
        # Validate arguments
        validate_arguments(args)

        # Resolve swagger path (auto-detect if not provided)
        swagger_path = resolve_swagger_path(args.module, args.swagger)
        logger.info(f"Using Swagger spec: {swagger_path}")

        # Step 1: Load Swagger spec
        step1_start = time.time()
        logger.info("Step 1: Loading Swagger specification...")
        swagger_parser, swagger_content = load_swagger_spec(str(swagger_path))
        ais_endpoints = swagger_parser.get_ais_endpoints()
        logger.info(f"Found {len(ais_endpoints)} AIS endpoints")
        step1_time = time.time() - step1_start

        # Step 2: Explore Woob module
        step2_start = time.time()
        logger.info(f"Step 2: Exploring Woob module '{args.module}'...")
        # Initialize explorer with correct root path (two levels up from dev_tools)
        explorer = ModuleExplorer(woob_root="../..")
        woob_analysis = explorer.explore_module(args.module)
        logger.info(
            f"Found {len(woob_analysis['extracted_fields'])} extracted fields "
            f"({len(woob_analysis['parent_analysis'])} parent classes)"
        )
        step2_time = time.time() - step2_start

        # Step 3: Format context for LLM
        step3_start = time.time()
        logger.info("Step 3: Formatting analysis context...")
        context = ContextFormatter.format_comparison_context(swagger_content, woob_analysis)
        logger.debug(f"Context size: {len(context)} characters")
        step3_time = time.time() - step3_start

        # Step 4: Send to Bedrock for analysis
        step4_start = time.time()
        logger.info("Step 4: Sending analysis to AWS Bedrock...")
        bedrock = BedrockAnalyzer(model_id=args.model)
        system_prompt = get_system_prompt()

        analysis_result = bedrock.analyze_gap(
            swagger_spec=swagger_content,
            woob_analysis=context,
            system_prompt=system_prompt,
        )

        if analysis_result["status"] != "success":
            logger.error(
                f"Bedrock analysis failed: {analysis_result.get('error', 'Unknown error')}"
            )
            return 1

        bedrock_response = analysis_result["analysis"]
        usage = analysis_result["usage"]
        logger.info(
            f"Analysis complete (tokens: {usage['input_tokens']} in, {usage['output_tokens']} out)"
        )
        step4_time = time.time() - step4_start

        # Step 5: Generate report
        step5_start = time.time()
        logger.info("Step 5: Generating markdown report...")
        report = ReportGenerator.format_report_with_summary(
            bedrock_response,
            args.module,
            "Bank API",
        )
        step5_time = time.time() - step5_start

        # Step 6: Output report
        step6_start = time.time()
        if args.output:
            output_path = Path(args.output)
        else:
            # Default: save to modules/{module}/api-spec/gap_analysis_{module}.md
            module_path = Path("..") / ".." / "modules" / args.module / "api-spec"
            output_path = module_path / f"gap_analysis_{args.module}.md"
            # Create directory if it doesn't exist
            module_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"Saving report to: {output_path}")
        ReportGenerator.save_report(report, str(output_path))
        step6_time = time.time() - step6_start

        # Print timing summary
        total_time = time.time() - start_time
        print("\n" + "=" * 70)
        print("EXECUTION TIME SUMMARY")
        print("=" * 70)
        print(f"Step 1 - Load Swagger spec:        {format_duration(step1_time)}")
        print(f"Step 2 - Explore Woob module:      {format_duration(step2_time)}")
        print(f"Step 3 - Format context:           {format_duration(step3_time)}")
        print(f"Step 4 - Bedrock analysis:         {format_duration(step4_time)}")
        print(f"Step 5 - Generate report:          {format_duration(step5_time)}")
        print(f"Step 6 - Save report:              {format_duration(step6_time)}")
        print("-" * 70)
        print(f"TOTAL TIME:                        {format_duration(total_time)}")
        print("=" * 70 + "\n")

        logger.info("Analysis complete!")
        return 0

    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        logger.error(f"Error: {e}")
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
