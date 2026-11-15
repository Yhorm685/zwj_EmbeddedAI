# STM32F407ZGT6 采集与推理固件

用 STM32CubeMX 6.18.1 与 STM32CubeF4 1.28.3 生成本目录工程。同组 Framework 的 F407 配置提供 HSE、USART1、SWD、HAL 时基参考，本工程独立配置 I²C1 与两个 RTOS 任务。HAL、CMSIS、FreeRTOS 的许可证随各源码目录保留。

## 构建与烧录

1. 板卡使用 **8 MHz 外部晶振**时，当前 PLL 配置给出 168 MHz 系统时钟。先核对板卡原理图或晶振丝印。使用其他频率时，在 CubeMX 打开 `firmware.ioc`，更改 HSE 与 PLL 并确认时钟树，再 Generate Code。项目使用 ARM GNU Toolchain、CMake 及 Ninja。下面命令的工作目录是 `firmware`。
2. 构建采集固件。编译器、CMake、Ninja 需要在 PATH 上。生成的 ELF、HEX、BIN、链接 map 位于 `build/Release`。Debug 用对应的 Debug preset。

   ```powershell
   cmake --preset Release -DMOTION_MODEL_HEADER=
   cmake --build --preset Release
   ```

3. 接好 SWDIO、SWCLK、GND、VTref，由板卡按其说明独立供电。MPU6050 模块电源接兼容的 3.3 V，地线接板 GND。SCL/SDA 需有到 3.3 V 的外部上拉，已带上拉的模块可直接使用。

   | F407 信号 | 对端 | 用途 |
   | --- | --- | --- |
   | PB6 / PB7 | MPU6050 SCL / SDA | I²C1，400 kHz；AD0 接 GND 或 3.3 V，依次探测地址 0x68、0x69 |
   | PA9 / PA10 | 3.3 V USB–TTL 的 RX / TX | USART1，115200、8N1，无流控；双方共地 |
   | PA13 / PA14 | J-Link SWDIO / SWCLK | 烧录及调试 |

4. 已安装 SEGGER J-Link 的 Windows 电脑可从此目录执行下列命令，路径按安装版本调整。`flash.jlink` 加载 Release HEX，复位后继续运行。

   ```powershell
   & 'C:\Program Files\SEGGER\JLink_V794k\JLink.exe' -device STM32F407ZG -if SWD -speed 1000 -autoconnect 1 -CommanderScript flash.jlink
   ```

5. 上电输出 `#motion-v1,...,model=none` 和连续 `S` 行。串口先打开后复位可以看到启动横幅，随时发送 `STATUS` 加换行可查询当前模型和时钟。先静置录制 60 秒检查序号、设备时间、加速度方向及错误，再按独立批次采集。断开传感器后应看到错误行，恢复连接后会重新探测并积窗。固定实物并拍摄实测记录。以上是预期行为，验收以原始日志和现场记录为依据。

有训练模型后，使用 `cmake --preset Release "-DMOTION_MODEL_HEADER=D:/.../motion_model.h"` 重新配置并构建烧录。以 `INFO` 中的模型标识核对本次导出。采集版本持续输出原始数据，模型未装载时请求回放返回错误。

## 采样、滑窗和运行故障

