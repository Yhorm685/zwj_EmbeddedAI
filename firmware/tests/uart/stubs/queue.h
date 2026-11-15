#pragma once
using QueueHandle_t = void *;
int xQueueReceive(QueueHandle_t, void*, unsigned);
