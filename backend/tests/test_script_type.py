"""determine_script_type 路由测试 —— 修正此前写死返回 web 的问题。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.script_generator import determine_script_type  # noqa: E402


def test_api_case_type():
    assert determine_script_type("api", []) == "api"


def test_backend_api_platform():
    assert determine_script_type("ui", ["backend_api"]) == "api"


def test_web():
    assert determine_script_type("ui", ["web"]) == "web"


def test_android_to_app():
    assert determine_script_type("ui", ["android"]) == "app"


def test_ios_to_app():
    assert determine_script_type("ui", ["ios"]) == "app"


def test_harmony():
    assert determine_script_type("ui", ["harmony"]) == "harmony"


def test_miniprogram():
    assert determine_script_type("ui", ["miniprogram"]) == "miniprogram"


def test_miniprogram_beats_web():
    assert determine_script_type("ui", ["web", "miniprogram"]) == "miniprogram"


def test_empty_defaults_web():
    assert determine_script_type("ui", []) == "web"
