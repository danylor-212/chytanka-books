#pragma once
#include <cstdio>
#define LOG_DBG(tag, ...) (std::fprintf(stderr, "[%s] ", tag), std::fprintf(stderr, __VA_ARGS__), std::fprintf(stderr, "\n"))
#define LOG_ERR(tag, ...) LOG_DBG(tag, __VA_ARGS__)
#define LOG_INF(tag, ...) LOG_DBG(tag, __VA_ARGS__)
