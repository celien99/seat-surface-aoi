# 模型产物目录

本目录用于放置 Python 检测层的真实模型和 PatchCore 资产。仓库可以保留说明、占位文件和小型验证资产；现场大权重、原始采集数据和训练过程数据应按部署流程管理。

## 目录约定

```text
model/
├── roi_yolo/
│   ├── .gitkeep
│   ├── seat_roi_seg.onnx          # 推荐：Dome ROI YOLO segmentation ONNX
│   └── seat_roi_yolo.onnx         # 兼容：bbox ROI YOLO ONNX
├── wideresnet50/
│   ├── .gitkeep
│   └── seat_wrn50_embedding.onnx  # WideResNet50 embedding ONNX
└── patchcore/
    ├── .gitkeep
    ├── seat_pca.json              # PCA 参数
    ├── seat_patchcore_bank.json   # PatchCore memory bank 元数据
    ├── seat_patchcore_bank.npy    # PatchCore memory bank float32 向量矩阵
    ├── seat_patchcore.faiss       # 可选 FAISS 索引
    ├── embeddings.npy             # 调试保留的原始 embedding 中间矩阵，默认训练后删除
    ├── pca_embeddings.npy         # 调试保留的 PCA 后 embedding 中间矩阵，默认训练后删除
    └── patchcore_training_summary.json
```

## 产物要求

- `seat_roi_seg.onnx`：生产推荐 ROI 定位产物。输入当前配方的 `DOME` 语义光源图，输出 YOLO segmentation mask；在线链路用 mask 自动生成运行时 `polygon_xy`，ROI 模板只作为安全边界和默认约束。当前项目 ROI 名称为 `seat`，必须与 `roi_locator.class_names` 一致；这里的 `class_names` 只服务 ROI 定位，不代表缺陷类别。
- `seat_roi_yolo.onnx`：兼容 bbox ROI 产物。输入 Dome 语义光源图，输出 `[x1, y1, x2, y2, score, class_id]` 行表，或通过 `output_decode: ultralytics_yolo` 解析 Ultralytics ONNX 输出。
- `seat_wrn50_embedding.onnx`：空间 PatchCore 模式下输出 layer2+layer3 中间层特征图，当前拼接后的原始 patch embedding 为 1536 维；全局模式才输出一维 embedding，维度必须与配方 `models.<key>.embedding_dim` 一致。
- `seat_pca.json`：包含 `version`、`mean`、`components`，版本必须与配方 `pca_version` 一致；当前生产资产使用 `pca_seat_v3`，输入维度为 1536，输出维度为 524。
- `seat_patchcore_bank.json`：只保存 memory bank 元数据，包含 `version`、`model_family: patchcore`、`embedding_dim`、`coreset_ratio`、`pca_version`、`faiss_enabled`、`vector_count`、`vectors_path` 和 `metadata`；不再允许内嵌大体积 `vectors` JSON 数组。`metadata` 记录输入通道、空间网格、manifest hash 和 embedding ONNX hash，供 `tools.validate_model_assets` 防错。
- `seat_patchcore_bank.npy`：PatchCore memory bank 的 `float32` 二维向量矩阵。当前生产资产为 `bank_v4_spatial256`，使用 `256×256` 空间 patch、PCA 后 524 维向量、`coreset_ratio=1.0`（全量 vector），共 2,752,512 个向量。在线 exact KNN fallback 和 FAISS 索引构建都从该文件读取向量。
- `seat_patchcore.faiss`：可选；缺失或不可加载时在线后端回退 exact KNN，并在 trace 中记录 fallback reason。启用时维度和向量数必须与 `seat_patchcore_bank.json` / `seat_patchcore_bank.npy` 一致。
- `embeddings.npy` / `pca_embeddings.npy`：训练过程中的中间 embedding 矩阵。`training_tools.train_patchcore_assets` 默认训练完成后删除它们，仅在显式传 `--keep-intermediate-embeddings` 做排障时保留。独立调试命令 `training_tools.extract_embeddings` 仍可输出 JSONL 明细，但生产训练链路不再依赖 JSONL。

当前生产缺陷判定链路不依赖 `model/supervised_defect/seat_defect_presence.onnx`。座椅 ROI 定位由 `seat_roi_seg.onnx` 完成，表面异常判定由 WideResNet50 embedding + PCA + PatchCore memory bank/FAISS 无监督主模型完成。监督 YOLO 只能作为离线研究或对比实验资产，不是生产配方必需项。

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

ROI YOLO segmentation：

```powershell
uv run python -m training_tools.train_roi_yolo `
  --data datasets/roi_seg/dataset.yaml `
  --task segment `
  --model yolov8n-seg.pt `
  --imgsz 1024 `
  --output model/roi_yolo/seat_roi_seg.onnx
```

WideResNet50 embedding：

```powershell
uv run python -m training_tools.export_wideresnet_embedding `
  --output model/wideresnet50/seat_wrn50_embedding.onnx `
  --input-channels 3 `
  --spatial-mode `
  --spatial-layers layer2,layer3
```

PatchCore PCA、memory bank 和可选 FAISS：

```powershell
uv run python -m training_tools.train_patchcore_assets `
  --manifest datasets/seat_roi_train/dataset_manifest.jsonl `
  --output-dir model/patchcore `
  --recipe seat_a_black_leather_production_v1 `
  --model-key patchcore_detector `
  --embedding-backend onnx_wideresnet50 `
  --embedding-model model/wideresnet50/seat_wrn50_embedding.onnx `
  --split train `
  --spatial-mode `
  --spatial-layers layer2,layer3 `
  --spatial-upsample-height 256 `
  --spatial-upsample-width 256 `
  --pca-components 524 `
  --pca-version pca_seat_v3 `
  --bank-version bank_v4_spatial256 `
  --coreset-ratio 1.0 `
  --coreset-method stride `
  --build-faiss
```

如果真实样本来自 `images_capture/` 平铺 PNG，先用已经训练好的 ROI segmentation 模型生成 ROI PNG 和 manifest：

```powershell
uv run python -m training_tools.collect_capture_dataset `
  --input images_capture\20260623\LINE1_AOI_CAPTURE_MANUAL_SEAT_9000 `
  --output datasets\seat_capture_20260623_9000 `
  --recipe seat_a_black_leather_production_v1 `
  --split train `
  --label-status verified_ok `
  --skip-failed
```

`collect_capture_dataset` 默认按配方 `light_order` 将 `L1/L2/L3` 映射为 `DIFFUSE/POLAR_DIFFUSE/HIGH_LEFT`，调用 `model/roi_yolo/seat_roi_seg.onnx` 定位真实座椅 ROI，并输出 PNG 图和 `dataset_manifest.jsonl`。默认保留 segmentation 裁出的原生 ROI 尺寸，避免纹理和细小缺陷被压缩失真；只有需要与固定 PatchCore 输入尺寸对齐时，才显式传 `--roi-output-size WIDTHxHEIGHT`，缩放方式是等比例 letterbox，不做直接拉伸。

当前 `model/patchcore/patchcore_training_summary.json` 记录的是基于 `datasets/seat_roi_train` 的 `256×256` 空间 patch、全量 vector（coreset_ratio=1.0）的生产训练；大模型资产文件由 `.gitignore` 忽略，不随 Git 提交。`patchcore_training_summary.json` 会记录实际进入 embedding 的 `input_shape_summary`，用于确认训练输入与在线检测裁剪策略一致。PatchCore 只能用人工确认正常样本建库；NG、RECHECK 和人工复核样本用于阈值曲线、误报和漏检分析，不进入正常样本 memory bank。