MPU6050 配置 DLPF=4、SMPLRT_DIV=19、加速度 ±8 g（4096 count/g）、角速度 ±1000 °/s（32.8 count/(°/s)）。采样任务以高于通信任务的优先级读取 INT_STATUS 及同一突发事务中的传感器寄存器，仅转发有 data-ready 标志的样本。时间戳为突发读取完成时的设备 HAL 毫秒计数，保留三轴原始有符号值。寄存器配置逐项回读，I²C 错误、50 ms 无新数据、探测失败均触发重连。[MPU6050 寄存器表](https://invensense.tdk.com/wp-content/uploads/2015/02/MPU-6000-Register-Map.pdf)

深度 32 的静态队列将采样与串口/推理解耦，队列满时保留原序号递增并累计失样。通信任务检查有效状态、uint32 序号连续性和相邻设备时间差（含 10–30 ms）。时钟/序号回绕作模 2³² 的差；遇到断点从当前新有效段重新积满 100 点。完整窗口每 25 点滑动一次。串口 TX 由通信任务独占，RX 中断写单生产者/单消费者环形缓冲。

## 串口协议

每条命令和回复以 LF 结束，固件发送 CRLF。均使用十进制；窗口 ID 取 0–4294967295。S、P 与命令应答可能交错，主机按行类型及请求 ID 匹配。

| 行 | 字段及含义 |
| --- | --- |
| `S,seq,ms,ax,ay,az,gx,gy,gz,status` | 状态为 OK、I2C、NO_DATA 或 OFFLINE。仅 OK 行的六轴字段有效。 |
| `P,id,class,q0,q1,q2,net_cycles,total_cycles` | 实时窗口，id 为最后一点的序号。class 为 0 静止、1 走路、2 跑步。三个 q 是有符号 int8 输出。 |
| `BEGIN,id` → `READY,id` | 开始独立回放，清除先前的未完成窗口。 |
| `D,ax,ay,az,gx,gy,gz` → `ACK,id,index` | 逐点发送六个 int16。index 从 1 到 100，每行等 ACK。 |
| `END,id` → `Q,id,24个值` 与 `R,id,class,q0,q1,q2,net_cycles,total_cycles` | 对刚接收的完整窗口执行板端预处理和网络，返回实际量化输入及分类。 |
| `I,id,24个值` → `J,id,q0,q1,q2,net_cycles` | 直接输入 24 个 int8，用于隔离定点算子误差。 |
| `STATUS` → `INFO,model_id,clock_hz,key=value,...` | 读取固件模型、时钟配置及内存和错误计数。 |

越界、条数/ID 错误及格式错误会返回 `ERR,id,reason` 并终止相关回放。回放连续 4 秒无行、未收完整的一行空闲 1 秒均终止。行过长或 UART 错误后先发送一个空行重新对齐，再以 BEGIN 开始新窗口。

## 板端计时与内存口径

- DWT CYCCNT 使用无符号差值；`net_cycles` 为量化输入就绪至定点网络产生三类输出。`total_cycles` 为完整窗口就绪到类别选出，实时模式含环形窗口有序拷贝，回放模式以已收齐的顺序窗口为起点。采集、串口传输和打印在计时边界之外。记录固件版本、优化级别、时钟、有效样本数和调度条件，计算平均/P95/最大值；运行中允许采样任务和中断抢占。
- 采样与回放工作区位于普通 128 KiB SRAM，另有 64 KiB CCM。用 ELF/map 的实际装载区域计算完整固件 Flash 占用。链接器报告中的 SRAM 包含预留的 C 堆区和 MSP 栈区。网络常量和固件代码位于 Flash。
- `INFO` 的 `heap_min_free` 是 16 KiB FreeRTOS 堆观测到的最小余量。`comm_stack_min_free` 与 `sample_stack_min_free` 是动态分配的通信/采样任务栈历史最小余量（分配 4096 / 2048 字节），这两个任务的栈已计入 RTOS 堆分配。`idle_stack_min_free` 和 `timer_stack_min_free` 是静态空闲/定时器任务栈历史最小余量（512 / 1024 字节）。`msp_min_free` 由启动时从主栈下界填入的标记测得，保留当时活跃栈帧和 64 字节保护，保守估计 1024 字节 MSP 的剩余量；它覆盖标记后的运行期。所有余量的单位均为字节。
- `queue_drops` 指采样入队失败，`rx_errors` 指 UART 硬件/接收环错误，`tx_errors` 指 UART 发送或行构造失败。回放或采集结束时再次请求 STATUS 并保留原文。报告分别列出静态预留、观测到的堆/栈高水位及其测量工况；不同任务各自历史峰值相加只能给出保守上界。
