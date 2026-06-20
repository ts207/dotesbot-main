from __future__ import annotations

import os
import json
import math
from pathlib import Path
from typing import Any, Mapping

# Global variables for model state caching
_MODEL_DATA: dict | None = None
_FEATURE_NAMES: list[str] | None = None
_METADATA: dict | None = None

def load_model(model_path: str = "models/dota_lgbm_win/model.json") -> bool:
    """Load model.json, features.json, and metadata.json from the specified model path."""
    global _MODEL_DATA, _FEATURE_NAMES, _METADATA
    try:
        path = Path(model_path)
        if not path.exists():
            print(f"Model path {model_path} does not exist.")
            return False

        with open(path, "r", encoding="utf-8") as f:
            _MODEL_DATA = json.load(f)

        # Load features.json
        features_path = path.parent / "features.json"
        if features_path.exists():
            with open(features_path, "r", encoding="utf-8") as f:
                _FEATURE_NAMES = json.load(f)
        else:
            _FEATURE_NAMES = []

        # Load metadata.json
        metadata_path = path.parent / "metadata.json"
        if metadata_path.exists():
            with open(metadata_path, "r", encoding="utf-8") as f:
                _METADATA = json.load(f)
        else:
            _METADATA = {}

        return True
    except Exception as e:
        print(f"Error loading model from {model_path}: {e}")
        _MODEL_DATA = None
        _FEATURE_NAMES = None
        _METADATA = None
        return False

def build_side_features(
    game: dict | Mapping,
    mapping: dict | Mapping,
    side: str,
    book: dict | None = None,
    paired_book: dict | None = None,
) -> dict[str, float] | None:
    """Build side-oriented features for Radiant or Dire.
    
    Returns None if any of the required features (net worths, scores) are missing or invalid.
    """
    try:
        radiant_nw = game.get("radiant_net_worth")
        dire_nw = game.get("dire_net_worth")
        radiant_score = game.get("radiant_score")
        dire_score = game.get("dire_score")

        if (radiant_nw is None or dire_nw is None or 
            radiant_score is None or dire_score is None):
            return None

        # Convert to float and handle potential formatting/parsing issues
        r_nw = float(radiant_nw)
        d_nw = float(dire_nw)
        r_score = float(radiant_score)
        d_score = float(dire_score)

        side_lower = side.lower()
        if side_lower == "radiant":
            token_net_worth_lead = r_nw - d_nw
            token_score_margin = r_score - d_score
        elif side_lower == "dire":
            token_net_worth_lead = d_nw - r_nw
            token_score_margin = d_score - r_score
        else:
            return None

        return {
            "token_net_worth_lead": token_net_worth_lead,
            "token_score_margin": token_score_margin
        }
    except (TypeError, ValueError):
        return None

def _evaluate_node(node: dict, features: dict[str, float], feature_names_list: list[str]) -> float:
    """Recursively evaluate a LightGBM decision tree node."""
    if "leaf_value" in node:
        return float(node["leaf_value"])

    split_feature = node.get("split_feature")
    threshold = float(node.get("threshold", 0.0))

    feature_val = None
    if isinstance(split_feature, int):
        if 0 <= split_feature < len(feature_names_list):
            feature_name = feature_names_list[split_feature]
            feature_val = features.get(feature_name)
    elif isinstance(split_feature, str):
        feature_val = features.get(split_feature)

    if feature_val is None:
        # Default direction on missing feature value (typical LightGBM default_left)
        default_left = node.get("default_left", True)
        child = node.get("left_child") if default_left else node.get("right_child")
        if child is None:
            raise ValueError(f"Missing child node for split feature {split_feature}")
        return _evaluate_node(child, features, feature_names_list)

    if feature_val <= threshold:
        child = node.get("left_child")
    else:
        child = node.get("right_child")

    if child is None:
        raise ValueError(f"Missing child node for split feature {split_feature}")
    return _evaluate_node(child, features, feature_names_list)

def predict_probability(features: dict[str, float] | None) -> dict[str, Any]:
    """Predict win probability using the loaded LightGBM trees.
    
    Returns a dict with model_probability, model_version, features_available, and reason.
    Fails closed if the model is not loaded or features are missing.
    """
    version = _METADATA.get("version", "unknown") if _METADATA else "unknown"

    if _MODEL_DATA is None:
        return {
            "model_probability": 0.0,
            "model_version": version,
            "features_available": False,
            "reason": "model_not_loaded"
        }

    if not features:
        return {
            "model_probability": 0.0,
            "model_version": version,
            "features_available": False,
            "reason": "missing_required_features"
        }

    # Ensure all configured features from features.json are present
    if _FEATURE_NAMES:
        for f in _FEATURE_NAMES:
            if f not in features:
                return {
                    "model_probability": 0.0,
                    "model_version": version,
                    "features_available": False,
                    "reason": f"missing_feature_{f}"
                }

    try:
        raw_score = 0.0
        trees = _MODEL_DATA.get("tree_info", [])
        for tree in trees:
            tree_structure = tree.get("tree_structure")
            if tree_structure:
                raw_score += _evaluate_node(tree_structure, features, _FEATURE_NAMES or [])

        # Sigmoid activation to get probability
        p = 1.0 / (1.0 + math.exp(-raw_score))
        return {
            "model_probability": p,
            "model_version": version,
            "features_available": True,
            "reason": "ok"
        }
    except Exception as e:
        return {
            "model_probability": 0.0,
            "model_version": version,
            "features_available": False,
            "reason": f"prediction_error: {e}"
        }
