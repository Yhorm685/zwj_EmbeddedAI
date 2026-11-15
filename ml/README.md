# 数据集、训练与模型转换

在仓库根目录运行以下命令，使用已安装 NumPy、CPU 版 PyTorch 的项目虚拟环境。第一次正式训练需要完成三类腰部佩戴实采，各 10 个独立批次。单元测试的临时信号只用于验证程序。

```powershell
.\.venv\Scripts\python.exe -m ml.data --data data/raw --out evidence/dataset.json
.\.venv\Scripts\python.exe -m ml.train --data data/raw --out models/v1
.\.venv\Scripts\python.exe -m ml.export --data data/raw --model models/v1
.\.venv\Scripts\python.exe -m ml.evaluate --data data/raw --model models/v1 --out evidence/pc-v1
.\.venv\Scripts\python.exe -m unittest ml.test_pipeline -v
```

## 数据约定

- 原始文件位于 `data/raw/{still,walk,run}/<批次>.csv`，表头包含 `seq,ms,ax,ay,az,gx,gy,gz,status,host_ns`。六轴为有符号 int16 原始值，`ms` 是设备毫秒，`host_ns` 是主机时间。训练采用设备时间。
- 配套同名 JSON 至少给出 `label` 和 `batch`，分别对应上一级目录和文件名主干。采集器记录 50 Hz、±8 g、±1000 °/s 的配置。未完整录制或量程不符的批次会报错。
- 裁剪区间采用相对首条设备记录的毫秒，含起点、不含终点。缺省去掉首尾各 2 秒。按真实记录修改 `valid_start_ms`、`valid_end_ms`；若中间发生动作转换，增加 `valid_segments_ms`，例如 `[[2000,18000],[23000,58000]]` 指定两个稳定区间。CSV 保持原貌。
- 种子默认为 407。先对每类独立批次排序、打乱并分为训练 6 批、验证 2 批、测试 2 批，最后分别切 100 点窗口、25 点步长。同一文件内的状态异常、缺号、设备间隔超出 10–30 ms 或标注区间变化都会切断窗口。序号和毫秒支持 uint32 回绕；复位导致时钟倒退时应重新独立录制该批。每批列出原始时长、有效时长、有效样本和窗口数，时长估算包含末次采样的名义 20 ms。
- 质量审计的 `evidence/dataset.json` 记录每个批次的分配、文件 SHA-256 及实际可用量。CSV 和 JSON 计算摘要前将 CRLF 统一为 LF，以支持跨平台检出。质量审计统计保留条件和窗口数。正式测试集的分类成绩在模型冻结后计算。

## 训练与导出

每轴按时间顺序先将原始加速度除以 4096，角速度除以 32.8，再以 float32 累加求均值、总体标准差、均方根、峰峰值。依 `ax,ay,az,gx,gy,gz` 拼接为 24 维。先在所有训练窗口上计算逐特征标准化参数，标准差小于 1e-6 时以 1 作为除数。验证和测试沿用同一组参数。

网络为全连接 24→16、ReLU、全连接 16→3，共 451 个可训练参数。输出顺序固定为 `still,walk,run`，三项均为 logits。采用 CPU、Adam、学习率 0.003、每批最多 64 个窗口、默认 100 个 epoch；保存验证交叉熵最低的权重。命令行支持 `--epochs` 和 `--seed`，默认值适用于首版。每次正式训练写入新的版本目录。

| 产物 | 内容 |
| --- | --- |
| `split.json` | 批次分配及原始数据与标注的摘要 |
| `training.json` | 随机种子、软件版本、训练与验证各轮损失、选定轮次、批次质量 |
| `model.pt` | 冻结的浮点权重、偏置和标准化参数及对应划分的摘要 |
| `model.json` | 量化参数、类别顺序、浮点模型文件摘要及量化模型 ID |
| `model_data.h` | 固件编译使用的连续行优先权重、偏置、量化乘数与移位、标准化和尺度 |

`ml.train` 自动执行一次转换。`ml.export` 使用保存的模型和相同的训练批次重建量化参数，支持 `--header <路径>`。原始记录或标注变化时应建立新版本。输入、隐藏层 ReLU 后输出、最终 logits 的对称尺度均由训练窗口最大绝对值计算。各层权重按张量取单尺度，零点为 0；偏置单位为输入尺度乘权重尺度。特征和标准化使用 float32，激活与权重为 int8、偏置和累加器为 int32。输入量化采用最近偶数舍入，整数重缩放按 CMSIS-NN v7 的默认双舍入路径实现。[Arm CMSIS-NN v7 整数实现](https://github.com/ARM-software/CMSIS-NN/blob/v7.0.0/Include/arm_nnsupportfunctions.h)

## 冻结测试与板端回放

正式模型冻结后，`ml.evaluate` 在同一批封存窗口上运行浮点网络和整数参考，并将结果写入新目录。指标包含准确率、各类召回率、混淆矩阵、分类分歧率、准确率差、反量化后的平均与最大 logits 绝对误差。`predictions.csv` 逐窗口保留批次、首个序号、标签、预测和分数。

`test_windows.npz` 是板端回放输入。它包含六轴 `raw[N,100,6]`、量化输入 `input_q[N,24]`、整数参考 `scores_q[N,3]`、浮点 `logits[N,3]`、`labels`、`batch`、`start_seq`、`model_id`、`output_scale`，可用 `numpy.load(..., allow_pickle=False)` 读取。板端使用相同的模型 ID 和原始窗口，记录板端实际返回的值，再进行对应比较。
