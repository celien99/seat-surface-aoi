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
    ├── seat_patchcore_bank.json   # PatchCore memory bank
    ├── seat_patchcore.faiss       # 可选 FAISS 索引
    ├── embeddings.jsonl           # 本地训练 embedding 明细，体积大，不提交 Git
    ├── pca_embeddings.jsonl       # 本次训练 PCA 后 embedding 明细
    └── patchcore_training_summary.json
```

## 产物要求

- `seat_roi_seg.onnx`：生产推荐 ROI 定位产物。输入当前配方的 `DOME` 语义光源图，输出 YOLO segmentation mask；在线链路用 mask 自动生成运行时 `polygon_xy`，ROI 模板只作为安全边界和默认约束。当前项目 ROI 单类别为 `seat`，必须与 `roi_locator.class_names` 一致。
- `seat_roi_yolo.onnx`：兼容 bbox ROI 产物。输入 Dome 语义光源图，输出 `[x1, y1, x2, y2, score, class_id]` 行表，或通过 `output_decode: ultralytics_yolo` 解析 Ultralytics ONNX 输出。
- `seat_wrn50_embedding.onnx`：空间 PatchCore 模式下输出 layer2+layer3 中间层特征图，当前拼接后的原始 patch embedding 为 1536 维；全局模式才输出一维 embedding，维度必须与配方 `models.<key>.embedding_dim` 一致。
- `seat_pca.json`：包含 `version`、`mean`、`components`，版本必须与配方 `pca_version` 一致；当前生产资产使用 `pca_seat_v2`，输入维度为 1536，输出维度为 524。
- `seat_patchcore_bank.json`：包含 `version`、`model_family: patchcore`、`embedding_dim`、`coreset_ratio`、`pca_version`、`faiss_enabled`、`metadata` 和 `vectors`；当前小样本工程验证资产为 `bank_v3_spatial64_small`，使用 `64x64` 空间 patch、PCA 后 524 维向量、`coreset_ratio=0.25`，共 43,008 个向量。`metadata` 记录输入通道、空间网格、manifest hash 和 embedding ONNX hash，供 `tools.validate_model_assets` 防错。
- `seat_patchcore.faiss`：可选；缺失或不可加载时在线后端回退 exact KNN，并在 trace 中记录 fallback reason。启用时维度和向量数必须与 `seat_patchcore_bank.json` 一致。
- `embeddings.jsonl`：训练过程中的原始空间 embedding 明细，当前会随样本数、`64x64` 网格和 1536 维 patch embedding 快速膨胀，仅用于离线审计或重建 PCA/memory bank；仓库通过 `.gitignore` 忽略该文件，在线部署不依赖它。

当前生产缺陷判定链路不依赖 `model/supervised_defect/seat_defect_detector.onnx`。座椅 ROI 定位由 `seat_roi_seg.onnx` 完成，表面异常判定由 WideResNet50 embedding + PCA + PatchCore memory bank/FAISS 无监督主模型完成。监督 YOLO 只能作为离线研究或对比实验资产，不是生产配方必需项。

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
  --model-key patchcore_unknown_detector `
  --embedding-backend onnx_wideresnet50 `
  --embedding-model model/wideresnet50/seat_wrn50_embedding.onnx `
  --split train `
  --spatial-mode `
  --spatial-layers layer2,layer3 `
  --spatial-upsample-height 64 `
  --spatial-upsample-width 64 `
  --pca-components 524 `
  --pca-version pca_seat_v2 `
  --bank-version bank_v3_spatial64_small `
  --coreset-ratio 0.25 `
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

当前 `model/patchcore/patchcore_training_summary.json` 记录的是基于 `datasets/seat_roi_train` 的 `64x64` 小样本工程验证训练；大模型资产文件由 `.gitignore` 忽略，不随 Git 提交。`patchcore_training_summary.json` 会记录实际进入 embedding 的 `input_shape_summary`，用于确认训练输入与在线检测裁剪策略一致。PatchCore 只能用人工确认正常样本建库；NG、RECHECK 和人工复核样本用于阈值曲线、误报和漏检分析，不进入正常样本 memory bank。
