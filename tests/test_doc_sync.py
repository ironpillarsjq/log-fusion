"""文档一致性自动检查：代码与文档漂移必须让测试失败。

对应 `AGENT_RULES.md` §4：新增/重命名/删除模块或目录时，必须同步
`docs/ARCHITECTURE.md` 组件表、`docs/README.md` 代码范围树，
并且 `docs/DATABASE.md` 要引用列事实源。
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "backend" / "app"

ARCHITECTURE = REPO_ROOT / "docs" / "ARCHITECTURE.md"
README = REPO_ROOT / "docs" / "README.md"
DATABASE = REPO_ROOT / "docs" / "DATABASE.md"
AGENTS = REPO_ROOT / "AGENTS.md"


def _app_modules() -> list[str]:
    return sorted(path.name for path in APP_DIR.glob("*.py") if path.name != "__init__.py")


def test_architecture_component_table_covers_every_app_module():
    architecture = ARCHITECTURE.read_text(encoding="utf-8")
    missing = [name for name in _app_modules() if f"app/{name}" not in architecture]
    assert not missing, f"以下模块未登记到 docs/ARCHITECTURE.md 组件表：{missing}"


def test_readme_code_tree_covers_key_paths():
    readme = README.read_text(encoding="utf-8")
    for entry in ("parsers.py", "tests/", "tools/", "vendor/", "pytest.ini", "启动服务.cmd"):
        assert entry in readme, f"docs/README.md 的代码范围树缺少：{entry}"


def test_database_doc_uses_column_truth_instead_of_old_whitelist():
    database = DATABASE.read_text(encoding="utf-8")
    assert "LINUX_TABLE_FIELDS" in database, "docs/DATABASE.md 必须引用代码侧的列事实源"
    assert "WINDOWS_TABLE_FIELDS" in database
    assert "### 4.2 写入字段白名单" not in database, "旧的全局字段白名单章节已被“按表裁剪”取代"


def test_agents_md_keeps_parser_layer_and_time_pitfall():
    agents = AGENTS.read_text(encoding="utf-8")
    assert "parsers.py" in agents
    assert "1292" in agents, "时间格式导致 1292 的坑必须留在操作提示里"
