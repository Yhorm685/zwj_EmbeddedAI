# 任务 04 · STM32F407 人体运动识别

固定在腰部的 MPU6050 以 50 Hz 采集六轴运动数据。STM32F407ZGT6 每积满 100 个连续有效样本建立一个 2 秒窗口，每新增 25 点更新一次静止、走路、跑步三分类结果。分类需要先装入由真实运动数据训练的模型。

**当前可用的是采集版固件**，通过 USART1 输出六轴原始值、设备时间、序号和状态。采集版的模型标识为 `none`。固件、训练、转换、回放和报告代码已就绪；首轮 60 秒静止数据、正式训练模型、板端指标和演示视频等待实物采集。

## 第一次上板

本机任务目录为 `D:\kofen\EmbeddedAI`。使用 STM32F407ZGT6、8 MHz 外部晶振，MPU6050 接 PB6/PB7，3.3 V USB–TTL 接 PA9/PA10。首次只录一段 60 秒静止基线；查看[接线、烧录、采集及回放操作单](docs/bringup.md)。

从新克隆的 Windows x64 工作区，在仓库根目录运行以下命令。工具链为 Arm GNU Toolchain、CMake 和 Ninja。Python 3.12 环境使用 CPU 版 PyTorch。

~~~powershell
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
Push-Location firmware
cmake --preset Release -DMOTION_MODEL_HEADER=
cmake --build --preset Release
Pop-Location
.\.venv\Scripts\python.exe -m serial.tools.list_ports -v
~~~

## 从数据到识别

| 步骤 | 输入与输出 |
| --- | --- |
| 录制 | 串口命令保存原始 `CSV` 和批次 `JSON`。三种动作各 10 批，腰部固定、每批 60 秒。正式数据放入 `data/raw`；桌面联调数据放在 `evidence/bringup`。 |
| 训练与转换 | 按批次分为 6/2/2 后切窗；从每个窗口提取 24 维特征，训练 451 参数网络并转为 int8。浮点模型、固件头文件及分组清单随模型版本保存。[训练与转换](ml/README.md) |
| 板上回放 | 同一封存窗口依次运行 PC 浮点、PC 整数、板端推理，逐窗保存类别与数值。DWT 给出网络和整窗周期数，STATUS 返回堆和任务栈水位。[固件与协议](firmware/README.md) |
| 验收 | 检查准确率、各类召回、误差、时延、Flash 和 RAM，另录实物三种动作与切换。软件验证及待测项目列在[验证记录](docs/validation.md)。 |

[题面与交付物](docs/requirements.md) · [阶段进度](docs/roadmap.md) · [推理实现与内存布局](firmware/inference/README.md) · [AI 会话记录](evidence/session.md)
