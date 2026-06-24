# 模型产物目录

本目录用于放置 Python 检测层真实模型产物。仓库只提交目录说明和占位文件，不提交真实大权重、现场数据或训练产物。

## 目录约定

```text
model/
├── roi_yolo/
│   ├── .gitkeep
│   ├── seat_roi_seg.onnx               # 推荐：真实 Dome ROI YOLO segmentation ONNX，部署时放入
│   └── seat_roi_yolo.onnx              # 兼容：bbox ROI YOLO ONNX，部署时放入
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

- `seat_roi_seg.onnx`：推荐 ROI 定位产物。输入 Dome 语义光源图，输出 YOLO segmentation mask；在线链路用 mask 自动生成运行时 `polygon_xy`，`roi_templates` 只作为安全边界和 `output_size` 约束。当前项目 ROI 单类别为 `seat`，需与 `roi_locator.class_names` 一致。
- `seat_roi_yolo.onnx`：兼容 bbox ROI 产物。输入 Dome 语义光源图，输出 `[x1, y1, x2, y2, score, class_id]` 行表，或使用 `output_decode: ultralytics_yolo` 直接接 Ultralytics ONNX 输出。
- `seat_wrn50_embedding.onnx`：输出一维 embedding，维度需与 `embedding_dim` 一致。
- `seat_pca.json`：包含 `version`、`mean`、`components`，版本需与 `pca_version` 一致。
- `seat_patchcore_bank.json`：包含 `version`、`model_family: patchcore`、`embedding_dim`、`coreset_ratio`、`pca_version`、`faiss_enabled` 和 `vectors`。
- `seat_patchcore.faiss`：可选；缺失或不可加载时在线后端回退 exact KNN，不输出 `OK` 掩盖模型错误。

当前生产检测链路不依赖 `model/supervised_defect/seat_defect_detector.onnx`。座椅 ROI 定位由 `seat_roi_seg.onnx` 完成，表面异常判定由 WideResNet50 embedding + PCA + PatchCore memory bank/FAISS 无监督主模型完成。监督 YOLO 只能作为离线研究或对比实验资产，不是生产配方必需项。

部署前执行：

```powershell
uv run python -m tools.validate_model_assets --recipe seat_a_black_leather_production_v1
uv run python -m tools.validate_model_assets --recipe seat_a_robot_flyshot_production_v1
```

`production_model.example.yaml` 保留为真实模型接入参考模板，也可单独检查：

```powershell
uv run python -m tools.validate_model_assets --recipe production_model_example
```

## 训练资产生成入口

当前仓库可生成 `python_detector` 生产配方直接消费的模型资产：

```powershell
# Dome ROI YOLO segmentation
uv run python -m training_tools.train_roi_yolo `
  --data datasets/roi_seg/dataset.yaml `
  --task segment `
  --model yolov8n-seg.pt `
  --imgsz 1024 `
  --output model/roi_yolo/seat_roi_seg.onnx

# WideResNet50 embedding
uv run python -m training_tools.export_wideresnet_embedding `
  --output model/wideresnet50/seat_wrn50_embedding.onnx `
  --embedding-dim 1024

# PatchCore PCA、memory bank、可选 FAISS
uv run python -m training_tools.train_patchcore_assets `
  --manifest datasets/seat_trace_v1/dataset_manifest.jsonl `
  --output-dir model/patchcore `
  --split train `
  --pca-components 3 `
  --coreset-ratio 0.1 `
  --build-faiss
```

`train_patchcore_assets` 从 trace manifest 的真实 OK ROI 多光源 PGM 图提取 embedding，确保 PCA 和 memory bank 与在线 `FeatureBuilder` 的输入通道一致。NG、RECHECK、人工复核样本用于阈值曲线和误报/漏检分析，不进入正常样本 memory bank。
