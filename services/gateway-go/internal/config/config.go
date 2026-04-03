package config

import (
	"os"
	"strconv"
	"time"
)

// Config 汇总网关运行所需的所有配置项。
type Config struct {
	HTTPAddr               string
	AIEngineAddr           string
	DatabaseURL            string
	DatabaseConnectTimeout time.Duration
	AuthAdminUsername      string
	AuthAdminPassword      string
	RedisAddr              string
	RedisPassword          string
	RedisDB                int
	TaskQueueName          string
	TaskMaxAttempts        int
	TaskRetryBackoff       time.Duration
	TaskExecutionTimeout   time.Duration
	ReadTimeout            time.Duration
	WriteTimeout           time.Duration
}

// Load 从环境变量加载配置，并为缺省值提供安全回退。
func Load() Config {
	return Config{
		HTTPAddr:               getenv("SYNAPSE_HTTP_ADDR", ":8080"),
		AIEngineAddr:           getenv("SYNAPSE_AI_ENGINE_ADDR", "127.0.0.1:50051"),
		DatabaseURL:            os.Getenv("SYNAPSE_DATABASE_URL"),
		DatabaseConnectTimeout: getDurationEnv("SYNAPSE_DB_CONNECT_TIMEOUT", 5*time.Second),
		AuthAdminUsername:      getenv("SYNAPSE_AUTH_ADMIN_USERNAME", "admin"),
		AuthAdminPassword:      getenv("SYNAPSE_AUTH_ADMIN_PASSWORD", "123456"),
		RedisAddr:              os.Getenv("SYNAPSE_REDIS_ADDR"),
		RedisPassword:          os.Getenv("SYNAPSE_REDIS_PASSWORD"),
		RedisDB:                getIntEnv("SYNAPSE_REDIS_DB", 0),
		TaskQueueName:          getenv("SYNAPSE_TASK_QUEUE", "synapse:tasks"),
		TaskMaxAttempts:        getIntEnv("SYNAPSE_TASK_MAX_ATTEMPTS", 3),
		TaskRetryBackoff:       getDurationEnv("SYNAPSE_TASK_RETRY_BACKOFF", 2*time.Second),
		TaskExecutionTimeout:   getDurationEnv("SYNAPSE_TASK_EXEC_TIMEOUT", 120*time.Second),
		ReadTimeout:            getDurationEnv("SYNAPSE_HTTP_READ_TIMEOUT", 15*time.Second),
		WriteTimeout:           getDurationEnv("SYNAPSE_HTTP_WRITE_TIMEOUT", 60*time.Second),
	}
}

// getenv 读取字符串环境变量，空值时返回 fallback。
func getenv(key, fallback string) string {
	value := os.Getenv(key)
	if value == "" {
		return fallback
	}
	return value
}

// getDurationEnv 解析时长环境变量，解析失败时使用默认值。
func getDurationEnv(key string, fallback time.Duration) time.Duration {
	value := os.Getenv(key)
	if value == "" {
		return fallback
	}

	parsed, err := time.ParseDuration(value)
	if err != nil {
		return fallback
	}

	return parsed
}

// getIntEnv 解析整数环境变量，解析失败时使用默认值。
func getIntEnv(key string, fallback int) int {
	value := os.Getenv(key)
	if value == "" {
		return fallback
	}

	parsed, err := strconv.Atoi(value)
	if err != nil {
		return fallback
	}

	return parsed
}
