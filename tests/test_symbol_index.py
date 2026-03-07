"""Tests for the cross-file symbol reference index."""


class TestPythonIndexing:
    """Test Python AST-based symbol indexing."""

    def test_indexes_class_definition(self, tmp_path):
        from scripts.symbol_index import _index_python_file, SymbolIndex

        src = tmp_path / "example.py"
        src.write_text("class MyClass:\n    pass\n")

        index = SymbolIndex()
        _index_python_file(src, "example.py", index)

        assert "MyClass" in index.definitions
        ref = index.definitions["MyClass"][0]
        assert ref.kind == "class"
        assert ref.line == 1

    def test_indexes_function_definition(self, tmp_path):
        from scripts.symbol_index import _index_python_file, SymbolIndex

        src = tmp_path / "example.py"
        src.write_text("def my_func():\n    return 1\n")

        index = SymbolIndex()
        _index_python_file(src, "example.py", index)

        assert "my_func" in index.definitions
        ref = index.definitions["my_func"][0]
        assert ref.kind == "def"

    def test_indexes_imports(self, tmp_path):
        from scripts.symbol_index import _index_python_file, SymbolIndex

        src = tmp_path / "example.py"
        src.write_text("from os.path import join\nimport sys\n")

        index = SymbolIndex()
        _index_python_file(src, "example.py", index)

        assert "join" in index.references
        assert "sys" in index.references

    def test_tracks_file_exports(self, tmp_path):
        from scripts.symbol_index import _index_python_file, SymbolIndex

        src = tmp_path / "example.py"
        src.write_text("class Foo:\n    pass\ndef bar():\n    pass\n")

        index = SymbolIndex()
        _index_python_file(src, "example.py", index)

        assert "Foo" in index.file_exports["example.py"]
        assert "bar" in index.file_exports["example.py"]


class TestTypeScriptIndexing:
    """Test TypeScript regex-based symbol indexing."""

    def test_indexes_function_export(self, tmp_path):
        from scripts.symbol_index import _index_ts_file, SymbolIndex

        src = tmp_path / "component.tsx"
        src.write_text("export function ChatView({ props }) {\n  return null;\n}\n")

        index = SymbolIndex()
        _index_ts_file(src, "component.tsx", index)

        assert "ChatView" in index.definitions

    def test_indexes_interface(self, tmp_path):
        from scripts.symbol_index import _index_ts_file, SymbolIndex

        src = tmp_path / "types.ts"
        src.write_text("export interface SessionData {\n  id: string;\n}\n")

        index = SymbolIndex()
        _index_ts_file(src, "types.ts", index)

        assert "SessionData" in index.definitions

    def test_indexes_named_imports(self, tmp_path):
        from scripts.symbol_index import _index_ts_file, SymbolIndex

        src = tmp_path / "app.tsx"
        src.write_text('import { ChatView, SessionList } from "./components";\n')

        index = SymbolIndex()
        _index_ts_file(src, "app.tsx", index)

        assert "ChatView" in index.references
        assert "SessionList" in index.references


class TestFindSymbol:
    """Test symbol lookup modes."""

    def test_find_all(self, tmp_path):
        from scripts.symbol_index import (
            _index_python_file, SymbolIndex, find_symbol,
        )

        defn = tmp_path / "defn.py"
        defn.write_text("class Foo:\n    pass\n")
        user = tmp_path / "user.py"
        user.write_text("from defn import Foo\n")

        index = SymbolIndex()
        _index_python_file(defn, "defn.py", index)
        _index_python_file(user, "user.py", index)

        refs = find_symbol(index, "Foo", mode="all")
        assert len(refs) == 2
        kinds = {r.kind for r in refs}
        assert "class" in kinds
        assert "import" in kinds

    def test_find_define_only(self, tmp_path):
        from scripts.symbol_index import (
            _index_python_file, SymbolIndex, find_symbol,
        )

        defn = tmp_path / "defn.py"
        defn.write_text("class Foo:\n    pass\n")
        user = tmp_path / "user.py"
        user.write_text("from defn import Foo\n")

        index = SymbolIndex()
        _index_python_file(defn, "defn.py", index)
        _index_python_file(user, "user.py", index)

        refs = find_symbol(index, "Foo", mode="define")
        assert len(refs) == 1
        assert refs[0].kind == "class"

    def test_find_callers_only(self, tmp_path):
        from scripts.symbol_index import (
            _index_python_file, SymbolIndex, find_symbol,
        )

        defn = tmp_path / "defn.py"
        defn.write_text("class Foo:\n    pass\n")
        user = tmp_path / "user.py"
        user.write_text("from defn import Foo\n")

        index = SymbolIndex()
        _index_python_file(defn, "defn.py", index)
        _index_python_file(user, "user.py", index)

        refs = find_symbol(index, "Foo", mode="callers")
        assert len(refs) == 1
        assert refs[0].kind == "import"


class TestFindImpact:
    """Test file impact analysis."""

    def test_finds_affected_files(self, tmp_path):
        from scripts.symbol_index import (
            _index_python_file, SymbolIndex, find_impact,
        )

        defn = tmp_path / "defn.py"
        defn.write_text("class Foo:\n    pass\ndef bar():\n    pass\n")
        user1 = tmp_path / "user1.py"
        user1.write_text("from defn import Foo\n")
        user2 = tmp_path / "user2.py"
        user2.write_text("from defn import bar\n")

        index = SymbolIndex()
        _index_python_file(defn, "defn.py", index)
        _index_python_file(user1, "user1.py", index)
        _index_python_file(user2, "user2.py", index)

        impact = find_impact(index, "defn.py")
        assert "Foo" in impact
        assert "bar" in impact
        assert impact["Foo"][0].file == "user1.py"
        assert impact["bar"][0].file == "user2.py"

    def test_excludes_self_references(self, tmp_path):
        from scripts.symbol_index import (
            _index_python_file, SymbolIndex, find_impact,
        )

        src = tmp_path / "self.py"
        src.write_text("class Foo:\n    pass\n\nfrom self import Foo\n")

        index = SymbolIndex()
        _index_python_file(src, "self.py", index)

        impact = find_impact(index, "self.py")
        # Self-reference should be excluded
        assert not impact.get("Foo", [])


class TestFullIndex:
    """Integration test: build the full project index."""

    def test_build_index_succeeds(self):
        from scripts.symbol_index import build_index

        index = build_index()

        # Should find key Chad symbols
        assert "SessionManager" in index.definitions
        assert "TaskExecutor" in index.definitions
        assert "verify" in index.definitions

        # Should find TS symbols too
        assert "ChatView" in index.definitions

    def test_full_index_impact_for_providers(self):
        from scripts.symbol_index import build_index, find_impact

        index = build_index()
        impact = find_impact(index, "src/chad/util/providers.py")

        # providers.py exports are used by multiple files
        assert len(impact) > 0
        all_files = set()
        for refs in impact.values():
            for ref in refs:
                all_files.add(ref.file)
        assert any("test_providers" in f for f in all_files)
