"""Recipe 加载与校验测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentrpa.recipes.loader import RecipeError, list_recipes, load_recipe, parse_recipe, render

EXAMPLES = Path(__file__).resolve().parent.parent / "agentrpa" / "recipes" / "examples"


def test_all_examples_load():
    recipes = list_recipes(EXAMPLES)
    assert recipes, "示例目录不应为空"
    names = {r["name"] for r in recipes}
    assert {"ecom_collect", "web_form", "desktop_erp", "office_report", "social_publish"} <= names
    assert not [r for r in recipes if "error" in r]


def test_example_has_hitl_step():
    recipe = load_recipe(EXAMPLES / "web_form.yaml")
    assert any(step.hitl for step in recipe["steps"])


def test_missing_steps_raises():
    with pytest.raises(RecipeError):
        parse_recipe({"name": "x"})


def test_empty_steps_raises():
    with pytest.raises(RecipeError):
        parse_recipe({"name": "x", "steps": []})


def test_duplicate_step_id_raises():
    with pytest.raises(RecipeError):
        parse_recipe(
            {
                "name": "x",
                "steps": [
                    {"id": "a", "action": "ping"},
                    {"id": "a", "action": "ping"},
                ],
            }
        )


def test_invalid_on_error_raises():
    with pytest.raises(RecipeError):
        parse_recipe({"name": "x", "steps": [{"id": "a", "action": "ping", "on_error": "explode"}]})


def test_step_defaults_inherit_driver():
    recipe = parse_recipe(
        {"name": "x", "driver": "office", "steps": [{"id": "a", "action": "read"}]}
    )
    assert recipe["steps"][0].driver == "office"


def test_render_substitutes_vars():
    out = render({"url": "https://{{host}}/{{path}}"}, {"host": "example.com", "path": "a"})
    assert out["url"] == "https://example.com/a"


def test_render_leaves_unknown_placeholder():
    assert render("{{missing}}", {}) == "{{missing}}"
