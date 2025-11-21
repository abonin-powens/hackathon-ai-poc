#!/usr/bin/env python3
"""Test script to verify the woob_gap_analyzer works with the ../woob directory."""

import sys
from pathlib import Path

# Add woob_gap_analyzer to path
sys.path.insert(0, str(Path(__file__).parent / "woob_gap_analyzer"))

from api_gap_analyzer.explorer import ModuleExplorer
from api_gap_analyzer.context_formatter import ContextFormatter

def main():
    print("="*70)
    print("Testing Woob Gap Analyzer")
    print("="*70)
    
    # Initialize explorer (should auto-detect ../woob)
    print("\n1. Initializing ModuleExplorer...")
    try:
        explorer = ModuleExplorer()
        print(f"   ✓ Explorer initialized")
        print(f"   Woob root: {explorer.woob_root}")
        print(f"   Woob root exists: {explorer.woob_root.exists()}")
    except Exception as e:
        print(f"   ✗ Failed to initialize explorer: {e}")
        return 1
    
    # Test analyzing cragr_stet module
    print("\n2. Analyzing cragr_stet module...")
    try:
        module_name = "cragr_stet"
        analysis = explorer.explore_module(module_name)
        print(f"   ✓ Module analyzed successfully")
        print(f"   Main file: {analysis['main_file']}")
        print(f"   Extracted fields: {len(analysis['extracted_fields'])}")
        print(f"   Parent classes: {len(analysis['parent_analysis'])}")
        
        # Show some extracted fields
        print("\n   Sample extracted fields:")
        for field in list(analysis['extracted_fields'])[:5]:
            print(f"     - {field}")
        
        # Show parent classes
        print("\n   Parent classes:")
        for parent_key in analysis['parent_analysis'].keys():
            print(f"     - {parent_key}")
            
    except Exception as e:
        import traceback
        print(f"   ✗ Failed to analyze module: {e}")
        print(traceback.format_exc())
        return 1
    
    # Test formatting
    print("\n3. Formatting analysis for LLM...")
    try:
        formatted = ContextFormatter.format_woob_analysis(analysis)
        print(f"   ✓ Analysis formatted successfully")
        print(f"   Formatted length: {len(formatted)} characters")
        print(f"\n   First 500 characters:")
        print("   " + "-"*66)
        for line in formatted[:500].split('\n'):
            print(f"   {line}")
        print("   " + "-"*66)
    except Exception as e:
        print(f"   ✗ Failed to format analysis: {e}")
        return 1
    
    print("\n" + "="*70)
    print("All tests passed! ✓")
    print("="*70)
    return 0

if __name__ == "__main__":
    sys.exit(main())
