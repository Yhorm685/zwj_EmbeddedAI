# 软件验证与上板检查点

测试日期为 2026-11-15，平台为 Windows x64。仓库初始化版本为 `e4ac211`，采集与推理接口提交为 `decdc3c`，训练与回放工具提交为 `b52871c`。采集固件和验收工具版本为 `4bbbdd8`，17 项 Python 回归全部通过；下方 HEX 摘要用于核对烧录文件。硬件接线、烧录、静置与佩戴实验均等待现场回传。

| 检查 | 配置与实际结果 |
| --- | --- |
| 独立仓库 | 在指定目录初始化 main，并核对初始化、固件、训练工具阶段各次推送的远端提交号。 |
| 采集固件 | Arm GNU GCC 14.3.1，CMake/Ninja，F407 硬浮点。Debug 与 Release 都产生 ELF、HEX、BIN 和 map。 |
| CubeMX 再生成 | CubeMX 6.18.1、CubeF4 1.28.3。在独立副本重新生成，核查的 179 个文件 SHA-256 一致，覆盖用户区、应用、配置、构建和测试代码。 |
| 采样与 UART 主机检查 | C++ 测试用模拟串口运行固件实际通信代码。逐点 ACK、100 点回放、整数输入、出错与超时、序号/时钟回绕、断点后重新积窗、新增 C 库堆峰值及边界检查通过。 |
| 数据和训练 | 7 项 Python 回归通过。临时构造 30 个带标签的人工批次，走通训练、量化、重复导出及封存评估。验证训练/验证/测试批次隔离、异常切段、时钟复位、量化边界，以及清单与数据版本变化检查。 |
| 算子核验 | 用桌面 GCC 编译实际 CMSIS-NN v7.0.0，检查无模型分支、69 组定点输入及 2 个边界原始窗口。另从人工训练流水线导出模型，用 54 个封存原始窗口对比 C 与 Python，24 个量化输入及 3 个输出逐项一致；直接输入量化特征的 54 组结果也一致。 |
| 带参交叉链接 | 在隔离目录装入人工固定权重，Cortex-M4 Release 含 CMSIS-NN 全连接和向量乘法符号；另用临时训练导出权重核查 DSP 指令。以上均为开发测试，临时权重不随采集固件发布。 |
| 采集和回放工具 | 7 项主机测试通过，覆盖静止单位、序号丢失/回绕、非法输入、未连接端口、全失效采集、封存回放指标和 DWT 停止时拒绝计时。 |
| 报告 | 3 项测试通过，含用 Arm 汇编与链接器构造测试 ELF 后走完整 Markdown 报告入口，验证模型标识、摘要和分区水位。 |

## 采集版链接资源

| 构建 | 优化 | Flash 镜像地址跨度 | 普通 SRAM 链接预留 | CCM 预留 |
| --- | --- | ---: | ---: | ---: |
| Debug | -O0 | 38,828 B | 26,800 B | 0 B |
| Release | -Os | 23,508 B | 26,800 B | 0 B |

Release 各 Flash 装载段的大小之和为 23,500 B，地址对齐空隙占 8 B。SRAM 统计包含链接脚本预留区；实测期间的堆栈水位在正式板端报告中另列。Release 采集版 firmware.hex 的 SHA-256 为 `20D8B5C4453B5757F328C2A6FFCF77BCB8C238E44CDCC757031C104691FD87F3`。

## 复现

从仓库根目录执行。桌面原生 C/C++ 检查使用 GCC/G++、CMake 与 Ninja，ARM 目标使用 Arm GNU Toolchain。人工样本只在临时目录参与训练和检查。

~~~powershell
.\.venv\Scripts\python.exe -m unittest ml.test_pipeline tools.test_host tools.test_report -v
.\.venv\Scripts\python.exe firmware/inference/tests/check.py
Push-Location firmware
cmake --preset Debug
cmake --build --preset Debug
cmake --preset Release -DMOTION_MODEL_HEADER=
cmake --build --preset Release
g++ -std=c++17 -O2 -Wall -Wextra -Werror -Itests/uart/stubs -Iinference tests/uart/test_console.cpp -o build/uart_test.exe
.\build\uart_test.exe
Pop-Location
.\.venv\Scripts\python.exe -m tools.resources firmware/build/Release/firmware.elf
~~~

正式模型冻结后，`firmware/inference/tests/check_export.py` 可对同一份 `model_data.h` 和 `test_windows.npz` 重做主机比对。实际串口回放命令、时延和资源报告生成方式见[上板操作单](bringup.md)。

## 现场验收

本次检查时电脑未枚举到串口，仓库还没有真实运动 CSV。首次现场记录应包含板卡与晶振、固件版本和文件摘要、烧录回显、静止 60 秒 CSV/JSON，以及断线恢复日志。正式测试将记录模型标识、三端逐窗结果、平均/P95/最大时延、运行时 RAM 水位与实时切换视频。当前没有可报告的正式准确率、板端推理时延或 RAM 运行峰值。
