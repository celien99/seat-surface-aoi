# 模型产物目录

本目录用于放置 Python 检测层真实模型产物。仓库只提交目录说明和占位文件，不提交真实大权重、现场数据或训练产物。

## 目录约定

```text
model/
├── roi_yolo/
│   ├── .gitkeep
│   └── seat_roi_yolo.onnx              # 真实 Dome ROI YOLO ONNX，部署时放入
├── supervised_defect/
│   ├── .gitkeep
│   └── seat_defect_detector.onnx       # 已知缺陷监督检测 ONNX，部署时放入
├── wideresnet50/
│   ├── .gitkeep
│   └── seat_wrn50_embedding.onnx       # WideResNet50 embedding ONNX，部署时放入
└── patchcore/
    ├── .gitkeep
    ├── seat_pca.json                   # PCA 参数，部署时放入
    ├── seat_patchcore_bank.json        # PatchCore memory bank，部署时放入
    └── seat_patchcore.faiss            # 可选 FAISS 索引，部署时放入
```

## 产物要求

- `seat_roi_yolo.onnx`：输入 Dome 语义光源图，输出 `[x1, y1, x2, y2, score, class_id]` 行表，类别需与 `roi_locator.class_names` 一致。
- `seat_defect_detector.onnx`：输入 ROI 多光源特征 `NCHW` tensor，输出 `[x1, y1, x2, y2, score, class_id]` 行表，类别需与配方 `class_names` 和 `thresholds` 一致。
- `seat_wrn50_embedding.onnx`：输出一维 embedding，维度需与 `embedding_dim` 一致。
- `seat_pca.json`：包含 `version`、`mean`、`components`，版本需与 `pca_version` 一致。
- `seat_patchcore_bank.json`：包含 `version`、`model_family: patchcore`、`embedding_dim`、`coreset_ratio`、`pca_version`、`faiss_enabled` 和 `vectors`。
- `seat_patchcore.faiss`：可选；缺失或不可加载时在线后端回退 exact KNN，不输出 `OK` 掩盖模型错误。

部署前执行：

```bash
uv run python -m tools.validate_model_assets --recipe production_model_example
```
