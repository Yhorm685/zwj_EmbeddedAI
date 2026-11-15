# 窗口预处理与定点推理

采集固件在 `MOTION_MODEL_HEADER` 为空时报告模型 ID `none`，推理接口返回不可用。训练与转换生成模型头后，在 `firmware` 目录配置单独的构建目录。例如模型导出在仓库的 `models/v1` 时可执行：

```powershell
$modelHeader = (Resolve-Path ../models/v1/model_data.h).Path
cmake --preset Release -B build/recognition "-DMOTION_MODEL_HEADER=$modelHeader"
cmake --build build/recognition
```

原始窗口为行优先的 100×6 个 int16，通道固定为 ax、ay、az、gx、gy、gz。将加速度除以 4096.0 得到 g，角速度除以 32.8 得到 °/s。每轴以 float32 顺序累计和及平方和，依次输出均值、总体标准差、均方根、峰峰值。浮点编译关闭 FMA 合并与 fast-math。用导出参数标准化并除以输入尺度，按最近偶数舍入后裁剪到 int8。

两层全连接分别使用 16×24、3×16 行优先权重，零点均为 0，偏置为 int32。两次调用 `arm_fully_connected_s8`，采用 CMSIS-NN 默认双舍入。第一层输出限制到 [0,127] 以实现 ReLU，第二层输出限制到 [-128,127]。输出顺序为 still、walk、run，各分数乘模型输出尺度即可与浮点 logits 比较。模型 ID 随导出参数固化在 Flash。

权重占 432 字节、偏置 76 字节。Flash 另含标准化、量化常量及代码。原始窗口由调用者持有，占 1200 字节；量化输入和输出共 27 字节。隐藏层 16 字节和一轴的四项统计量属于固定大小的函数局部量，随调用任务栈计入占用。程序核对目标架构的 CMSIS-NN 缓冲区查询值，F407 DSP 版本需要 0 字节额外 scratch。推理过程中无堆分配，可独立处理两份调用者提供的窗口。

板上 `network_cycles` 由 DWT CYCCNT 包围两次全连接调用，含中断抢占及算子返回值检查；每次使用无符号差值得到周期数。完整窗口耗时由调用方另行计时。峰值 RAM 要结合最终模型固件的 ELF/map、各任务栈和堆运行记录计算。

## 桌面一致性检查

从仓库根目录，使用系统 GCC、CMake、Ninja 和 Python 运行。第一条命令会在临时目录创建人工权重，检查无模型模式、两次全连接、边界舍入、饱和和原始窗口布局。第二条命令读取训练与评估流程实际产生的输出，核对每一个窗口的 24 个输入及 3 个输出，并单独比较整数算子。

```powershell
python firmware/inference/tests/check.py
python firmware/inference/tests/check_export.py --header models/v1/model_data.h --windows evidence/pc/test_windows.npz
```

桌面可执行文件使用 CMSIS-NN 标量路径，F407 使用由 `-mcpu=cortex-m4` 启用的 DSP 路径。最终板端回放需记录相同模型 ID 和相同窗口的实际输出。
