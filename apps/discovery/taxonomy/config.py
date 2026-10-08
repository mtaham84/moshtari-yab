from __future__ import annotations

import os

# Hierarchy Configuration
MAX_HIERARCHY_DEPTH = int(os.environ.get("PEYDA_MAX_HIERARCHY_DEPTH", "5"))
CATEGORY_TOP_K = int(os.environ.get("PEYDA_CATEGORY_TOP_K", "3"))

# Confidence Thresholds
CATEGORY_HIGH_CONFIDENCE = float(os.environ.get("PEYDA_CATEGORY_HIGH_CONFIDENCE", "0.85"))
CATEGORY_MIN_CONFIDENCE = float(os.environ.get("PEYDA_CATEGORY_MIN_CONFIDENCE", "0.65"))
CATEGORY_AMBIGUITY_MARGIN = float(os.environ.get("PEYDA_CATEGORY_AMBIGUITY_MARGIN", "0.05"))

# Product Ranking Weights (Sum = 1.0)
PRODUCT_SEMANTIC_WEIGHT = float(os.environ.get("PEYDA_PRODUCT_SEMANTIC_WEIGHT", "0.35"))
PRODUCT_CATEGORY_WEIGHT = float(os.environ.get("PEYDA_PRODUCT_CATEGORY_WEIGHT", "0.20"))
PRODUCT_ATTRIBUTE_WEIGHT = float(os.environ.get("PEYDA_PRODUCT_ATTRIBUTE_WEIGHT", "0.20"))
PRODUCT_AVAILABILITY_WEIGHT = float(os.environ.get("PEYDA_PRODUCT_AVAILABILITY_WEIGHT", "0.10"))
PRODUCT_PRICE_WEIGHT = float(os.environ.get("PEYDA_PRODUCT_PRICE_WEIGHT", "0.10"))
PRODUCT_OTHER_WEIGHT = float(os.environ.get("PEYDA_PRODUCT_OTHER_WEIGHT", "0.05"))

# Cache TTLs
TAXONOMY_CACHE_TIMEOUT = int(os.environ.get("PEYDA_TAXONOMY_CACHE_TIMEOUT", str(3600 * 24)))  # 24 hours
NEED_EMBEDDING_CACHE_TIMEOUT = int(os.environ.get("PEYDA_NEED_EMBEDDING_CACHE_TIMEOUT", str(3600 * 12)))  # 12 hours
LLM_ANALYSIS_CACHE_TIMEOUT = int(os.environ.get("PEYDA_LLM_ANALYSIS_CACHE_TIMEOUT", str(3600 * 6)))  # 6 hours

# Pipeline Version
PIPELINE_VERSION = "2.1.0-hierarchical"
PROMPT_VERSION = "v2"
