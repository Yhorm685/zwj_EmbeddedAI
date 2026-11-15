# 首次上板与采集

## 1. 核对硬件

本版本面向 **STM32F407ZGT6、8 MHz 外部晶振**。先核对板卡型号与原理图或晶振丝印。如果晶振频率不同，先调整时钟配置并重建固件。记录板卡、晶振、采集时的固定朝向、固件提交以及是否使用外部上拉。

| 开发板 | 对端 |
| --- | --- |
| 3V3、GND | MPU6050 的 3.3 V 兼容电源、GND；SCL/SDA 上拉至 3.3 V |
| PB6、PB7 | 分别接 MPU6050 SCL、SDA；AD0 接地时使用地址 0x68 |
| PA9、PA10、GND | 分别接 3.3 V USB–TTL 的 RX、TX、GND；板卡独立供电，USB–TTL 只接信号和地 |
| PA13、PA14、GND、3V3 | 分别接 J-Link SWDIO、SWCLK、GND、VTref |

## 2. 烧录采集版

Windows PowerShell，从仓库根目录开始。现有电脑已安装本项目的 Python 环境并已构建采集版；新克隆先运行 README 的环境准备和构建命令。J-Link 只在板型、晶振和引脚核对后连接。烧录会替换目标 MCU 内原固件。

~~~powershell
cd D:\kofen\EmbeddedAI
Set-Location firmware
& 'C:\Program Files\SEGGER\JLink_V794k\JLink.exe' -device STM32F407ZG -if SWD -speed 1000 -autoconnect 1 -CommanderScript flash.jlink
Set-Location ..
.\.venv\Scripts\python.exe -m serial.tools.list_ports -v
~~~

找到 USB–TTL 的 COM 号。以下以 COM4 为例，替换为实际端口。关闭占用它的串口助手。先接好线再复位板，执行下面第一条查看状态。屏幕每秒显示一条原始样本，传感器状态变化立即显示；按 Ctrl+C 关闭监视器，再执行录制。下面的版本号对应当前采集版 HEX。如果已经烧录过第一版 decdc3c，该版本也可以录静止基线，记录时填实际烧录的版本号。

~~~powershell
.\.venv\Scripts\python.exe -m tools.capture monitor --port COM4
.\.venv\Scripts\python.exe -m tools.capture record --port COM4 --root evidence/bringup --label still --batch baseline01 --placement bench --orientation '平放，模块 Z 朝上' --firmware-version 4bbbdd8 --seconds 60
.\.venv\Scripts\python.exe -m tools.capture inspect evidence/bringup/still/baseline01.csv
~~~

- 固件启动或 STATUS 应显示 model=none 或 INFO,none，时钟为 168000000；S 行应有递增序号，状态为 OK。设备时间相邻约 20 ms，静止时加速度模长约 1 g。检查平均三轴分量是否与实际摆放方向一致。
- 保存 60 秒 CSV 与同名 JSON，检查实际行数、状态计数、丢号、重复/复位、周期分布和有效时长。若读数持续为零、状态异常或间隔突跳，保留原记录并先排查；重复测试换批次号。
- 单独做断线/恢复检查。保持双方供电和共地，断开再接回一根 I²C 信号线，记录状态变化与恢复到连续 OK。可用单独的记录命令，改用 --batch reconnect01；该实验不进入训练集。
- 无 COM 口先检查 USB–TTL、驱动和数据线。无串口输出核对 TX/RX 交叉、共地、8 MHz 晶振及 115200/8N1。持续 OFFLINE 时核对模块供电、SCL/SDA 和上拉。板端固件会自动重新探测。

**首轮需要回传**：烧录成功或失败的控制台输出、晶振频率、实际摆放方向，以及 evidence/bringup/still/ 下的 CSV 和 JSON。这是开始佩戴采集前的检查点。

## 3. 正式采集

腰部固定传感器，记录朝向，电脑放在背包中记录。每次单独启动一批，保持动作约一分钟。静止、走路、跑步各 10 批，批次名例如 still01…still10、walk01…walk10、run01…run10。同一长录制拆段会高估泛化效果。下面以一次走路为例，朝向文字替换为实际观察。

~~~powershell
.\.venv\Scripts\python.exe -m tools.capture record --port COM4 --label walk --batch walk01 --seconds 60 --orientation '腰部固定，记录实际 X/Y/Z 朝向' --firmware-version 4bbbdd8
~~~

保留 data/raw 下各批 CSV、JSON。JSON 默认剔除首尾各 2 秒，若中途停下、改变动作或调整传感器，按实际时刻在同名 JSON 的 valid_segments_ms 中标出保留区间。断点、异常状态和未积满的窗口由软件处理。30 批实采齐备后，按[训练与转换说明](../ml/README.md)生成并冻结首版模型。

## 4. 板端验收和视频

1. 从仓库根目录运行训练说明中的数据审计、训练与封存测试命令。保存 models/v1 和 evidence/pc-v1。使用同一 model_data.h 构建并烧录正式版。
2. 用 STATUS 核实 model_id 与评估结果一致。回放工具将每个封存窗口分别按原始六轴和 PC 量化输入送入板端，逐窗落盘；若中途失败，保留已有 windows.csv，用新的输出目录重新执行。
3. 在已记录出厂时钟、编译优化与固件版本的条件下，保留测试开始及结束时的 INFO、水位和错误计数。记录静止、走路、跑步及切换画面，同时拍摄或录屏串口预测、时延和模型版本。将实际视频及测量结果连同原始文件保存至 evidence。

~~~powershell
cmake -S firmware -B firmware/build/Release "-DMOTION_MODEL_HEADER=$((Resolve-Path models/v1/model_data.h).Path)"
cmake --build firmware/build/Release
Push-Location firmware
& 'C:\Program Files\SEGGER\JLink_V794k\JLink.exe' -device STM32F407ZG -if SWD -speed 1000 -autoconnect 1 -CommanderScript flash.jlink
Pop-Location
.\.venv\Scripts\python.exe -m tools.replay --port COM4 --windows evidence/pc-v1/test_windows.npz --out evidence/board/replay-01 --firmware-version "$(git rev-parse --short HEAD) Release" --elf firmware/build/Release/firmware.elf
.\.venv\Scripts\python.exe -m tools.resources firmware/build/Release/firmware.elf
.\.venv\Scripts\python.exe -m tools.report --summary evidence/board/replay-01/summary.json --elf firmware/build/Release/firmware.elf --out evidence/board/replay-01/report.md
~~~

summary.json 中包括窗口数、三个混淆矩阵、各类召回率、logits 平均与最大绝对误差、分类分歧率、板端相对浮点准确率差，以及网络与整窗耗时的平均/P95/最大值。资源报告同时保留 ELF/map 的静态统计和板上堆/栈水位，按[固件说明](../firmware/README.md)解释峰值。
