# Horus ADHD Screening

本项目是一个本地运行的 ADHD 注意力筛查原型。界面用 SvelteKit 做，Tauri 后端负责摄像头、眼动处理、实验数据落盘和报告生成。

## 主要流程

- 在本机维护受试者列表。
- 运行 Sternberg 视觉工作记忆任务，支持调整 block 和 trial 数量。
- 筛查前用摄像头完成注视点校准。
- 将 trial、校准记录和报告保存到本地 SQLite。
- 根据 27 个行为、瞳孔和注视特征调用随包的随机森林模型生成报告。
- 眼动校准完成后，可启动单独的 Unity 训练游戏。

摄像头画面只在本地处理。这个项目用于筛查和研究，不是医学诊断设备。

## Development

```bash
npm install
npm run dev
npm run tauri dev
npm run check
```

## Notes

运行和打包时需要保留 `models/` 目录。ONNX、TFLite、JSON 模型文件以及 baseline 参数文件要放在一起。
