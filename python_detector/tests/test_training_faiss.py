from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.fixture
def memory_bank_json(tmp_path: Path) -> Path:
    """构造包含 20 条 8 维向量的 memory bank JSON。"""
    path = tmp_path / "bank.json"
    bank = {
        "version": "bank_v1",
        "model_family": "patchcore",
        "embedding_dim": 8,
        "coreset_ratio": 1.0,
        "pca_version": "pca_v1",
        "faiss_enabled": True,
        "vectors": [[float(i + j) for j in range(8)] for i in range(20)],
    }
    path.write_text(json.dumps(bank), encoding="utf-8")
    return path


def test_build_faiss_flat_l2(tmp_path: Path, memory_bank_json: Path) -> None:
    """FlatL2 索引构建和搜索验证。"""
    if not _faiss_available():
        pytest.skip("faiss-cpu 未安装")

    output = tmp_path / "index.faiss"
    result = _run_faiss_case(
        f"""
from pathlib import Path
import json
import faiss
import numpy as np
from training_tools.build_faiss_index import build_faiss_index

output = Path({str(output)!r})
result = build_faiss_index(
    bank_path=Path({str(memory_bank_json)!r}),
    output=output,
    index_type="FlatL2",
    nlist=4,
)
idx = faiss.read_index(str(output))
query = np.asarray([[1.0] * 8], dtype=np.float32)
distances, indices = idx.search(query, 3)
assert distances.shape == (1, 3)
assert indices.shape == (1, 3)
assert all(d >= 0.0 for d in distances[0])
print(json.dumps(result, ensure_ascii=False, sort_keys=True))
"""
    )

    assert output.exists()
    assert output.stat().st_size > 0
    assert result["index_type"] == "FlatL2"
    assert result["vector_count"] == 20
    assert result["dimension"] == 8


def test_build_faiss_ivf_flat(tmp_path: Path, memory_bank_json: Path) -> None:
    """IVFFlat 索引构建。"""
    if not _faiss_available():
        pytest.skip("faiss-cpu 未安装")

    output = tmp_path / "index.faiss"
    result = _run_faiss_case(
        f"""
from pathlib import Path
import json
from training_tools.build_faiss_index import build_faiss_index

result = build_faiss_index(
    bank_path=Path({str(memory_bank_json)!r}),
    output=Path({str(output)!r}),
    index_type="IVFFlat",
    nlist=4,
)
print(json.dumps(result, ensure_ascii=False, sort_keys=True))
"""
    )
    assert result["index_type"] == "IVFFlat"
    assert output.stat().st_size > 0


def test_build_faiss_empty_bank(tmp_path: Path) -> None:
    """空 memory bank 抛出错误。"""
    if not _faiss_available():
        pytest.skip("faiss-cpu 未安装")

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"version": "v", "model_family": "patchcore", "embedding_dim": 3, "vectors": []}), encoding="utf-8")

    _run_faiss_case(
        f"""
from pathlib import Path
from training_tools.build_faiss_index import build_faiss_index
from training_tools.training_errors import EmptyMemoryBankError

try:
    build_faiss_index(bank_path=Path({str(empty)!r}), output=Path({str(tmp_path / "out.faiss")!r}))
except EmptyMemoryBankError as exc:
    assert "为空" in str(exc)
    print({{"ok": True}})
else:
    raise AssertionError("expected EmptyMemoryBankError")
""",
        parse_json=False,
    )


def test_build_faiss_not_installed(tmp_path: Path, memory_bank_json: Path) -> None:
    """FAISS 未安装时优雅报错。"""
    if _faiss_available():
        pytest.skip("faiss-cpu 已安装，跳过此测试")

    from training_tools.build_faiss_index import build_faiss_index
    from training_tools.training_errors import EmbeddingExtractionError

    with pytest.raises(EmbeddingExtractionError, match="faiss-cpu 未安装"):
        build_faiss_index(bank_path=memory_bank_json, output=tmp_path / "out.faiss")


def _faiss_available() -> bool:
    result = subprocess.run(
        [sys.executable, "-c", "import faiss, numpy"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _run_faiss_case(code: str, *, parse_json: bool = True) -> dict:
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if not parse_json:
        return {}
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines, result.stdout + result.stderr
    return json.loads(lines[-1])
