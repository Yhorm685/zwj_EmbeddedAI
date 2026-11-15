#pragma once
struct TaskStatus_t { const char *pcTaskName; unsigned usStackHighWaterMark; };
unsigned uxTaskGetSystemState(TaskStatus_t*, unsigned, void*);
